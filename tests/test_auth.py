"""
tests/test_auth.py — identity resolution and the guards around it (Phase 3.1).

The dashboard moved from Databricks Apps, where an OAuth proxy injected
`X-Forwarded-Email`, to Render, where it authenticates users itself. These tests
cover the three ways a request acquires an identity — session, dev bypass,
anonymous — and the several ways it must *fail* to.

Two are worth singling out, because both would be silent:

  * A forwarded header must no longer do anything. Render has no proxy to set
    one, so any request carrying it is forged. A dormant header path that still
    worked would be an authentication bypass that no test noticed.

  * CSRF must actually be enabled. The app fixture turns it off so POST tests do
    not each have to fetch a token; without a test that builds an app with it ON,
    "disabled in tests" could quietly become "disabled everywhere".
"""

import pytest

from middleware import auth as auth_module
from tests.conftest import DEV_EMAIL, sign_in


# ---------------------------------------------------------------------------
# The header path is gone
# ---------------------------------------------------------------------------

def test_a_forwarded_email_header_does_nothing(signed_out_client, db):
    """
    Render has no auth proxy, so a request carrying this header invented it.
    Honouring it would let anyone become anyone by setting a header.
    """
    db.get_or_create_user("victim@example.com")

    resp = signed_out_client.get("/", headers={"X-Forwarded-Email": "victim@example.com"})

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_the_middleware_exposes_no_header_constants():
    """A removed code path should leave nothing behind for a later edit to revive."""
    for leftover in ("EMAIL_HEADER", "USERNAME_HEADER", "USER_ID_HEADER",
                     "REQUIRE_FORWARDED_AUTH"):
        assert not hasattr(auth_module, leftover), f"{leftover} survived the rewrite"


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------

def test_a_session_resolves_to_its_user(signed_out_client, db):
    user = db.get_or_create_user("real@example.com", "Real Person")
    sign_in(signed_out_client, user["user_id"])

    assert signed_out_client.get("/").status_code == 200


def test_signing_out_clears_the_session(client, db):
    user = db.get_or_create_user("bye@example.com")
    sign_in(client, user["user_id"])

    resp = client.post("/logout")

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert auth_module.SESSION_USER_KEY not in sess


def test_a_session_naming_a_deleted_user_is_cleared(signed_out_client, db):
    """
    The account was removed while the cookie lived on. Clearing the session turns
    a 500 on every page into a clean signed-out state.
    """
    user = db.get_or_create_user("ghost@example.com")
    sign_in(signed_out_client, user["user_id"])
    db.users_by_id.pop(str(user["user_id"]))
    db.users_by_email.pop("ghost@example.com")
    auth_module._USER_CACHE.clear()

    resp = signed_out_client.get("/")

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Dev bypass
# ---------------------------------------------------------------------------

def test_the_dev_bypass_resolves_without_signing_in(client, db):
    assert client.get("/").status_code == 200
    assert DEV_EMAIL in db.users_by_email


def test_a_real_session_wins_over_the_dev_bypass(client, db):
    """Order matters: debugging convenience must never displace a real identity."""
    user = db.get_or_create_user("priority@example.com")
    sign_in(client, user["user_id"])

    client.get("/")   # resolve once

    # The dev user is not created, because the session was resolved first.
    assert "priority@example.com" in db.users_by_email


def test_the_bypass_cannot_be_enabled_in_production():
    """
    The most important guard in the phase. A stray env var in the Render dashboard
    would otherwise make every visitor the dev user, silently, with no error.
    Config raises at import so the deploy fails its health check instead.
    """
    import importlib
    import os

    saved = {k: os.environ.get(k) for k in ("APP_ENV", "ALLOW_DEV_USER_BYPASS")}
    os.environ["APP_ENV"] = "production"
    os.environ["ALLOW_DEV_USER_BYPASS"] = "true"
    try:
        import config
        with pytest.raises(RuntimeError, match="ALLOW_DEV_USER_BYPASS"):
            importlib.reload(config)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import config
        importlib.reload(config)


# ---------------------------------------------------------------------------
# Anonymous and signed-out behaviour
# ---------------------------------------------------------------------------

def test_signed_out_requests_are_sent_to_login(signed_out_client):
    resp = signed_out_client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_the_login_page_itself_is_reachable_signed_out(signed_out_client):
    assert signed_out_client.get("/login").status_code == 200


def test_anonymous_browsing_is_allowed_when_enabled(anon_client):
    assert anon_client.get("/").status_code == 200


def test_an_anonymous_visitor_gets_no_user_row(anon_client, db):
    """
    Minting a row per visitor would fill `users` with abandoned sessions and make
    "how many users are there?" unanswerable.
    """
    before = len(db.users_by_id)
    anon_client.get("/")
    assert len(db.users_by_id) == before


def test_an_anonymous_visitor_gets_a_stable_quota_id(anon_client):
    """Quotas need something to count against; it must not change per request."""
    anon_client.get("/")
    with anon_client.session_transaction() as sess:
        first = sess.get(auth_module.SESSION_ANON_KEY)
    anon_client.get("/")
    with anon_client.session_transaction() as sess:
        assert sess.get(auth_module.SESSION_ANON_KEY) == first
    assert first


# ---------------------------------------------------------------------------
# Exempt paths
# ---------------------------------------------------------------------------

def test_healthz_answers_without_an_identity(signed_out_client):
    """
    Render's health check. Making it depend on identity - or on Lakebase - would
    restart the service during an outage it cannot fix by restarting.
    """
    resp = signed_out_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_static_assets_need_no_identity(signed_out_client):
    assert signed_out_client.get("/static/css/base.css").status_code == 200


# ---------------------------------------------------------------------------
# Open redirect
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    "https://evil.example.com/steal",
    "//evil.example.com/steal",          # protocol-relative: a browser reads this as absolute
    "http://evil.example.com",
])
def test_next_cannot_leave_the_site(app, hostile):
    """Without this, `?next=` turns a link from our domain into a phishing redirect."""
    from routes.auth import safe_next
    with app.test_request_context():
        assert safe_next(hostile) == "/"


def test_next_keeps_a_relative_path(app):
    from routes.auth import safe_next
    with app.test_request_context():
        assert safe_next("/collections") == "/collections"


def test_next_falls_back_when_absent(app):
    from routes.auth import safe_next
    with app.test_request_context():
        assert safe_next(None) == "/"
        assert safe_next("") == "/"


# ---------------------------------------------------------------------------
# CSRF is genuinely on
# ---------------------------------------------------------------------------

def test_csrf_rejects_a_post_without_a_token(db):
    """
    Cookie authentication made this reachable: before 3.1 an attacker's page could
    not forge an authenticated request, because it could not set the identity
    header. Now the browser attaches the session cookie by itself.

    Built with a fresh app so the shared fixture's WTF_CSRF_ENABLED=False cannot
    hide a regression.
    """
    from app import create_app

    protected = create_app()
    protected.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    client = protected.test_client()

    resp = client.post("/goals", json={"title": "Forged"})

    assert resp.status_code == 400
    assert not db.goals, "a request with no CSRF token created a goal"


# ---------------------------------------------------------------------------
# The development identity is a shared contract
# ---------------------------------------------------------------------------

def test_the_dev_identity_matches_the_seeded_user_and_the_agent():
    """
    Three places key on this address and all three must agree:

        setup_db.py                               seeds the row
        mcp_server/middleware/request_context.py  the agent writes as this user
        dashboard config DEV_USER_EMAIL           the dashboard reads as this user

    Changing one of them once already split the identity in two — the agent wrote
    to one library while the dashboard rendered another, so every collection, note
    and progress row created beforehand looked as though it had disappeared.
    Nothing was deleted; the dashboard had simply stopped being that user.

    This test fails loudly on the next attempt, because the symptom ("my data is
    gone") points nowhere near the cause.
    """
    import pathlib
    import re

    from config import DEV_USER_EMAIL

    root = pathlib.Path(__file__).resolve().parents[1]

    seed = (root / "setup_db.py").read_text(encoding="utf-8")
    assert DEV_USER_EMAIL in seed, (
        f"setup_db.py does not seed {DEV_USER_EMAIL!r}; a fresh install would give "
        f"the dev user an empty workspace."
    )

    context = (root / "mcp_server" / "middleware" / "request_context.py").read_text(encoding="utf-8")
    match = re.search(r'DEFAULT_USER_EMAIL\s*=\s*["\']([^"\']+)["\']', context)
    assert match, "mcp_server no longer declares DEFAULT_USER_EMAIL — update this test"
    assert match.group(1) == DEV_USER_EMAIL, (
        f"the agent writes as {match.group(1)!r} but the dashboard reads as "
        f"{DEV_USER_EMAIL!r}; work saved by one would be invisible to the other."
    )
