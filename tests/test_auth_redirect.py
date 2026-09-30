"""
tests/test_auth_redirect.py — where you land after signing in.

Written because a bug got through that nothing here could have caught.
`needs_onboarding()` was correct and had four passing tests, all of which called
it directly. Nothing tested the callback that calls it — and the callback could
never reach it, because `safe_next(None)` returned the home page, so the
`if not asked_for` guard in front of it was permanently false. Every account
created since that code shipped went to the dashboard instead of onboarding.

So these drive the real callback with a stubbed Google, rather than asserting
about the service underneath it. A redirect decision is made by the route, and
that is the level it has to be tested at.

Three cases, and the interesting one is the third: an explicit destination beats
onboarding, because somebody who followed a link to a paper asked for that paper.
"""

import pytest

import routes.auth as auth_routes


class _StubGoogle:
    """Enough of Authlib's client for the two calls the routes make."""

    def __init__(self, claims):
        self._claims = claims

    def authorize_redirect(self, redirect_uri):
        from flask import redirect
        return redirect("https://accounts.google.test/o/oauth2/auth")

    def authorize_access_token(self):
        return {"userinfo": self._claims}


@pytest.fixture
def google(monkeypatch):
    """Signs in brand.new@example.com, who has no profile row."""
    stub = _StubGoogle({
        "sub": "google-subject-123",
        "email": "brand.new@example.com",
        "name": "Brand New",
        "email_verified": True,
    })
    monkeypatch.setattr(auth_routes._oauth, "google", stub, raising=False)
    monkeypatch.setattr(auth_routes._oauth, "_clients", {"google": stub}, raising=False)
    return stub


def _sign_in(client, next_url=None):
    client.get("/auth/google" + (f"?next={next_url}" if next_url else ""))
    return client.get("/auth/google/callback")


# ---------------------------------------------------------------------------
# The three destinations
# ---------------------------------------------------------------------------

def test_a_brand_new_account_is_taken_to_onboarding(client, db, google):
    """The case that was broken in production for every account ever created."""
    assert _sign_in(client).headers["Location"].endswith("/welcome")


def test_somebody_who_has_been_here_before_is_not(client, db, google):
    """
    Onboarding intercepts the first sign-in only. Being dropped into a form on
    every return is how people learn to click through one without reading it.
    """
    from services import onboarding_service

    _sign_in(client)                                    # creates the account
    user = db.get_user_by_email("brand.new@example.com")["user_id"]
    onboarding_service.save_step(user, 1, {"fields": ["Medicine"]})

    assert _sign_in(client).headers["Location"] == "/"


def test_an_asked_for_destination_beats_onboarding(client, db, google):
    """
    Somebody who followed a link to a paper asked for that paper. Onboarding can
    wait for the dashboard; the thing they clicked cannot.
    """
    assert _sign_in(client, "/collections").headers["Location"] == "/collections"


# ---------------------------------------------------------------------------
# The mechanism the bug lived in
# ---------------------------------------------------------------------------

def test_nothing_is_stored_when_nobody_asked_for_anywhere(client, db, google):
    """
    The regression itself, at the level it happened. A stored `/` is truthy, and
    a truthy `asked_for` silently disables the onboarding branch behind it.
    """
    client.get("/auth/google")
    with client.session_transaction() as session:
        assert not session.get("post_login_next")


def test_the_sign_in_link_carries_no_destination_by_default(client, db):
    """
    The other half of it: the page baked `next=/` into every link, so the route
    received an explicit destination from visitors who had never named one.
    """
    body = client.get("/login").get_data(as_text=True)
    assert "next=" not in body


def test_the_sign_in_link_still_carries_a_real_destination(client, db):
    body = client.get("/login?next=/collections").get_data(as_text=True)
    assert "next=%2Fcollections" in body or "next=/collections" in body


def test_a_hostile_destination_survives_neither_hop(client, db, google):
    """
    Rejected on the way in, and again on the way out. The second check matters
    because a session minted by an older build is not something to redirect on
    faith.
    """
    client.get("/auth/google?next=https://evil.example.com/steal")
    with client.session_transaction() as session:
        assert not session.get("post_login_next")

    # Straight to the callback: going through /auth/google again would overwrite
    # the planted value, and the test would prove nothing.
    with client.session_transaction() as session:
        session["post_login_next"] = "//evil.example.com/steal"

    assert client.get("/auth/google/callback").headers["Location"] == "/"
