#!/usr/bin/env python
"""
Phase 3.6 quota calibration — turn measurements into limits.

Read-only. It reads `ai_operations`, reads the provider's current allowance from
the provider, and prints the numbers. It changes no configuration: the limits it
recommends go into `render.yaml` by hand, so that a deliberate decision is
recorded in a diff rather than applied by a script nobody re-reads.

Run it again whenever the model, the agent's tool budget, or the provider's free
tier changes. All three move.

    python scripts/calibrate_quotas.py


Why this is not a cost calculation
----------------------------------
`authentication_and_demo_design.md` §6d is explicit, and it is easy to read past:
with no budget, exceeding a free allowance does not cost money, it ends the
feature until the allowance resets. The ceiling protects AVAILABILITY. So the
sum is not "divide a budget by unit cost" — it is "subtract a reserve from an
allowance, then divide by operations per request".

And the unit that matters is the PROVIDER REQUEST, not the user's question. One
agent question is several OpenRouter calls, one per reasoning turn. Sizing a
ceiling as though they were the same thing overstates capacity by whatever the
mean turn count happens to be, which is the error this script exists to prevent.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

# Both roots: `dashboard` for the repositories, and the project root because
# lakebase reaches into `mcp_server.shared_resource` for the note mutations the
# MCP server and the dashboard share.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

# Held back so the demo answers when somebody you care about opens the link.
# §6d: "the difference between a demo that works when a recruiter opens it and
# one that has been drained by a crawler."
RESERVE_FRACTION = 0.35

# How the distributable requests are divided between the two features that draw
# on the same pool. Agent questions are the product's point; cited answers are
# cheaper per use and recover faster.
AGENT_SHARE = 0.75


def openrouter_allowance() -> dict:
    """
    The account's CURRENT free-model limit, read from OpenRouter.

    §6e: read the provider rather than trust a number in a document. These
    change, and a stale figure is worse than no figure.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return {"limit": None, "note": "OPENROUTER_API_KEY not set"}

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)["data"]

    free = data.get("free_model_daily_requests") or {}
    return {"limit": free.get("limit"),
            "used": free.get("used"),
            "is_free_tier": data.get("is_free_tier")}


def measurements() -> list[dict]:
    """Per metric: how often, how expensive, how slow, and how many turns."""
    from repositories.lakebase import run_query

    return run_query("""
        SELECT metric,
               count(*)                                                   AS operations,
               COALESCE(sum(llm_turns), 0)                                AS provider_requests,
               round(avg(llm_turns), 2)                                   AS mean_turns,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY llm_turns)    AS p95_turns,
               max(llm_turns)                                             AS max_turns,
               round(avg(estimated_cost_usd)::numeric, 6)                 AS mean_cost,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY estimated_cost_usd)
                                                                          AS p95_cost,
               round(avg(latency_ms))                                     AS mean_ms,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_ms,
               round(avg(tool_calls), 2)                                  AS mean_tools
          FROM ai_operations
         WHERE COALESCE(llm_turns, 0) > 0
         GROUP BY metric
         ORDER BY operations DESC;
    """)


def ceiling(distributable: int, requests_per_operation: float) -> int:
    """
    Operations per day a request budget supports.

    Floored, never rounded up: a ceiling that rounds up is a ceiling that can be
    exceeded, which is the one thing it exists not to be.
    """
    if not requests_per_operation:
        return distributable
    return int(distributable // requests_per_operation)


def main() -> None:
    allowance = openrouter_allowance()
    rows = {row["metric"]: row for row in measurements()}

    print("=" * 74)
    print("QUOTA CALIBRATION")
    print("=" * 74)

    print("\nPROVIDER ALLOWANCE (read from OpenRouter, not from a document)")
    limit = allowance.get("limit")
    if limit is None:
        print(f"  unavailable: {allowance.get('note')}")
        print("  Cannot recommend ceilings without it.")
        return
    print(f"  free model requests per day : {limit}")
    print(f"  used at the time of reading : {allowance.get('used')}")

    print("\nMEASURED, per operation that reached a model")
    if not rows:
        print("  No operations recorded yet. Run the §6b sample first.")
        return
    header = f"  {'metric':16}{'n':>4}{'reqs':>6}{'mean':>7}{'p95':>5}{'max':>5}" \
             f"{'mean $':>10}{'p95 $':>10}{'mean s':>8}{'p95 s':>7}"
    print(header)
    for metric, row in rows.items():
        print(f"  {metric:16}{row['operations']:>4}{row['provider_requests']:>6}"
              f"{row['mean_turns']:>7}{row['p95_turns']:>5}{row['max_turns']:>5}"
              f"{float(row['mean_cost'] or 0):>10.6f}{float(row['p95_cost'] or 0):>10.6f}"
              f"{(row['mean_ms'] or 0) / 1000:>8.1f}{(row['p95_ms'] or 0) / 1000:>7.1f}")

    reserve = round(limit * RESERVE_FRACTION)
    distributable = limit - reserve
    print(f"\nBUDGET")
    print(f"  allowance                   : {limit} requests/day")
    print(f"  reserve ({RESERVE_FRACTION:.0%}, kept for you) : {reserve}")
    print(f"  distributable               : {distributable}")

    agent_budget = round(distributable * AGENT_SHARE)
    rag_budget = distributable - agent_budget

    agent = rows.get("agent_query")
    print("\nRECOMMENDED GLOBAL CEILINGS")
    print("  Agent and RAG draw on ONE pool, so their ceilings are shares of it")
    print("  rather than independent numbers.\n")

    if agent:
        p95 = float(agent["p95_turns"] or 1)
        mean = float(agent["mean_turns"] or 1)
        print(f"  GLOBAL_AGENT_PER_DAY")
        print(f"    budget {agent_budget} requests")
        print(f"    by p95 ({p95} turns)  -> {ceiling(agent_budget, p95):>3}  "
              f"safe: survives a run of hard questions")
        print(f"    by mean ({mean} turns) -> {ceiling(agent_budget, mean):>3}  "
              f"optimistic: typical questions only")

    # RAG is one synthesis call. Stated rather than measured, and flagged as
    # such: nothing has exercised it yet, so there is no distribution to take a
    # p95 from, and a measured number would be better.
    rag = rows.get("rag_query")
    rag_turns = float(rag["p95_turns"]) if rag else 1.0
    print(f"\n  GLOBAL_RAG_PER_DAY")
    print(f"    budget {rag_budget} requests")
    print(f"    at {rag_turns} turn(s) -> {ceiling(rag_budget, rag_turns):>3}"
          f"{'' if rag else '   (ASSUMED: no rag_query has ever run)'}")

    print("\n  GLOBAL_SEARCH_PER_DAY")
    print("    not bounded by OpenRouter — searches embed via Hugging Face and")
    print("    make no completion call. Bounded by the HF token's limit instead.")

    print("\n" + "=" * 74)
    print("These are inputs to a decision, not the decision. Put the chosen")
    print("numbers in render.yaml by hand so the reasoning lands in a diff.")
    print("=" * 74)


if __name__ == "__main__":
    main()
