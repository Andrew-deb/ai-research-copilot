"""
tests/test_sessions.py — making a signed cookie withdrawable.

A Flask session cookie is valid until it expires, wherever it is, including on a
laptop somebody left on a train. Nothing on the server could previously say
otherwise. `user_sessions` is the row that can: the cookie carries a token, the
row decides whether the token still counts, and revoking is an UPDATE.

Two properties carry the weight here:

**Revoking must affect the cookie, not merely the browser holding it.** Clearing
a cookie stops that browser presenting it and does nothing about a copy taken
beforehand. That difference is the whole point of the table, so logout revokes.

**A cookie with no token is treated as signed out.** Grandfathering old cookies
would have made "sign out everywhere" quietly untrue for exactly the sessions
somebody is worried about.
"""

import pytest

from middleware import auth as auth_mw
from repositories import sessions
from tests.conftest import sign_in


@pytest.fixture
def user(db):
    return db.get_or_create_user("sessions@example.test", "Session Person")["user_id"]


# ---------------------------------------------------------------------------
# The token, and what is stored
# ---------------------------------------------------------------------------

def test_the_token_is_never_stored_as_itself(db, user):
    """
    The cookie is signed, not encrypted, so its contents are readable by whoever
    holds it. A dump of this table should still not hand anybody a session.
    """
    token = sessions.new_token()
    sessions.record(user, token, "Firefox", "10.0.0.1")

    stored = [r["session_hash"] for r in db.sessions.values()]
    assert token not in stored
    assert sessions.digest(token) in stored


def test_two_sign_ins_are_two_rows(db, user):
    for _ in range(2):
        sessions.record(user, sessions.new_token(), "Firefox", "10.0.0.1")

    assert len(sessions.list_active(user)) == 2


def test_what_the_device_list_shows(db, user):
    sessions.record(user, sessions.new_token(), "Edge on Windows", "105.112.233.95")
    row = sessions.list_active(user)[0]

    assert row["user_agent"] == "Edge on Windows"
    assert row["ip_address"] == "105.112.233.95"
    assert row["created_at"] and row["last_seen_at"]


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

def test_a_live_token_validates(db, user):
    token = sessions.new_token()
    sessions.record(user, token, None, None)

    assert sessions.touch_and_validate(token)["user_id"] == str(user)


def test_a_token_nobody_issued_does_not(db, user):
    assert sessions.touch_and_validate(sessions.new_token()) is None


def test_a_revoked_token_does_not(db, user):
    token = sessions.new_token()
    sessions.record(user, token, None, None)
    sessions.revoke_by_token(token)

    assert sessions.touch_and_validate(token) is None


# ---------------------------------------------------------------------------
# Revoking
# ---------------------------------------------------------------------------

def test_revoking_keeps_the_row(db, user):
    """
    "Signed out at 14:02 from an address in Lagos" is what somebody wants after
    losing a laptop, and a DELETE throws exactly that away.
    """
    token = sessions.new_token()
    sessions.record(user, token, "Edge", "102.89.83.63")
    sessions.revoke_by_token(token)

    row = next(iter(db.sessions.values()))
    assert row["revoked_at"] is not None
    assert row["ip_address"] == "102.89.83.63"


def test_signing_out_others_keeps_this_one(db, user):
    """
    Being logged out by your own security action reads as a failure. Somebody
    worried about a device they no longer control should not have to sign in
    again on the one they are holding.
    """
    here, there = sessions.new_token(), sessions.new_token()
    sessions.record(user, here, "This browser", None)
    sessions.record(user, there, "That browser", None)

    sessions.revoke_others(user, keep_token=here)

    assert sessions.touch_and_validate(here)
    assert sessions.touch_and_validate(there) is None


def test_revoking_one_device_is_scoped_to_its_owner(db, user):
    """A guessed session id revokes nothing that is not yours."""
    other = db.get_or_create_user("intruder@example.test", "Intruder")["user_id"]
    token = sessions.new_token()
    sessions.record(user, token, None, None)
    victim = sessions.list_active(user)[0]["session_id"]

    assert sessions.revoke_one(other, victim) == 0
    assert sessions.touch_and_validate(token)


def test_revoke_all_leaves_nothing(db, user):
    tokens = [sessions.new_token() for _ in range(3)]
    for token in tokens:
        sessions.record(user, token, None, None)

    sessions.revoke_all(user)

    assert sessions.list_active(user) == []
    assert all(sessions.touch_and_validate(t) is None for t in tokens)


def test_one_account_cannot_see_or_revoke_another(db, user):
    other = db.get_or_create_user("other@example.test", "Other")["user_id"]
    mine, theirs = sessions.new_token(), sessions.new_token()
    sessions.record(user, mine, None, None)
    sessions.record(other, theirs, None, None)

    sessions.revoke_all(user)

    assert len(sessions.list_active(other)) == 1
    assert sessions.touch_and_validate(theirs)


# ---------------------------------------------------------------------------
# What the middleware does with all this
# ---------------------------------------------------------------------------

def test_a_signed_in_request_works(signed_out_client, db, user):
    sign_in(signed_out_client, user)
    assert signed_out_client.get("/").status_code == 200


def test_a_cookie_with_no_token_is_signed_out(signed_out_client, db, user):
    """
    The grandfathering decision, exercised. A cookie naming a user but carrying
    no session token is one this build cannot revoke, so it is not honoured.
    """
    with signed_out_client.session_transaction() as session:
        session.clear()
        session[auth_mw.SESSION_USER_KEY] = str(user)

    assert signed_out_client.get("/").status_code == 302


def test_revoking_signs_the_browser_out(signed_out_client, db, user):
    token = sign_in(signed_out_client, user)
    assert signed_out_client.get("/").status_code == 200

    sessions.revoke_by_token(token)
    auth_mw.forget_sessions(user)

    assert signed_out_client.get("/").status_code == 302


def test_a_validated_session_is_not_re_checked_every_request(signed_out_client, db, user, monkeypatch):
    """
    The cache exists because this database sits across a network with a
    round-trip floor of several hundred milliseconds. Checking per request would
    put that on every page load to close a one-minute window.
    """
    sign_in(signed_out_client, user)
    calls = []
    real = auth_mw.sessions.touch_and_validate
    monkeypatch.setattr(auth_mw.sessions, "touch_and_validate",
                        lambda t: (calls.append(t), real(t))[1])

    for _ in range(5):
        signed_out_client.get("/")

    assert len(calls) == 1


def test_the_cache_is_purged_in_the_worker_that_revoked(db, user):
    """
    Immediate here, within SESSION_CHECK_SECONDS elsewhere. The stated trade.
    """
    token = sessions.new_token()
    sessions.record(user, token, None, None)
    auth_mw._session_is_live(token)
    assert token in auth_mw._SESSION_CACHE

    auth_mw.forget_sessions(user)
    assert token not in auth_mw._SESSION_CACHE


# ---------------------------------------------------------------------------
# Sign-in and sign-out
# ---------------------------------------------------------------------------

def test_logging_out_revokes_rather_than_just_forgetting(signed_out_client, db, user):
    """
    The difference between logging out and appearing to. Clearing the cookie
    stops this browser presenting the token; revoking kills a copy taken first.
    """
    token = sign_in(signed_out_client, user)
    assert sessions.touch_and_validate(token)        # live while signed in

    signed_out_client.post("/logout")

    # Stated as an attacker holding a copy would find it: the token is dead, not
    # merely absent from the browser it was taken from.
    assert sessions.touch_and_validate(token) is None
