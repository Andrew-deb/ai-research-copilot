"""
tests/test_account.py — devices, incognito, and ending an account.

The deletion tests carry most of the weight, and the property they defend is
that **a session alone does not authorise it**. The threat is not somebody
deleting their own account on purpose; it is an unlocked laptop, and a
confirmation dialog is something anybody sitting at one can click. Only proving
the account again answers that, which is why the flow leaves and comes back.

Two failure modes matter more than the happy path: proving a DIFFERENT Google
account must not authorise deleting this one, and proof must not be reusable.
"""

import time

import pytest

import routes.auth as auth_routes
from middleware import auth as auth_mw
from repositories import sessions
from services import account_service
from tests.conftest import sign_in


@pytest.fixture
def user(db):
    return db.get_or_create_user("owner@example.test", "Owner")["user_id"]


class _StubGoogle:
    """Returns whichever account the test says came back from Google."""

    def __init__(self):
        self.subject = "google-owner"
        self.email = "owner@example.test"
        self.prompts = []

    def authorize_redirect(self, uri, **kwargs):
        from flask import redirect
        self.prompts.append(kwargs.get("prompt"))
        return redirect("https://accounts.google.test/o/oauth2/auth")

    def authorize_access_token(self):
        return {"userinfo": {"sub": self.subject, "email": self.email,
                             "name": "Owner", "email_verified": True}}


@pytest.fixture
def google(monkeypatch):
    stub = _StubGoogle()
    monkeypatch.setattr(auth_routes._oauth, "google", stub, raising=False)
    monkeypatch.setattr(auth_routes._oauth, "_clients", {"google": stub}, raising=False)
    return stub


@pytest.fixture
def owner(db, signed_out_client, google, user):
    """Signed in, and known to Google as the account that owns this session."""
    db.users_by_id[str(user)]["auth_provider"] = "google"
    db.users_by_id[str(user)]["provider_subject"] = "google-owner"
    sign_in(signed_out_client, user)
    return user


def _prove(client):
    """The Google round trip, as the browser performs it."""
    client.get("/auth/reauth?next=/account/delete")
    return client.get("/auth/google/callback")


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

def test_a_user_agent_becomes_a_readable_label():
    agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0")
    assert account_service.describe_device(agent) == "Edge on Windows"


def test_an_unreadable_agent_says_so_rather_than_guessing():
    """A string the client chooses is not something to invent a device from."""
    assert account_service.describe_device("") == "Unknown device"
    assert account_service.describe_device(None) == "Unknown device"


def test_this_device_is_marked(db, signed_out_client, user):
    token = sign_in(signed_out_client, user)
    sessions.record(user, sessions.new_token(), "Firefox", "10.0.0.2")

    devices = account_service.devices(user, token)
    assert sum(1 for d in devices if d["is_current"]) == 1


def test_nothing_is_marked_current_without_a_token(db, user):
    """
    Better than marking the most recent one: a wrong badge here would invite
    somebody to sign out the browser they are holding.
    """
    sessions.record(user, sessions.new_token(), "Firefox", None)
    assert not any(d["is_current"] for d in account_service.devices(user, None))


def test_signing_out_others_from_the_route(db, signed_out_client, user):
    token = sign_in(signed_out_client, user)
    other = sessions.new_token()
    sessions.record(user, other, "Elsewhere", None)

    signed_out_client.post("/account/sessions/revoke-others")

    assert sessions.touch_and_validate(other) is None
    assert sessions.touch_and_validate(token) is not None


def test_signing_out_one_device_from_the_route(db, signed_out_client, user):
    token = sign_in(signed_out_client, user)
    doomed = sessions.new_token()
    sessions.record(user, doomed, "Mozilla/5.0 Firefox/130.0", "10.0.0.9")

    session_id = next(d["session_id"] for d in account_service.devices(user, token)
                      if not d["is_current"])
    signed_out_client.post(f"/account/sessions/{session_id}/revoke")

    assert sessions.touch_and_validate(doomed) is None
    assert sessions.touch_and_validate(token) is not None


# ---------------------------------------------------------------------------
# Incognito
# ---------------------------------------------------------------------------

def test_incognito_is_stored_on_the_account(client, db):
    client.get("/dashboard")
    user = next(iter(db.users_by_id))

    client.post("/settings/incognito", data={"incognito": "on"})
    assert db.users_by_id[str(user)]["incognito_mode"] is True


def test_incognito_does_not_create_a_profile_row(client, db):
    """
    The trap this column was placed to avoid. `needs_onboarding()` tests whether
    a `user_profiles` row exists at all, so storing a preference there would
    permanently mark onboarding as started for somebody who never saw it — the
    step 1 bug, re-introduced from a direction nobody would think to look.
    """
    from services import onboarding_service

    client.get("/dashboard")
    user = next(iter(db.users_by_id))

    client.post("/settings/incognito", data={"incognito": "on"})

    assert db.get_user_profile(user) is None
    assert onboarding_service.needs_onboarding(user) is True


def test_incognito_turns_back_off(client, db):
    client.get("/dashboard")
    user = next(iter(db.users_by_id))

    client.post("/settings/incognito", data={"incognito": "on"})
    client.post("/settings/incognito", data={"incognito": "off"})

    assert db.users_by_id[str(user)]["incognito_mode"] is False


# ---------------------------------------------------------------------------
# Deletion: the guards
# ---------------------------------------------------------------------------

def test_deletion_needs_recent_proof(db, signed_out_client, owner):
    """Reaching the final step without the Google round trip deletes nothing."""
    signed_out_client.get("/account/delete")
    assert db.get_user_by_id(owner) is not None


def test_stale_proof_is_not_proof(db, signed_out_client, owner, monkeypatch):
    """
    "Signed in this morning" is not permission to delete. Without an expiry,
    re-authentication would be a formality somebody passed hours ago.
    """
    _prove(signed_out_client)
    with signed_out_client.session_transaction() as session:
        session["delete_confirm_email"] = "owner@example.test"
        session[auth_mw.REAUTH_KEY] = time.time() - 10_000

    signed_out_client.get("/account/delete")
    assert db.get_user_by_id(owner) is not None


def test_proof_is_single_use(db, signed_out_client, owner):
    """
    One round trip authorises one deletion attempt. Otherwise a failed attempt
    would leave a live permission sitting in the session.
    """
    signed_out_client.post("/account/delete/confirm",
                           data={"confirm_email": "wrong@example.test"})
    _prove(signed_out_client)
    signed_out_client.get("/account/delete")          # refused: email mismatch

    with signed_out_client.session_transaction() as session:
        assert not session.get(auth_mw.REAUTH_KEY)


def test_a_different_google_account_does_not_authorise_this_one(db, signed_out_client,
                                                                owner, google):
    """
    The one that would be easy to get wrong: re-auth returns through the ordinary
    sign-in callback, so without an identity check, proving ANY Google account
    would authorise deleting THIS one.
    """
    google.subject = "google-someone-else"
    google.email = "someone.else@example.test"

    _prove(signed_out_client)
    with signed_out_client.session_transaction() as session:
        assert not session.get(auth_mw.REAUTH_KEY)


def test_reauth_forces_the_credential(db, signed_out_client, owner, google):
    """
    Without prompt=login Google recognises its own session and returns instantly
    — proving the browser still holds a cookie, which is the thing already in
    doubt. That would make this a redirect, not a check.
    """
    signed_out_client.get("/auth/reauth?next=/account/delete")
    assert google.prompts == ["login"]


def test_the_typed_email_must_match(db, owner):
    from exceptions import ValidationError
    with pytest.raises(ValidationError):
        account_service.delete_account(owner, "not-my@example.test")
    assert db.get_user_by_id(owner) is not None


# ---------------------------------------------------------------------------
# Deletion: what it removes, and what it keeps
# ---------------------------------------------------------------------------

def test_the_whole_flow_deletes_the_account(db, signed_out_client, owner):
    signed_out_client.post("/account/delete/confirm",
                           data={"confirm_email": "owner@example.test"})
    _prove(signed_out_client)
    signed_out_client.get("/account/delete")

    assert db.get_user_by_id(owner) is None


def test_deleting_takes_the_content_with_it(db, owner):
    db.create_collection(owner, "Private reading", "notes to self")
    db.upsert_user_profile(owner, {"primary_goal": "Run a literature review"})
    db.record_interest(owner, "topic", "immunology", "explicit_onboarding", 0.9)

    account_service.delete_account(owner, "owner@example.test")

    assert db.get_user_profile(owner) is None
    assert db.get_user_interests(owner) == []
    assert not [c for c in db.collections.values() if str(c["user_id"]) == str(owner)]


def test_deleting_signs_every_device_out(db, signed_out_client, owner):
    elsewhere = sessions.new_token()
    sessions.record(owner, elsewhere, "Other laptop", None)

    account_service.delete_account(owner, "owner@example.test")

    assert sessions.touch_and_validate(elsewhere) is None


def test_the_operational_record_survives_without_naming_anyone(db, owner):
    """
    Telemetry is kept and anonymised rather than deleted: it is the record of
    what the system did, which capacity planning depends on. `ai_operations`
    already declares ON DELETE SET NULL for exactly this reason.
    """
    db.ai_operations.append({"metric": "agent_query", "user_id": str(owner),
                             "input_tokens": 400, "latency_ms": 900})

    account_service.delete_account(owner, "owner@example.test")

    assert len(db.ai_operations) == 1
    assert db.ai_operations[0]["user_id"] is None
    assert db.ai_operations[0]["input_tokens"] == 400


def test_todays_counters_go_with_the_account(db, owner):
    """
    `usage_counters` has no foreign key — it counts anonymous visitors too — so
    nothing deletes these for us. Left behind, they would be inherited by
    whoever the id was next issued to.
    """
    db.usage[("user", str(owner), "agent_query")] = 7

    account_service.delete_account(owner, "owner@example.test")

    assert not [k for k in db.usage if k[0] == "user" and k[1] == str(owner)]


def test_one_account_is_not_deleted_with_another(db, owner):
    bystander = db.get_or_create_user("bystander@example.test", "Bystander")["user_id"]
    db.create_collection(bystander, "Their reading", "")

    account_service.delete_account(owner, "owner@example.test")

    assert db.get_user_by_id(bystander) is not None
    assert [c for c in db.collections.values() if str(c["user_id"]) == str(bystander)]
