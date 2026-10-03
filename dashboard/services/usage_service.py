"""
dashboard/services/usage_service.py — what this account has actually done.

Built on `ai_operations`, which the telemetry work has been writing since Phase
3.6 and which nothing has read until now.

Two things it is careful about.

**It shows cost, and says plainly that nobody is being billed.** Reversed on
1 October 2026, deliberately: the number tells somebody what the free tier is
actually worth, and it is the number a self-hoster most needs when the
deployment is their own. The risk it carries is being read as an invoice, so the
page never calls it one — it is what the requests cost to run, and the words
beside it say so.

Where a provider returns no cost, the rows that could not be priced are counted
and reported. A total that quietly omits them is a wrong number rather than an
incomplete one.

**It says when it does not know.** A window with no operations in it returns
empty rather than a page of zeros, because "you have run nothing in 30 days"
and "we are not recording this" look identical as a row of noughts and are
very different facts.
"""

from __future__ import annotations

import logging

from repositories import usage_analytics
from services import quota_service
from services.settings_service import USAGE_LABELS

logger = logging.getLogger(__name__)

# Offered as choices on the page. Short enough to be about this week's work,
# long enough to show a habit.
WINDOWS = (7, 30)
DEFAULT_WINDOW = 7


# What the page calls a feature. The agent is two of them: the research agent
# and Wick spend the same allowance and do very different work, which is the
# split somebody most wants when they look at what their usage cost.
FEATURE_LABELS = {
    "research": "Research agent",
    "wick": "Wick",
    "ask": "Ask in search",
    "search": "Searches",
}


# What an ALLOWANCE is called, which is not what a feature is called.
#
# `agent_query` is one allowance covering two features: the research agent and
# Wick share it, deliberately — splitting the metric would have given every
# account two separate allowances. So the metered row cannot borrow a feature
# label. It did, and reported four Wick questions as "Research agent".
ALLOWANCE_LABELS = {
    "agent_query": "Agent questions",
    "rag_query": "Cited answers",
    "semantic_search": "Searches",
}

# Allowances that cover more than one feature, and therefore want a breakdown
# under the bar: the bar answers "how much is left", which is shared, and the
# line under it answers "on what", which is not.
SHARED_ALLOWANCES = {"agent_query"}


def _feature_of(metric: str, mode: str | None) -> str:
    if metric == "agent_query":
        return "wick" if mode == "wick" else "research"
    if metric == "rag_query":
        return "ask"
    if metric == "semantic_search":
        return "search"
    return metric


def _label(metric: str, mode: str | None = None) -> str:
    feature = _feature_of(metric, mode)
    return (FEATURE_LABELS.get(feature)
            or USAGE_LABELS.get(metric)
            or metric.replace("_", " ").title())


def today(user_id: str, tier: str) -> dict:
    """
    What this account has done since midnight.

    Two sources, because the tab answers two different questions depending on
    how the deployment is configured, and each source is authoritative for one
    of them:

      metering ON   "how much of my allowance is left" — `usage_counters`, the
                    same counter that refuses the next request. Anything else
                    could tell somebody they had room at the moment they were
                    turned away.
      metering OFF  "what have I done today" — `ai_operations`, which is written
                    whether or not limits are enforced.

    The tab used to show nothing at all with metering off, on the reasoning that
    there was no allowance to report. That was true and unhelpful: the activity
    had happened, the record of it was sitting in `ai_operations`, and the page
    answered with a sentence about deployment configuration.
    """
    metered = quota_service.usage_summary(tier, "user", user_id)

    if metered:
        split = _today_by_feature(user_id)
        rows = []
        for metric, counts in metered.items():
            limit = counts.get("limit") or 0
            used = counts.get("used") or 0
            rows.append({
                "metric": metric,
                # The allowance's name, not a feature's. See ALLOWANCE_LABELS.
                "label": ALLOWANCE_LABELS.get(metric, _label(metric)),
                "used": used,
                "limit": limit,
                "left": max(limit - used, 0),
                # Clamped: a limit lowered after somebody has already spent more
                # than it should not render a bar past its own end.
                "percent": min(round(used / limit * 100), 100) if limit else 0,
            })

        for row in rows:
            row["breakdown"] = (_breakdown(row, split)
                                if row["metric"] in SHARED_ALLOWANCES else [])

        return {"metered": True,
                "rows": sorted(rows, key=lambda r: r["label"])}

    try:
        counted = usage_analytics.today(user_id)
    except Exception as exc:  # noqa: BLE001 - a usage tab is not worth a 500
        logger.warning("Today's usage unavailable: %s", exc)
        return {"metered": False, "rows": [], "unavailable": True}

    busiest = max((row["operations"] or 0) for row in counted) if counted else 0

    rows = [{
        "metric": row["metric"],
        "feature": _feature_of(row["metric"], row.get("mode")),
        "label": _label(row["metric"], row.get("mode")),
        "used": row["operations"] or 0,
        # No limit, so no "of N". The bar is RELATIVE — each feature against the
        # busiest one today — which compares rather than measures. A bar against
        # an invented ceiling would measure against nothing.
        "limit": None,
        "left": None,
        "percent": (round((row["operations"] or 0) / busiest * 100)
                    if busiest else 0),
        "breakdown": [],
    } for row in counted]

    return {"metered": False, "rows": rows}


def _today_by_feature(user_id: str) -> list[dict]:
    """
    Today's operations per feature, or nothing if they cannot be read.

    Swallowed rather than raised: this feeds the breakdown line under a bar, and
    the bar itself comes from a different table. Losing the detail is a worse
    page; losing the allowance is a broken one.
    """
    try:
        return usage_analytics.today(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Today's feature split unavailable: %s", exc)
        return []


def _breakdown(row: dict, split: list[dict]) -> list[dict]:
    """
    What a shared allowance was spent on — as segments of its own bar.

    One allowance, so one bar. Giving Wick a bar of its own would draw a second
    quota that does not exist; dividing the one bar shows the same information
    without inventing an allowance.

    Each segment's width is its SHARE of the recorded activity, applied to the
    bar's fill — not its raw count. The two numbers come from different tables
    and can legitimately differ: a request refused by the quota increments
    `usage_counters` and never reaches `ai_operations`, and toggling metering
    off does the reverse. Scaling by share keeps the segments summing to the
    bar whatever the totals do, so the picture never contradicts the number
    printed beside it.

    The counts in the legend stay as recorded, because those are a fact. Only
    the geometry is scaled.

    Returns nothing when there is nothing to tell apart — one feature, and that
    feature the one a reader would already assume. "8 Research agent" under a
    bar labelled Agent questions is the same sentence twice. "8 Wick" is not.
    """
    parts = sorted(
        ({"feature": _feature_of(item["metric"], item.get("mode")),
          "label": _label(item["metric"], item.get("mode")),
          "used": item["operations"] or 0}
         for item in split
         if item["metric"] == row["metric"] and (item["operations"] or 0)),
        key=lambda part: -part["used"],
    )

    if len(parts) == 1 and parts[0]["feature"] == "research":
        return []

    recorded = sum(part["used"] for part in parts)
    for part in parts:
        part["percent"] = (round(part["used"] / recorded * row["percent"], 2)
                           if recorded else 0)
    return parts


def analytics(user_id: str, days: int = DEFAULT_WINDOW,
              feature: str | None = None) -> dict:
    """
    The Analytics tab, in one call.

    Returns `{"days", "has_data", "totals", "features", "daily"}`. `has_data`
    rather than letting the template infer it from an empty list: the difference
    between "nothing yet" and "nothing recorded" is worth stating in words, and
    the template should not be the place that decides which it is.
    """
    days = days if days in WINDOWS else DEFAULT_WINDOW
    feature = feature if feature in usage_analytics.FEATURES else None

    try:
        rows = usage_analytics.by_metric(user_id, days, feature)
        daily = usage_analytics.by_day(user_id, days, feature)
    except Exception as exc:  # noqa: BLE001 - a usage page is not worth a 500
        logger.warning("Usage analytics unavailable: %s", exc)
        return {"days": days, "feature": feature, "has_data": False,
                "unavailable": True, "totals": {}, "features": [], "daily": []}

    features = []
    for row in rows:
        operations = row["operations"] or 0
        features.append({
            "metric": row["metric"],
            "feature": _feature_of(row["metric"], row.get("mode")),
            "label": _label(row["metric"], row.get("mode")),
            "operations": operations,
            "failures": row["failures"] or 0,
            # Approval continuations count toward execution health, not new
            # questions. Match the failure numerator's segment denominator.
            "failure_rate": round((row["failures"] or 0) / (row.get("segments", operations) or 1) * 100),
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "tokens": (row["input_tokens"] or 0) + (row["output_tokens"] or 0),
            "llm_turns": row["llm_turns"] or 0,
            "tool_calls": row["tool_calls"] or 0,
            "cost_usd": float(row.get("cost_usd") or 0),
            "cost_unknown": row.get("cost_unknown") or 0,
            "median_ms": row["median_ms"],
            "p95_ms": row["p95_ms"],
        })

    totals = {
        "operations": sum(f["operations"] for f in features),
        "tokens": sum(f["tokens"] for f in features),
        "failures": sum(f["failures"] for f in features),
        "tool_calls": sum(f["tool_calls"] for f in features),
        "cost_usd": round(sum(f["cost_usd"] for f in features), 6),
        # How many requests the providers gave no price for. Surfaced rather
        # than buried: "$0.04" over a window where half the rows were unpriced
        # is a number somebody would reasonably act on and should not.
        "cost_unknown": sum(f["cost_unknown"] for f in features),
    }

    peak = max((d["operations"] or 0) for d in daily) if daily else 0
    return {
        "days": days,
        "feature": feature,
        "feature_label": FEATURE_LABELS.get(feature) if feature else None,
        "has_data": totals["operations"] > 0,
        "unavailable": False,
        "totals": totals,
        "features": features,
        "daily": [{
            "day": d["day"].isoformat() if hasattr(d["day"], "isoformat") else str(d["day"]),
            "operations": d["operations"] or 0,
            # Height as a percentage of the busiest day, computed here so the
            # chart does not have to find its own maximum in three places.
            "height": round((d["operations"] or 0) / peak * 100) if peak else 0,
        } for d in daily],
        "peak": peak,
    }
