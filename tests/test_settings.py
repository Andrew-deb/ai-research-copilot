"""
tests/test_settings.py — what an account can change about itself (Phase 6).

What is NOT editable matters as much as what is, so most of this file is about
the boundaries:

**Identity is shown, never offered.** Email and provider are what this
application authenticates against. They are displayed because they are useful
to see, and the route never reads them from the form — so no amount of editing
the page can submit one.

**Research preferences go through onboarding's own path.** A second way to say
"I research immunology" that stored it differently would give the eventual
personalisation two sources disagreeing about one person.

**Appearance is per-device, deliberately.** A laptop in a bright room and a
phone at night want different answers, and syncing them means one device
overrules the other.

**Usage is read-only**, because the allowances are operator configuration. A
settings page that let somebody raise their own ceiling would not be a settings
page.
"""

import pathlib
import re

import pytest

from services import quota_service, settings_service

ROOT = pathlib.Path(__file__).resolve().parents[1]
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# Getting there
# ---------------------------------------------------------------------------

def test_the_account_menu_leads_to_it(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'href="/settings"' in body


def test_the_menu_is_the_same_in_both_sidebar_states(client, db):
    """
    One element, shown whether the rail is expanded or collapsed to icons —
    rather than two that can drift apart.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    assert body.count('class="profile-menu-link"') == 1


def test_an_anonymous_visitor_has_no_settings(anon_client):
    """They have no account to have settings for."""
    assert anon_client.get("/settings").status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# Identity: shown, not offered
# ---------------------------------------------------------------------------

def test_the_verified_identity_is_displayed(client, db):
    _me(client, db)
    body = client.get("/settings").get_data(as_text=True)
    assert "Signs in with" in body


def test_the_email_is_not_a_form_field(client, db):
    """
    Not "validated and rejected" — never read. A disabled input still looks
    like something that could be typed into, so it is not an input at all.
    """
    _me(client, db)
    body = client.get("/settings").get_data(as_text=True)

    assert not re.search(r'<input[^>]*name="email"', body)
    assert not re.search(r'<input[^>]*name="auth_provider"', body)

    source = (ROOT / "dashboard" / "routes" / "settings.py").read_text(encoding="utf-8")
    assert 'request.form.get("email")' not in source


def test_posting_an_email_changes_nothing(client, db):
    """
    The boundary, exercised rather than asserted about.

    The name assertion is what makes the email assertion mean anything: it
    proves the post landed, so the unchanged email is the route ignoring a
    field rather than the request being rejected.
    """
    user = _me(client, db)
    before = db.users_by_id[str(user)]["email"]

    client.post("/settings/profile", data={"display_name": "Andy",
                                           "email": "attacker@example.com"})

    assert db.users_by_id[str(user)]["display_name"] == "Andy"
    assert db.users_by_id[str(user)]["email"] == before


# ---------------------------------------------------------------------------
# The display name, and the provider that keeps sending one
# ---------------------------------------------------------------------------

def test_a_chosen_name_is_kept(client, db):
    user = _me(client, db)
    settings_service.update_display_name(user, "Andy O.")

    assert db.users_by_id[str(user)]["display_name"] == "Andy O."
    assert db.users_by_id[str(user)]["display_name_custom"] is True


def test_signing_in_again_does_not_undo_it(client, db):
    """
    touch_user_login refreshes the name from the provider on every sign-in.
    Without the flag, somebody who renamed themselves would have it undone —
    silently, and somewhere they would never think to look.
    """
    user = _me(client, db)
    settings_service.update_display_name(user, "Andy O.")

    db.touch_user_login(user, display_name="Andrew Omaku")
    assert db.users_by_id[str(user)]["display_name"] == "Andy O."


def test_a_name_nobody_chose_still_follows_the_provider(client, db):
    """People change their Google name and expect this to follow."""
    user = _me(client, db)
    db.touch_user_login(user, display_name="Andrew Omaku")

    assert db.users_by_id[str(user)]["display_name"] == "Andrew Omaku"


def test_clearing_the_field_goes_back_to_the_provider(client, db):
    """
    Emptying it is an instruction — "use my Google name" — not an error, and
    clearing the flag is what makes the next sign-in repopulate it. No second
    column needed to remember what the provider said.
    """
    user = _me(client, db)
    settings_service.update_display_name(user, "Andy O.")
    settings_service.update_display_name(user, "")

    assert db.users_by_id[str(user)]["display_name_custom"] is False
    db.touch_user_login(user, display_name="Andrew Omaku")
    assert db.users_by_id[str(user)]["display_name"] == "Andrew Omaku"


def test_an_overlong_name_is_refused(client, db):
    from exceptions import ValidationError
    with pytest.raises(ValidationError):
        settings_service.update_display_name(_me(client, db), "x" * 200)


def test_the_column_the_sign_in_query_depends_on_is_declared(client, db):
    """
    touch_user_login reads display_name_custom on every sign-in. Without the
    migration the failure is not a missing feature, it is nobody being able to
    sign in.
    """
    migration = (ROOT / "sql" / "21_display_name.sql").read_text(encoding="utf-8")
    assert "display_name_custom" in migration
    assert "DEFAULT false" in migration

    repo = (ROOT / "dashboard" / "repositories"
            / "lakebase.py").read_text(encoding="utf-8")
    assert "display_name_custom" in repo


def test_the_cached_identity_is_dropped_on_rename(client, db):
    """
    The identity cache is read on every request; a stale one would show the old
    name until it expired.
    """
    source = (ROOT / "dashboard" / "services"
              / "settings_service.py").read_text(encoding="utf-8")
    assert "forget_user" in source


# ---------------------------------------------------------------------------
# Research preferences: one path, one provenance
# ---------------------------------------------------------------------------

def test_editing_fields_here_writes_what_onboarding_writes(client, db):
    user = _me(client, db)
    settings_service.update_research(user, {"fields": ["Medicine"]})

    row = db.get_user_interests(user)[0]
    assert row["value"] == "Medicine"
    assert row["source"] == "explicit_onboarding"
    assert row["confidence"] == 0.9


def test_the_exact_field_choices_survive(client, db):
    """
    The plan calls these out by name: Biology, Biochemistry, Economics and
    Business are separate choices, and a settings page that merged any pair
    would undo the decision onboarding was built around.
    """
    user = _me(client, db)
    picked = ["Agricultural & Biological Sciences",
              "Biochemistry, Genetics & Molecular Biology",
              "Economics, Econometrics & Finance",
              "Business, Management & Accounting"]
    settings_service.update_research(user, {"fields": picked})

    assert sorted(r["value"] for r in db.get_user_interests(user)) == sorted(picked)


def test_one_card_does_not_wipe_another(client, db):
    """
    The page saves a card at a time. A form that omits topics means "I did not
    change topics", not "I have none" — and a single save-everything button
    could not tell the difference.
    """
    user = _me(client, db)
    settings_service.update_research(user, {"topics": ["causal inference"]})
    settings_service.update_research(user, {"fields": ["Medicine"]})

    values = {r["value"] for r in db.get_user_interests(user)}
    assert values == {"causal inference", "Medicine"}


def test_the_route_only_touches_the_card_that_posted(client, db):
    user = _me(client, db)
    settings_service.update_research(user, {"topics": ["causal inference"]})

    client.post("/settings/research", data={"section": "fields", "fields": ["Medicine"]})

    values = {r["value"] for r in db.get_user_interests(user)}
    assert values == {"causal inference", "Medicine"}


def test_settings_does_not_write_interests_directly():
    """
    Two paths into the same table would eventually disagree about provenance or
    validation, and the one thing this data must keep is that an explicit
    answer is recognisably explicit.
    """
    source = (ROOT / "dashboard" / "services"
              / "settings_service.py").read_text(encoding="utf-8")
    assert "record_interest" not in source
    assert "onboarding_service.save_step" in source


def test_an_unfinished_setup_is_offered_a_way_back(client, db):
    _me(client, db)
    body = client.get("/settings").get_data(as_text=True)
    assert "/welcome" in body


# ---------------------------------------------------------------------------
# Appearance: per device, and said so
# ---------------------------------------------------------------------------

def test_appearance_is_not_stored_on_the_account(client, db):
    """
    Nothing about how the page looks reaches the server, so there is nothing
    for one device to overrule on another.
    """
    user = _me(client, db)
    data = settings_service.overview(user, "authenticated")

    assert set(data) == {"identity", "research", "usage"}
    assert not any("theme" in k or "sidebar" in k
                   for k in db.users_by_id[str(user)])


def test_the_page_says_it_is_per_device(client, db):
    """
    Better than letting somebody discover it when their phone changes colour.
    """
    _me(client, db)
    body = client.get("/settings").get_data(as_text=True)
    assert "on this device" in body


def test_the_theme_control_delegates_rather_than_reimplementing(client, db):
    """
    One place decides what "switch theme" means, and it already handles
    storage. Two would drift the moment either changed.
    """
    js = (ROOT / "dashboard" / "static" / "js"
          / "settings.js").read_text(encoding="utf-8")
    assert 'getElementById("theme-toggle")' in js


# ---------------------------------------------------------------------------
# Usage: information, not a control
# ---------------------------------------------------------------------------

def test_usage_is_shown_as_counts_against_limits(client, db):
    user = _me(client, db)
    rows = settings_service.usage(user, "authenticated")

    assert rows
    assert {"used", "limit", "left", "percent"} <= set(rows[0])


def test_the_numbers_are_the_ones_the_application_counts(client, db):
    """
    The same accounting that refuses a request when it runs out, not a second
    tally. A page showing anything else would tell somebody they had room left
    at the moment they were turned away.
    """
    user = _me(client, db)
    quota_service.check_and_consume("agent_query", "authenticated", "user", user)
    quota_service.check_and_consume("agent_query", "authenticated", "user", user)

    truth = quota_service.usage_summary("authenticated", "user", user)
    shown = {r["metric"]: r for r in settings_service.usage(user, "authenticated")}

    assert shown["agent_query"]["used"] == truth["agent_query"]["used"] == 2
    assert shown["agent_query"]["limit"] == truth["agent_query"]["limit"]
    assert shown["agent_query"]["left"] == truth["agent_query"]["limit"] - 2


def test_no_limit_is_ever_a_form_field(client, db):
    """
    A settings page that let somebody raise their own ceiling would not be a
    settings page. The numbers appear as text beside the counts and nowhere
    else.
    """
    _me(client, db)
    body = client.get("/settings").get_data(as_text=True)

    usage = body.split("Usage today")[1].split("</section>")[0]
    assert "usage-row" in usage          # the card really rendered its numbers
    assert "<input" not in usage
    assert "<select" not in usage


def test_nothing_operator_side_is_exposed(client, db):
    """No secrets, no capability assignments, no deployment configuration."""
    _me(client, db)
    body = client.get("/settings").get_data(as_text=True).lower()

    for leak in ("api_key", "client_secret", "database_url", "quotas_enabled",
                 "allow_dev_user_bypass", "capability"):
        assert leak not in body, leak


def test_an_unmetered_deployment_says_so_rather_than_showing_zeros(client, db, monkeypatch):
    """
    "0 of 0" reads as an exhausted allowance, which is the opposite of what an
    unmetered deployment means.
    """
    monkeypatch.setattr(settings_service.quota_service, "usage_summary",
                        lambda *a, **k: {})
    assert settings_service.usage(_me(client, db), "authenticated") == []


def test_a_bar_never_runs_past_its_own_end(client, db, monkeypatch):
    """A limit lowered after somebody has already spent more than it."""
    monkeypatch.setattr(
        settings_service.quota_service, "usage_summary",
        lambda *a, **k: {"agent_query": {"used": 40, "limit": 10}})

    row = settings_service.usage(_me(client, db), "authenticated")[0]
    assert row["percent"] == 100
    assert row["left"] == 0
