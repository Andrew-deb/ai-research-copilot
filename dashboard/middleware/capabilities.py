"""
dashboard/middleware/capabilities.py — what a tier may do, and how much of it.

Two questions, two decorators, kept apart because they have different answers:

    @require_capability("library:write")   may this tier use the feature at all?
    @require_quota("rag_query")            is there allowance left right now?

Collapsing them would mean telling a signed-in user who ran out of research
answers to sign in.

**Not an RBAC framework.** One frozen dict per tier, and the only way to gain a
capability is to sign in. No roles table, no permission management, no admin UI.
When a real need for per-user grants appears, the seam is `quota_service`'s
policy object, not this module.
"""

import functools
import logging

from flask import g

from exceptions import CapabilityDeniedError
from middleware.auth import TIER_ANONYMOUS, current_quota_scope, current_tier
from services import quota_service

logger = logging.getLogger(__name__)

# Capability names are `area:verb`. Reads are absent entirely: browsing the
# global corpus needs no capability, so listing one would imply a decision that
# was never made.
LIBRARY_WRITE = "library:write"
NOTES_WRITE = "notes:write"
GOALS_WRITE = "goals:write"
PROGRESS_WRITE = "progress:write"
SEARCH_SEMANTIC = "search:semantic"
RAG_ASK = "rag:ask"
AGENT_QUERY = "agent:query"
# Conversations outlive a visit, so they belong with the persistent
# capabilities rather than the metered ones.
CHAT_HISTORY = "chat:history"

_ANONYMOUS = frozenset({SEARCH_SEMANTIC, RAG_ASK, AGENT_QUERY})

CAPABILITIES: dict[str, frozenset[str]] = {
    # Anonymous visitors get every *expensive* capability and no *persistent*
    # one. That is the shape of a demo: it should feel like the product, and it
    # must not accumulate state nobody owns. The expensive ones are metered, so
    # "can use" is not "can use without limit".
    TIER_ANONYMOUS: _ANONYMOUS,
    "authenticated": _ANONYMOUS | {
        LIBRARY_WRITE, NOTES_WRITE, GOALS_WRITE, PROGRESS_WRITE, CHAT_HISTORY,
    },
}


def tier_can(tier: str, capability: str) -> bool:
    return capability in CAPABILITIES.get(tier, frozenset())


def can(capability: str) -> bool:
    """For templates: hide a control the current visitor may not use."""
    return tier_can(current_tier(), capability)


def require_capability(capability: str):
    """
    Refuse the request unless the current tier holds `capability`.

    Raises rather than redirecting so the error handler can answer in the shape
    the caller expects - JSON for a fetch(), a flash and redirect for a form.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            tier = current_tier()
            if not tier_can(tier, capability):
                raise CapabilityDeniedError(
                    "Sign in to save your own papers, notes and reading progress.",
                    capability=capability,
                    requires_auth=True,
                )
            return view(*args, **kwargs)
        return wrapper
    return decorator


def consume_quota(metric: str) -> dict:
    """
    Consume one unit of `metric`, or raise QuotaExceededError.

    Exposed as a function as well as a decorator for the one case the decorator
    cannot express: a route that must decide whether there is any work to do
    before charging for it. The agent endpoint is that case while it is not yet
    connected - see agent_service.is_connected. It is still called before the
    work, never after, which is the property that makes a limit a limit.
    """
    scope, scope_id = current_quota_scope()
    g.quota = quota_service.check_and_consume(
        metric=metric, tier=current_tier(), scope=scope, scope_id=scope_id
    )
    return g.quota


def require_quota(metric: str):
    """
    Consume one unit of `metric`, or refuse.

    Applied OUTSIDE the work it meters, so a request that has no allowance never
    reaches an embedding call or an LLM completion - metering after the fact
    would record the cost without preventing it.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            consume_quota(metric)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def register_capabilities(app) -> None:
    """Expose `can()` to templates so the UI matches what the server will allow."""

    @app.context_processor
    def inject_capabilities() -> dict:
        return {
            "can": can,
            "CAP": {
                "library_write": LIBRARY_WRITE,
                "notes_write": NOTES_WRITE,
                "goals_write": GOALS_WRITE,
                "progress_write": PROGRESS_WRITE,
            },
        }
