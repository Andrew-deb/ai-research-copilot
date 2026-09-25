"""
tests/test_auth_identity.py — turning a Google identity into an account.

`auth_service.resolve_or_create_user` decides which account a person gets, and
had no tests at all. The first real Google sign-in crashed on its second line:

    AttributeError: module 'repositories.lakebase' has no attribute 'get_user_by_email'

The suite was green throughout, because the FakeDB implemented the method and
the real repository never had it. The fake was not standing in for the module —
it was standing in for a module that did not exist, and every test that mattered
passed against the fiction.

So this file does two jobs: cover the matching logic branch by branch, and
compare the fake against the thing it pretends to be.
"""

import os
import pathlib
import re

import pytest

from repositories import lakebase
from services import auth_service

SUBJECT = "google-sub-1234567890"
EMAIL = "researcher@example.com"


# ---------------------------------------------------------------------------
# The drift that hid the bug
# ---------------------------------------------------------------------------

def _referenced_functions() -> set[str]:
    """Every lakebase.<name>( call made anywhere in the dashboard."""
    root = pathlib.Path(__file__).resolve().parents[1] / "dashboard"
    names: set[str] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        names |= set(re.findall(r"\blakebase\.([a-z_][a-z0-9_]*)\s*\(",
                                path.read_text(encoding="utf-8")))
    return names


def test_every_repository_function_the_app_calls_exists():
    """
    The test that would have caught this before a person ever clicked Sign in.
    A missing repository function is an AttributeError at the moment of use —
    which, on the OAuth callback, is after the visitor has already left the site
    and come back.
    """
    missing = sorted(n for n in _referenced_functions() if not hasattr(lakebase, n))
    assert missing == [], f"called but not defined: {missing}"


def test_the_fake_does_not_invent_functions(db):
    """
    A fake method with no real counterpart is worse than a missing one: the
    suite goes green *because* of it. The db fixture only patches names the real
    module already has, so an invented one is silently skipped and every test
    exercises a repository the application does not have.
    """
    from tests import conftest

    invented = sorted(
        name for name in dir(conftest.FakeDB)
        if not name.startswith("_")
        and name not in conftest.FAKE_ONLY_HELPERS
        and not hasattr(lakebase, name)
    )
    assert invented == [], f"FakeDB defines what lakebase does not: {invented}"


# ---------------------------------------------------------------------------
# Matching order — the security-relevant part
# ---------------------------------------------------------------------------

def test_a_returning_user_gets_their_own_account(db):
    """The subject is the real key: same `sub`, same account, every time."""
    first = auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email=EMAIL, display_name="A Researcher")
    again = auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email=EMAIL, display_name="A Researcher")

    assert first["user_id"] == again["user_id"]


def test_a_new_identity_creates_an_account(db):
    """The path that crashed. Nothing matches, so an account is made."""
    user = auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email=EMAIL, display_name="A Researcher")

    assert user["email"] == EMAIL
    assert user["auth_provider"] == "google"
    assert user["provider_subject"] == SUBJECT


def test_an_account_that_predates_oauth_is_linked_not_duplicated(db):
    """
    The dev/demo row was created before sign-in existed. Linking is what keeps
    its collections and notes attached to the person instead of stranding them
    behind a second row with the same address.
    """
    existing = db.get_or_create_user(EMAIL, "Earlier Account")

    linked = auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email=EMAIL, display_name="A Researcher")

    assert linked["user_id"] == existing["user_id"]
    assert linked["provider_subject"] == SUBJECT


def test_a_reassigned_address_cannot_claim_the_account(db):
    """
    Google lets an account's address change, so "whoever presents this email" is
    not an identity. If a previous owner's address were reassigned, matching on
    email alone would hand the new holder the original account.
    """
    auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email=EMAIL, display_name="First Owner")

    with pytest.raises(PermissionError):
        auth_service.resolve_or_create_user(
            provider="google", subject="a-different-sub", email=EMAIL,
            display_name="Someone Else")


def test_a_changed_address_still_finds_the_account(db):
    """The converse: the subject is stable even when the address is not."""
    first = auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email=EMAIL, display_name="A Researcher")

    moved = auth_service.resolve_or_create_user(
        provider="google", subject=SUBJECT, email="new.address@example.com",
        display_name="A Researcher")

    assert moved["user_id"] == first["user_id"]


# ---------------------------------------------------------------------------
# The lookup itself
# ---------------------------------------------------------------------------

def test_the_email_lookup_ignores_case():
    """
    Google may return `Name@Gmail.com` where the row was written
    `name@gmail.com`. A case-sensitive miss would not raise — it would quietly
    create a second account and leave the first one's library behind.
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "repositories" / "lakebase.py").read_text(encoding="utf-8")
    body = source.split("def get_user_by_email")[1][:900]

    assert "lower(email) = lower(%s)" in body
