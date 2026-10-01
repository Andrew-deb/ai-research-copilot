"""
dashboard/routes/account.py — acting on your own access.

Signing devices out, and ending the account.

**Deletion takes a round trip through Google.** Not a confirmation dialog alone:
the threat here is an unlocked laptop, and a dialog is something anybody sitting
at it can click. Proving the account again is the only guard that answers that,
and it is cheap for the person who genuinely meant it.

The proof is time-boxed and single-use. A `recent_auth_at` that never expired
would turn "signed in this morning" into standing permission to delete, which is
the thing re-authentication exists to prevent.
"""

import logging
import time

from flask import Blueprint, redirect, request, session, url_for

from middleware.auth import (
    REAUTH_KEY,
    SESSION_TOKEN_KEY,
    current_user,
    current_user_id,
)
from middleware.capabilities import require_capability
from routes.helpers import action_response
from services import account_service

logger = logging.getLogger(__name__)

bp = Blueprint("account", __name__)

# Long enough to read the dialog and type an email address, short enough that a
# walk to the kettle closes the window.
REAUTH_MAX_AGE_SECONDS = 300


def _reauthenticated_recently() -> bool:
    proved_at = session.get(REAUTH_KEY)
    return bool(proved_at) and (time.time() - float(proved_at)) < REAUTH_MAX_AGE_SECONDS


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@bp.post("/account/sessions/<session_id>/revoke")
@require_capability("notes:write")
def revoke_session(session_id: str):
    account_service.sign_out_device(current_user_id(), session_id)
    return action_response(
        {"ok": True},
        redirect_to=url_for("settings.page", section="security"),
        flash_message="That device has been signed out.",
    )


@bp.post("/account/sessions/revoke-others")
@require_capability("notes:write")
def revoke_other_sessions():
    """
    Everything except this browser.

    Keeping the current session is deliberate: being logged out by your own
    security action reads as a failure, and somebody worried about a device they
    no longer control should not have to sign in again on the one in their hand.
    """
    revoked = account_service.sign_out_others(
        current_user_id(), session.get(SESSION_TOKEN_KEY) or "")
    return action_response(
        {"ok": True, "revoked": revoked},
        redirect_to=url_for("settings.page", section="security"),
        flash_message=(f"Signed out of {revoked} other "
                       f"{'device' if revoked == 1 else 'devices'}."
                       if revoked else "There were no other devices."),
    )


@bp.post("/account/sessions/revoke-all")
@require_capability("notes:write")
def revoke_all_sessions():
    """
    Everything, including this browser — so it ends by signing you out.

    The session is cleared here rather than left to expire on its own: the row
    is already revoked, so the cookie is dead, and a page that carried on
    looking signed in until the next request would be lying about the thing
    somebody just pressed a button to change.
    """
    revoked = account_service.sign_out_everywhere(current_user_id())
    logger.info("Signed out of all %s sessions", revoked)
    session.clear()
    return redirect(url_for("home.index"))


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

@bp.post("/account/delete/confirm")
@require_capability("notes:write")
def begin_delete():
    """
    Hold the typed email, then send them to Google to prove the account.

    The confirmation is kept in the session rather than re-asked afterwards, so
    the flow reads as one decision rather than two — and so the value being
    checked is the one typed before leaving, not something a redirect could
    supply on the way back.
    """
    session["delete_confirm_email"] = (request.form.get("confirm_email") or "").strip()
    return redirect(url_for("auth.reauth", next=url_for("account.finish_delete")))


@bp.get("/account/delete")
@require_capability("notes:write")
def finish_delete():
    """
    Called only after the Google round trip. Refuses without fresh proof.

    A GET that deletes would normally be wrong — but this one is the OAuth
    return leg, reachable only with a `reauth_at` this server wrote moments ago,
    and it consumes that proof as it runs. An <img> tag pointing here achieves
    nothing.
    """
    user = current_user()
    if not _reauthenticated_recently():
        session.pop("delete_confirm_email", None)
        return action_response(
            {"ok": False},
            redirect_to=url_for("settings.page", section="security"),
            flash_message="For your security, please start the deletion again.",
        )

    typed = session.pop("delete_confirm_email", "")
    session.pop(REAUTH_KEY, None)          # single use, whatever happens next

    try:
        account_service.delete_account(user["user_id"], typed)
    except Exception as exc:  # noqa: BLE001 - surfaced to the person who asked
        logger.warning("Account deletion refused for %s: %s", user["user_id"], exc)
        return action_response(
            {"ok": False},
            redirect_to=url_for("settings.page", section="security"),
            flash_message=str(exc),
        )

    session.clear()
    return redirect(url_for("home.index"))
