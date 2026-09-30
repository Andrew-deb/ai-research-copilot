"""
dashboard/services/settings_service.py — what an account can change about itself.

Four areas, and what is NOT editable in each matters as much as what is.

**Profile.** The display name only. Email and provider are the verified
identity this application authenticates against — showing them is useful, and
letting somebody type over them would mean the page displays a claim rather
than a fact.

**Research preferences.** The same fields and topics onboarding collects,
written through the same path with the same provenance. A second way to say
"I research immunology" that stored it differently would give the eventual
personalisation two sources disagreeing about one person.

**Appearance.** Deliberately NOT stored on the account. The theme and the
sidebar width are per-device — a laptop in a bright room and a phone at night
want different answers, and syncing them means one device overrules the other.
They stay in the browser, and the page says so rather than leaving somebody to
discover it.

**Usage.** Read-only, and it is the only honest option: the allowances are
operator configuration, not preferences. A settings page that let somebody
raise their own ceiling would not be a settings page.
"""

from __future__ import annotations

import logging

from exceptions import ValidationError
from repositories import lakebase
from services import account_service, onboarding_service, quota_service

logger = logging.getLogger(__name__)

DISPLAY_NAME_MAX = 80

# What a person may see about their own consumption. Ceilings appear beside the
# counts as context — "3 of 10 today" — and nowhere as an input.
USAGE_LABELS = {
    "semantic_search": "Searches",
    "rag_query": "Cited answers",
    "agent_query": "Research questions",
}


def overview(user_id: str, tier: str, session_token: str | None = None) -> dict:
    """
    Everything the settings page shows, in one call.

    Shaped as answers rather than questions. The first version of this page
    rendered every onboarding option permanently — two screens of radio buttons
    to convey three facts — because it was built from the onboarding template.
    A settings page shows what you chose; the choices appear when you ask to
    change them.
    """
    user = lakebase.get_user_by_id(user_id) or {}
    profile = lakebase.get_user_profile(user_id) or {}
    chosen = onboarding_service.interests(user_id)

    return {
        "account": {
            "incognito": bool(user.get("incognito_mode")),
            "devices": account_service.devices(user_id, session_token),
        },
        "identity": {
            "email": user.get("email"),
            "provider": user.get("auth_provider"),
            "display_name": user.get("display_name"),
            # Drives the "following your Google name" note: without it the
            # field looks the same whether it is theirs or the provider's.
            "name_is_custom": bool(user.get("display_name_custom")),
            "avatar_url": user.get("avatar_url"),
            "joined": user.get("created_at"),
        },
        "research": {
            "fields": chosen.get("field", []),
            "topics": chosen.get("topic", []),
            "primary_goal": profile.get("primary_goal"),
            "researcher_type": profile.get("researcher_type"),
            "help_tasks": profile.get("help_tasks") or [],
            "onboarding_complete": bool(profile.get("onboarding_completed_at")),
        },
        "usage": usage(user_id, tier),
    }


def usage(user_id: str, tier: str) -> list[dict]:
    """
    Today's consumption per metric. Read-only, always.

    Empty when metering is switched off rather than showing zeros: "0 of 0"
    reads as an exhausted allowance, which is the opposite of what an unmetered
    deployment means.
    """
    summary = quota_service.usage_summary(tier, "user", user_id)
    if not summary:
        return []

    rows = []
    for metric, counts in summary.items():
        limit = counts.get("limit") or 0
        used = counts.get("used") or 0
        rows.append({
            "metric": metric,
            "label": USAGE_LABELS.get(metric, metric.replace("_", " ").title()),
            "used": used,
            "limit": limit,
            "left": max(limit - used, 0),
            # Clamped: a limit lowered after somebody has already spent more
            # than it should not render a bar past its own end.
            "percent": min(round(used / limit * 100), 100) if limit else 0,
        })
    return sorted(rows, key=lambda r: r["label"])


# ---------------------------------------------------------------------------
# Changing things
# ---------------------------------------------------------------------------

def update_display_name(user_id: str, name: str | None) -> dict:
    """
    Set a chosen name, or clear it to follow the provider again.

    Emptying the field is a real instruction — "use my Google name" — rather
    than an error, so it clears the custom flag instead of being refused.
    """
    name = (name or "").strip()
    if len(name) > DISPLAY_NAME_MAX:
        raise ValidationError(
            f"Keep your name under {DISPLAY_NAME_MAX} characters.")

    user = lakebase.set_display_name(user_id, name or None)

    # The identity cache is read on every request; a stale one would show the
    # old name until it expired.
    from middleware.auth import forget_user
    forget_user(user_id)

    return user or {}


def update_research(user_id: str, answers: dict) -> dict:
    """
    Change research preferences.

    Routed through onboarding_service step by step rather than writing
    interests directly. Two paths into the same table would eventually disagree
    about provenance or validation, and the one thing this data has to keep is
    that an explicit answer is recognisably explicit.

    Only the sections present are touched: the page posts one card at a time,
    and a form that omits topics means "I did not change topics", not "I have
    none".
    """
    changed = []

    if "fields" in answers:
        onboarding_service.save_step(user_id, 1, {"fields": answers["fields"]})
        changed.append("fields")
    if "topics" in answers:
        onboarding_service.save_step(user_id, 2, {"topics": answers["topics"]})
        changed.append("topics")
    if "primary_goal" in answers:
        onboarding_service.save_step(user_id, 3,
                                     {"primary_goal": answers["primary_goal"]})
        changed.append("primary_goal")
    if "researcher_type" in answers:
        onboarding_service.save_step(user_id, 4,
                                     {"researcher_type": answers["researcher_type"]})
        changed.append("researcher_type")
    if "help_tasks" in answers:
        onboarding_service.save_step(user_id, 5,
                                     {"help_tasks": answers["help_tasks"]})
        changed.append("help_tasks")

    logger.info("Research preferences updated for %s: %s", user_id, changed)
    return onboarding_service.state(user_id)
