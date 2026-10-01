"""
dashboard/services/account_service.py — devices, and the end of an account.

Two jobs that share a theme: both are about a person acting on their own access
rather than on their content.

**Devices.** Turning `user_sessions` rows into something readable. The user agent
is parsed for display only — it is a string the client chooses, so nothing is
decided on its strength, and a browser lying about itself changes a label and
nothing else.

**Deletion.** Guarded by re-authentication rather than by a checkbox. The threat
is not somebody deleting their own account on purpose; it is an unlocked laptop,
and only proving the account again answers that.
"""

from __future__ import annotations

import logging

from exceptions import ValidationError
from middleware.auth import forget_sessions, forget_user
from repositories import lakebase, sessions

logger = logging.getLogger(__name__)

# Enough to tell devices apart in a list, and no more. A full user-agent parser
# is a dependency and a maintenance burden for a string that only ever becomes a
# label; where this cannot tell, it says so rather than guessing.
_BROWSERS = (
    ("Edg", "Edge"), ("OPR", "Opera"), ("Chrome", "Chrome"),
    ("Firefox", "Firefox"), ("Safari", "Safari"),
)
_PLATFORMS = (
    ("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"),
    ("iPad", "iPad"), ("Mac OS", "macOS"), ("Linux", "Linux"),
)


def describe_device(user_agent: str | None) -> str:
    """"Chrome on Windows", or an honest shrug."""
    agent = user_agent or ""
    browser = next((name for token, name in _BROWSERS if token in agent), None)
    platform = next((name for token, name in _PLATFORMS if token in agent), None)

    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform or "Unknown device"


def devices(user_id: str, current_token: str | None) -> list[dict]:
    """
    Live sessions, most recent first, with this one marked.

    Marking the current device is what makes the list actionable: "sign this one
    out" needs to be obviously unavailable for the browser you are reading it in.
    """
    current_hash = sessions.digest(current_token) if current_token else None
    rows = []
    for row in sessions.list_active(user_id):
        rows.append({
            "session_id": str(row["session_id"]),
            "label": describe_device(row.get("user_agent")),
            "ip_address": row.get("ip_address") or "Unknown address",
            "last_seen_at": row.get("last_seen_at"),
            "created_at": row.get("created_at"),
            "is_current": bool(current_hash and row.get("session_hash") == current_hash),
        })
    return rows


def sign_out_device(user_id: str, session_id: str) -> int:
    revoked = sessions.revoke_one(user_id, session_id)
    forget_sessions(user_id)
    logger.info("Revoked session %s for user %s", session_id, user_id)
    return revoked


def sign_out_others(user_id: str, keep_token: str) -> int:
    revoked = sessions.revoke_others(user_id, keep_token)
    forget_sessions(user_id)
    logger.info("Revoked %s other sessions for user %s", revoked, user_id)
    return revoked


def sign_out_everywhere(user_id: str) -> int:
    """
    Every session, this one included.

    Distinct from `sign_out_others` on purpose. That one is housekeeping — tidy
    up the laptop at the office. This is the panic button: somebody thinks their
    account is compromised, and leaving the current session alive because it
    happens to be the one asking would be exactly the wrong reading of "all".
    """
    revoked = sessions.revoke_all(user_id)
    forget_sessions(user_id)
    logger.info("Revoked all %s sessions for user %s", revoked, user_id)
    return revoked


def set_incognito(user_id: str, enabled: bool) -> dict:
    user = lakebase.set_incognito(user_id, enabled)
    forget_user(user_id)
    return user or {}


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def delete_account(user_id: str, typed_email: str) -> None:
    """
    Erase the account, once the typed confirmation matches.

    The typed email is the smaller of the two guards — it exists so nobody
    deletes an account by muscle memory on a page full of buttons. The real one
    is re-authentication, which the route enforces before calling this.

    Sessions are revoked before the row goes so that every other browser is
    signed out by the deletion itself rather than left to discover it.
    """
    user = lakebase.get_user_by_id(user_id)
    if not user:
        raise ValidationError("That account no longer exists.")

    if (typed_email or "").strip().lower() != (user.get("email") or "").lower():
        raise ValidationError(
            "That does not match the email on this account.")

    sessions.revoke_all(user_id)
    forget_sessions(user_id)
    forget_user(user_id)
    lakebase.delete_user(user_id)
    logger.info("Deleted account %s", user_id)
