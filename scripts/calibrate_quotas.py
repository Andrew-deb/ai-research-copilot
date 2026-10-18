#!/usr/bin/env python
"""
Quota calibration — turn measurements into limits.

Read-only. It reads the model's live catalogue entry, every configured
credential's allowance, and `ai_operations`, then prints the numbers. It changes
no configuration: the limits it recommends go into `render.yaml` by hand, so a
deliberate decision is recorded in a diff rather than applied by a script nobody
re-reads.

    python scripts/calibrate_quotas.py


What the ceiling is actually for
--------------------------------
This script used to compute "subtract a reserve from a free allowance, then
divide by provider requests per question". That was the right shape for a
50-request-per-day free tier, and it is the wrong shape now, because the premise
was wrong in a way nothing here could see.

`free_model_daily_requests` meters ONLY `:free` model variants. The configured
model is a paid one, so that counter reads 0/50 on every credential while real
money is being spent — and every ceiling derived from it was derived from a meter
the application never touches. The arithmetic was sound and the input was
fiction.

Priced properly, the configured model costs hundredths of a cent per question:
roughly fourteen thousand questions per dollar. A budget divided by that is a
number in the thousands, which protects nothing, because money was never the
binding constraint.

So the ceiling is not a cost control. It is ABUSE protection. The risk is a
crawler finding the demo and draining it overnight, so that it fails when
somebody who matters opens the link — `authentication_and_demo_design.md` §6d,
"the difference between a demo that works when a recruiter opens it and one that
has been drained by a crawler". That is what RESERVE_FRACTION and the
per-visitor limits defend, and it is bounded by what a plausible burst of abuse
costs rather than by what the account can afford.

Money is still worth bounding, but as a backstop rather than as the sizing
input: see GLOBAL_SPEND_USD_PER_DAY, which stops a runaway in the one unit that
cannot be gamed by a cheaper model or a longer answer.

And the unit of a request ceiling is the PROVIDER REQUEST, not the question. One
agent question is several OpenRouter calls, one per reasoning turn. Sizing as
though they were the same thing overstates capacity by the mean turn count.

Run it again whenever the model, the tool budget, or the provider changes. All
three move, and the model has now moved twice without anyone choosing to.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Both roots: `dashboard` for the repositories, and the project root because
# lakebase reaches into `mcp_server.shared_resource` for the note mutations the
# MCP server and the dashboard share.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

OPENROUTER = "https://openrouter.ai/api/v1"

# Held back so the demo answers when somebody you care about opens the link.
RESERVE_FRACTION = 0.35

# How the distributable requests are divided between the two features that draw
# on the same pool. Agent questions are the product's point; cited answers are
# cheaper per use and recover faster.
AGENT_SHARE = 0.75


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------

def _get(url: str, credential: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {credential}"} if credential else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def catalogue_entry(model: str) -> dict:
    """
    The model's live listing, or a hard stop.

    FIRST, and fatal, because this is the check that was missing. Twice a pinned
    model slug has been retired by OpenRouter and started returning 404 on every
    single call - `openai/gpt-oss-120b:free`, then
    `inclusionai/ling-3.0-flash-vl:free` - and both times the application kept
    the dead name until a human noticed every AI feature was broken. Nothing in
    the repository changes when a provider withdraws a variant, so the only way
    to find out is to ask.
    """
    models = _get(f"{OPENROUTER}/models")["data"]
    for entry in models:
        if entry["id"] == model:
            return entry

    stem = model.split(":")[0]
    alternatives = sorted(e["id"] for e in models if e["id"].startswith(stem))
    print(f"\n  FATAL: {model} is not in OpenRouter's catalogue.\n")
    print("  Every completion call is returning 404. This is not a calibration")
    print("  problem - the configured model does not exist.\n")
    if alternatives:
        print("  Closest live slugs:")
        for name in alternatives:
            print(f"    {name}")
        print("\n  Set OPENROUTER_MODEL in render.yaml and dashboard/config.py.")
    sys.exit(1)


def pricing(entry: dict) -> tuple[float, float]:
    price = entry.get("pricing") or {}
    return float(price.get("prompt") or 0), float(price.get("completion") or 0)


def providers(model: str) -> list[dict]:
    """
    Who actually serves this model.

    Reported next to the cost because it is the other half of whether a model is
    usable, and the half that is easy to miss. A model with ONE provider is a
    single point of failure that no number of credentials fixes: measured,
    `inclusionai/ling-3.0-flash-vl` returned `engine_overloaded` from its sole
    provider's shared pool for over twenty minutes, on all three credentials at
    once, because they are three ways of reaching the same provider.
    """
    try:
        data = _get(f"{OPENROUTER}/models/{model}/endpoints")["data"]
        return data.get("endpoints") or []
    except (urllib.error.URLError, KeyError, TimeoutError):
        return []


def allowances() -> list[dict]:
    """
    Every configured credential, deduplicated, with what each reports.

    All of them, because reading only OPENROUTER_API_KEY reported a third of the
    picture once failover existed - and because that name turned out to hold the
    same value as OPENROUTER_API_KEY_3, so a naive count said four when there
    were three.
    """
    from config import OPENROUTER_API_KEYS

    found = []
    for index, credential in enumerate(OPENROUTER_API_KEYS, start=1):
        row: dict = {"index": index, "tail": credential[-4:]}
        try:
            data = _get(f"{OPENROUTER}/key", credential)["data"]
        except urllib.error.HTTPError as exc:
            row["error"] = f"HTTP {exc.code}"
            found.append(row)
            continue
        except Exception as exc:  # noqa: BLE001 - a diagnostic, never fatal
            row["error"] = type(exc).__name__
            found.append(row)
            continue
        free = data.get("free_model_daily_requests") or {}
        row.update(spent=data.get("usage"), cap=data.get("limit"),
                   is_free_tier=data.get("is_free_tier"),
                   free_used=free.get("used"), free_limit=free.get("limit"))
        found.append(row)
    return found


# ---------------------------------------------------------------------------
# The traffic
# ---------------------------------------------------------------------------

def measurements() -> list[dict]:
    """Per metric: how often, how many provider requests, how expensive, how slow."""
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
               round(sum(estimated_cost_usd)::numeric, 4)                 AS total_cost,
               round(avg(latency_ms))                                     AS mean_ms,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_ms
          FROM ai_operations
         WHERE COALESCE(llm_turns, 0) > 0
         GROUP BY metric
         ORDER BY operations DESC;
    """)


def models_measured() -> list[dict]:
    """
    Which models produced the rows being averaged.

    Because the measurements outlive the model. `ai_operations` records which
    model ANSWERED each operation, and the configured model has changed twice -
    so a mean cost computed across the table can be the price of a model nobody
    runs any more. Reporting one model's cost as though it were another's is the
    same class of mistake as calibrating against the wrong allowance, and it is
    just as invisible once it is a single number in a table.
    """
    from repositories.lakebase import run_query

    return run_query("""
        SELECT COALESCE(model, '(unrecorded)')      AS model,
               count(*)                             AS operations,
               round(avg(estimated_cost_usd)::numeric, 6) AS mean_cost,
               max(occurred_at)                     AS last_seen
          FROM ai_operations
         WHERE COALESCE(llm_turns, 0) > 0
         GROUP BY model
         ORDER BY operations DESC;
    """)


def ceiling(distributable: float, requests_per_operation: float) -> int:
    """
    Operations per day a request budget supports.

    Floored, never rounded up: a ceiling that rounds up is a ceiling that can be
    exceeded, which is the one thing it exists not to be.
    """
    if not requests_per_operation:
        return int(distributable)
    return int(distributable // requests_per_operation)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def main() -> None:
    import config

    model = config.OPENROUTER_MODEL
    print("=" * 78)
    print("QUOTA CALIBRATION")
    print("=" * 78)
    print(f"\nMODEL  {model}")

    entry = catalogue_entry(model)          # exits if the slug is dead
    prompt_price, completion_price = pricing(entry)
    is_free = prompt_price == 0 and completion_price == 0

    print(f"  live in the catalogue      : yes")
    print(f"  price per million tokens   : ${prompt_price * 1e6:.4f} prompt, "
          f"${completion_price * 1e6:.4f} completion")
    print(f"  metered by                 : "
          + ("free_model_daily_requests (a :free variant)" if is_free
             else "CREDIT SPEND - the free-request counter does not apply"))

    serving = providers(model)
    print(f"  providers serving it       : {len(serving) or 'unknown'}"
          + ("".join(f"\n      {ep.get('provider_name')} "
                     f"ctx={ep.get('context_length')}" for ep in serving)))
    if len(serving) == 1:
        print("    WARNING: one provider is a single point of failure. When its")
        print("    shared pool is busy there is nowhere to route, and extra")
        print("    credentials do not help - they reach the same provider.")

    print("\nCREDENTIALS (all of them, deduplicated)")
    rows = allowances()
    if not rows:
        print("  none configured. Set OPENROUTER_API_KEY.")
        return
    for row in rows:
        if row.get("error"):
            print(f"  {row['index']}. ...{row['tail']}  unreadable: {row['error']}")
            continue
        print(f"  {row['index']}. ...{row['tail']}  spent=${row['spent']} "
              f"cap={row['cap']} free_requests={row['free_used']}/{row['free_limit']}")
    if not is_free:
        print("\n  The free_requests column is shown for completeness and is NOT")
        print("  the constraint: a paid model never moves that counter. Sizing a")
        print("  ceiling from it is how these limits came to be wrong.")

    print("\nMEASURED, per operation that reached a model")
    measured = {row["metric"]: row for row in measurements()}
    if not measured:
        print("  No operations recorded yet. Exercise the features first.")
        return
    print(f"  {'metric':16}{'n':>4}{'reqs':>6}{'mean':>7}{'p95':>5}{'max':>5}"
          f"{'mean $':>11}{'p95 $':>11}{'total $':>10}{'mean s':>8}")
    for metric, row in measured.items():
        print(f"  {metric:16}{row['operations']:>4}{row['provider_requests']:>6}"
              f"{row['mean_turns']:>7}{row['p95_turns']:>5}{row['max_turns']:>5}"
              f"{float(row['mean_cost'] or 0):>11.6f}{float(row['p95_cost'] or 0):>11.6f}"
              f"{float(row['total_cost'] or 0):>10.4f}"
              f"{(row['mean_ms'] or 0) / 1000:>8.1f}")

    agent = measured.get("agent_query")
    rag = measured.get("rag_query")

    # Whose numbers are these, actually?
    by_model = models_measured()
    stale = [row for row in by_model if row["model"] not in (model, "(unrecorded)")]
    if stale:
        print()
        print("  MEASURED UNDER A DIFFERENT MODEL")
        for row in by_model:
            marker = "  <- configured" if row["model"] == model else ""
            print(f"    {row['model']:34} {row['operations']:>4} ops  "
                  f"mean ${float(row['mean_cost'] or 0):.6f}{marker}")
        configured_rows = sum(r["operations"] for r in by_model if r["model"] == model)
        total_rows = sum(r["operations"] for r in by_model)
        print()
        print(f"    {configured_rows} of {total_rows} priced operations ran on "
              f"the configured model.")
        print("    The costs above are an average across all of them, so treat the")
        print("    money as indicative until the new model has its own history.")
        print("    Turn counts travel better than prices: they are a property of")
        print("    the task and the tool budget, not of what a token costs.")

    # -- What a day of abuse would cost ------------------------------------
    #
    # The number the ceiling is actually chosen against, now that cost is not
    # the binding constraint. Expressed as "what does the ceiling let a bad
    # actor spend", which is the question a reserve answers.
    print("\nWHAT THE CEILING IS PROTECTING")
    if agent and agent["mean_cost"]:
        per_question = float(agent["mean_cost"])
        print(f"  measured cost per agent question : ${per_question:.6f}")
        if per_question:
            print(f"  questions per dollar             : {1 / per_question:,.0f}")
        print(f"  a 1000-question crawl would cost   : ${per_question * 1000:.2f}")
        print("\n  Cost is not the binding constraint at these prices. The ceiling")
        print("  exists so the demo still answers when somebody who matters opens")
        print("  the link, and GLOBAL_SPEND_USD_PER_DAY is the backstop for the")
        print("  case where a cheaper model or a longer answer moves these numbers.")
    else:
        print("  No priced agent operations yet.")

    # -- Request ceilings, for a genuinely free model ----------------------
    print("\nREQUEST CEILINGS")
    free_limit = next((r.get("free_limit") for r in rows if r.get("free_limit")), None)
    if is_free and free_limit:
        # Credentials are account-scoped, so allowances add up across accounts.
        usable = sum(r.get("free_limit") or 0 for r in rows if not r.get("error"))
        reserve = round(usable * RESERVE_FRACTION)
        distributable = usable - reserve
        print(f"  allowance across {len(rows)} credential(s) : {usable} requests/day")
        print(f"  reserve ({RESERVE_FRACTION:.0%}, kept back)        : {reserve}")
        print(f"  distributable                     : {distributable}")

        agent_budget = round(distributable * AGENT_SHARE)
        rag_budget = distributable - agent_budget
        if agent:
            p95 = float(agent["p95_turns"] or 1)
            mean = float(agent["mean_turns"] or 1)
            print(f"\n  GLOBAL_AGENT_PER_DAY   budget {agent_budget} requests")
            print(f"    by p95  ({p95} turns) -> {ceiling(agent_budget, p95):>4}"
                  f"   survives a run of hard questions")
            print(f"    by mean ({mean} turns) -> {ceiling(agent_budget, mean):>4}"
                  f"   typical questions only")
        rag_turns = float(rag["p95_turns"]) if rag else 1.0
        print(f"\n  GLOBAL_RAG_PER_DAY     budget {rag_budget} requests")
        print(f"    at {rag_turns} turn(s) -> {ceiling(rag_budget, rag_turns):>4}"
              f"{'' if rag else '   (ASSUMED: no rag_query has ever run)'}")
    else:
        print("  Not bounded by a free-request allowance - this is a paid model.")
        print("  Choose the request ceilings from the abuse budget above, and let")
        print("  GLOBAL_SPEND_USD_PER_DAY bound the money. A request ceiling")
        print("  derived from credit is a number in the thousands and protects")
        print("  nothing.")
        if agent:
            print(f"\n  For reference, one agent question costs "
                  f"{agent['mean_turns']} provider requests on average "
                  f"(p95 {agent['p95_turns']}, max {agent['max_turns']}).")

    print("\n  GLOBAL_SEARCH_PER_DAY")
    print("    not bounded by OpenRouter - searches embed via Hugging Face and")
    print("    make no completion call. Bounded by the HF token's limit instead.")

    print("\n" + "=" * 78)
    print("These are inputs to a decision, not the decision. Put the chosen")
    print("numbers in render.yaml by hand so the reasoning lands in a diff.")
    print("=" * 78)


if __name__ == "__main__":
    main()
