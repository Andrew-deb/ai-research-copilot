"""
dashboard/services/quota_service.py — how much of a metered feature may be used.

This answers "may THIS visitor do this now?". It knows nothing about OpenRouter,
Hugging Face or rate limits, and it must stay that way.

    user request
        -> quota check          HERE          "may this visitor?"
        -> application service
        -> provider throttling  client layer  "may we call upstream?"
        -> external API

Merging the two produces the worst of both: a provider outage that burns a user's
daily allowance, or a user limit that cannot be changed without editing an API
client. Batching, caching, politeness delays and backoff stay in the clients that
own those relationships.

Two independent limits apply to every metered call:

    per-visitor   shapes behaviour and prompts sign-in. Bypassable by clearing
                  cookies, whoever it is keyed on - it is a UX device.
    global        the real protection, because no amount of cookie-clearing moves
                  it. Applies to authenticated traffic too: many well-behaved
                  users cost as much as one abusive one.
"""

import logging

from config import (
    ANON_AGENT_PER_DAY,
    ANON_RAG_PER_DAY,
    ANON_SEARCH_PER_DAY,
    GLOBAL_AGENT_PER_DAY,
    GLOBAL_RAG_PER_DAY,
    QUOTAS_ENABLED,
    USER_AGENT_PER_DAY,
    USER_RAG_PER_DAY,
    USER_SEARCH_PER_DAY,
)
from exceptions import QuotaExceededError
from repositories import lakebase

logger = logging.getLogger(__name__)

SEMANTIC_SEARCH = "semantic_search"
RAG_QUERY = "rag_query"
AGENT_QUERY = "agent_query"

METRICS = (SEMANTIC_SEARCH, RAG_QUERY, AGENT_QUERY)

# Human wording for the metric, used in the message a visitor actually reads.
_METRIC_LABEL = {
    SEMANTIC_SEARCH: "semantic searches",
    RAG_QUERY: "research answers",
    AGENT_QUERY: "agent requests",
}

# Metrics with a global ceiling. Semantic search has none: it costs one embedding
# call and one vector query, so it is not where a budget goes.
_GLOBAL_LIMITS = {
    RAG_QUERY: GLOBAL_RAG_PER_DAY,
    AGENT_QUERY: GLOBAL_AGENT_PER_DAY,
}


class StaticQuotaPolicy:
    """
    Limits from configuration.

    `user_id` is accepted and ignored. That is deliberate: it is the seam that
    lets a database-backed policy with per-user overrides replace this class
    without touching a single route, because routes already pass it. Adding the
    parameter later would mean editing every call site instead.
    """

    _LIMITS = {
        "anonymous": {
            SEMANTIC_SEARCH: ANON_SEARCH_PER_DAY,
            RAG_QUERY: ANON_RAG_PER_DAY,
            AGENT_QUERY: ANON_AGENT_PER_DAY,
        },
        "authenticated": {
            SEMANTIC_SEARCH: USER_SEARCH_PER_DAY,
            RAG_QUERY: USER_RAG_PER_DAY,
            AGENT_QUERY: USER_AGENT_PER_DAY,
        },
    }

    def limit(self, tier: str, metric: str, user_id: str | None = None) -> int:
        return self._LIMITS.get(tier, self._LIMITS["anonymous"]).get(metric, 0)


_policy = StaticQuotaPolicy()


def limit_for(tier: str, metric: str, user_id: str | None = None) -> int:
    """The per-visitor allowance. The only place that knows where a number comes from."""
    return _policy.limit(tier, metric, user_id)


def global_limit_for(metric: str) -> int | None:
    return _GLOBAL_LIMITS.get(metric)


def check_and_consume(metric: str, tier: str, scope: str, scope_id: str) -> dict:
    """
    Consume one unit of `metric`, or raise QuotaExceededError.

    The global ceiling is checked FIRST, and deliberately: when it is reached the
    feature is resting for everyone, so telling an individual visitor they are
    personally out of allowance would be wrong and would invite them to sign in
    for something that would not help.

    Counting is increment-then-compare rather than compare-then-increment. Two
    simultaneous requests both reading "4 of 5 used" would both proceed;
    incrementing first makes the database arbitrate, and the cost of occasionally
    consuming one unit from a rejected request is far lower than the cost of
    letting a limit be exceeded under load.
    """
    if not QUOTAS_ENABLED:
        return {"metric": metric, "used": 0, "limit": None, "remaining": None}

    if metric not in METRICS:
        raise ValueError(f"Unknown quota metric {metric!r}.")

    ceiling = global_limit_for(metric)
    if ceiling is not None:
        used_globally = lakebase.increment_usage("global", "global", metric)
        if used_globally > ceiling:
            logger.warning("Global %s ceiling reached (%d/%d)", metric, used_globally, ceiling)
            raise QuotaExceededError(
                f"{_METRIC_LABEL[metric].capitalize()} are resting for today.",
                metric=metric, scope="global", used=used_globally, limit=ceiling,
            )

    allowance = limit_for(tier, metric, scope_id if scope == "user" else None)
    used = lakebase.increment_usage(scope, scope_id, metric)

    if used > allowance:
        raise QuotaExceededError(
            f"You've used your {_METRIC_LABEL[metric]} for today.",
            metric=metric, scope=scope, used=used, limit=allowance,
        )

    return {"metric": metric, "used": used, "limit": allowance,
            "remaining": max(allowance - used, 0)}


def usage_summary(tier: str, scope: str, scope_id: str) -> dict:
    """Current consumption per metric, for rendering "3 of 5 left" without consuming."""
    if not QUOTAS_ENABLED:
        return {}
    counts = lakebase.get_usage_counts(scope, scope_id)
    return {
        metric: {
            "used": counts.get(metric, 0),
            "limit": limit_for(tier, metric, scope_id if scope == "user" else None),
        }
        for metric in METRICS
    }
