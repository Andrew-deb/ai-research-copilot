"""
tests/test_onboarding.py — five questions, none of them required.

Phase 3.7. What it collects matters less than three properties it has to keep:

**Every step is skippable, and skipping still advances.** The question was put
to somebody and they declined; treating that as "not asked" means asking again,
which is how an optional flow becomes a compulsory one.

**One chip is exactly one field.** A 2026-09-20 decision rejected grouped chips
because these rows carry `confidence = 0.9` — a chip covering two fields would
put a high-confidence interest in somebody's profile that they never chose. A
biochemist who picks Biochemistry must not also be recorded as researching
agriculture.

**Explicit and behavioural evidence are separate rows.** One row per
(user, kind, value, source), so a weak later signal cannot overwrite a
deliberate answer — somebody who said "immunology" and then read three ML
papers does not silently stop being an immunologist.

Deliberately NOT tested, because deliberately not built: anything that READS
these answers. §8g marks the "For You" surface documentation-only for Phase 3.
"""

import pathlib

import pytest

from services import onboarding_service

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# The taxonomy
# ---------------------------------------------------------------------------

def test_the_full_taxonomy_is_twenty_six_fields():
    assert len(onboarding_service.ALL_FIELDS) == 26
    assert len(set(onboarding_service.ALL_FIELDS)) == 26


def test_thirteen_are_shown_and_thirteen_are_behind_the_search():
    assert len(onboarding_service.FIELDS_SHOWN) == 13
    assert len(onboarding_service.FIELDS_MORE) == 13


def test_the_two_halves_do_not_overlap():
    assert not set(onboarding_service.FIELDS_SHOWN) & set(onboarding_service.FIELDS_MORE)


def test_the_shown_fields_are_alphabetical():
    """
    A list with Computer Science at the top tells a nurse what this product
    thinks of their subject.
    """
    assert list(onboarding_service.FIELDS_SHOWN) == sorted(onboarding_service.FIELDS_SHOWN)


def test_every_field_is_reachable_from_the_page(client, db):
    import html

    _me(client, db)
    # Unescaped first: half these names contain "&", which Jinja writes as
    # &amp; — so the raw string would never match its own markup.
    body = html.unescape(client.get("/welcome/1").get_data(as_text=True))

    for field in onboarding_service.ALL_FIELDS:
        assert field in body, field


def test_one_chip_is_one_field(client, db):
    """
    The rejected design had chips covering two fields each. Each input carries
    exactly one value, so ticking one can only ever record one interest.
    """
    user = _me(client, db)
    onboarding_service.save_step(
        user, 1, {"fields": ["Biochemistry, Genetics & Molecular Biology"]})

    stored = [r["value"] for r in db.get_user_interests(user)]
    assert stored == ["Biochemistry, Genetics & Molecular Biology"]


def test_nothing_the_page_did_not_offer_can_be_stored(client, db):
    """
    The form is editable by anybody. An unknown value is dropped rather than
    refused — a wrong row is not worth an error page in somebody's first five
    minutes — but it is never written.
    """
    user = _me(client, db)
    onboarding_service.save_step(
        user, 1, {"fields": ["Computer Science", "Necromancy"]})

    assert [r["value"] for r in db.get_user_interests(user)] == ["Computer Science"]


# ---------------------------------------------------------------------------
# Answers, and what they are worth
# ---------------------------------------------------------------------------

def test_an_onboarding_answer_is_trusted_and_says_where_it_came_from(client, db):
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {"fields": ["Medicine"]})

    row = db.get_user_interests(user)[0]
    assert row["source"] == "explicit_onboarding"
    assert row["confidence"] == 0.9


def test_free_text_topics_are_kept_as_typed(client, db):
    """
    "Causal Inference" is how somebody wrote it and is how it should read back,
    even though it is the same interest as "causal inference".
    """
    user = _me(client, db)
    onboarding_service.save_step(user, 2, {"topics": ["Causal Inference", "causal inference"]})

    stored = [r["value"] for r in db.get_user_interests(user)]
    assert stored == ["Causal Inference"]        # deduplicated, not lowercased


def test_there_is_a_ceiling_on_topics(client, db):
    from exceptions import ValidationError
    with pytest.raises(ValidationError):
        onboarding_service.save_step(
            _me(client, db), 2, {"topics": [f"topic {i}" for i in range(12)]})


def test_re_answering_a_step_replaces_it(client, db):
    """
    Somebody who returns and unticks Chemistry means they are not a chemist.
    An insert-only step would leave them one forever.
    """
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {"fields": ["Chemistry", "Medicine"]})
    onboarding_service.save_step(user, 1, {"fields": ["Medicine"]})

    assert [r["value"] for r in db.get_user_interests(user)] == ["Medicine"]


def test_re_answering_does_not_touch_other_sources(client, db):
    """
    Deselecting a field here must not delete the evidence that they keep
    reading that field's papers — a different claim, made by a different
    mechanism. That is the whole reason source is part of the key.
    """
    user = _me(client, db)
    db.record_interest(user, "field", "Chemistry", "reading_activity", 0.4)
    onboarding_service.save_step(user, 1, {"fields": ["Medicine"]})

    sources = {r["source"] for r in db.get_user_interests(user)}
    assert sources == {"explicit_onboarding", "reading_activity"}


def test_an_explicit_answer_and_a_behavioural_signal_coexist(client, db):
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {"fields": ["Immunology & Microbiology"]})
    db.record_interest(user, "field", "Immunology & Microbiology", "saved_paper", 0.3)

    rows = db.get_user_interests(user)
    assert len(rows) == 2
    assert {r["confidence"] for r in rows} == {0.9, 0.3}


# ---------------------------------------------------------------------------
# Moving through it
# ---------------------------------------------------------------------------

def test_a_refresh_resumes_where_you_stopped(client, db):
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {"fields": ["Medicine"]})
    onboarding_service.save_step(user, 2, {"topics": ["protein folding"]})

    assert onboarding_service.state(user)["next_step"] == 3


def test_skipping_a_step_still_advances(client, db):
    """
    The question was put to them and they answered it by declining. Treating
    that as "not asked" means asking again.
    """
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {})           # nothing ticked

    assert onboarding_service.state(user)["next_step"] == 2
    assert db.get_user_interests(user) == []


def test_finishing_marks_it_done(client, db):
    user = _me(client, db)
    for step in range(1, onboarding_service.TOTAL_STEPS + 1):
        onboarding_service.save_step(user, step, {})

    assert onboarding_service.state(user)["complete"] is True


def test_going_back_does_not_un_answer_later_steps(client, db):
    """
    Somebody revisiting step 2 from a finished flow has not undone steps 3
    to 5.
    """
    user = _me(client, db)
    for step in range(1, 6):
        onboarding_service.save_step(user, step, {})
    onboarding_service.save_step(user, 2, {"topics": ["a topic"]})

    assert onboarding_service.state(user)["step"] == onboarding_service.TOTAL_STEPS


def test_the_completion_time_is_not_rewritten(client, db):
    """
    Changing one answer a year later should not make it look like the account
    was set up yesterday.
    """
    user = _me(client, db)
    for step in range(1, 6):
        onboarding_service.save_step(user, step, {})
    first = db.profiles[str(user)]["onboarding_completed_at"]

    onboarding_service.save_step(user, 3, {"primary_goal": "Just curious"})
    assert db.profiles[str(user)]["onboarding_completed_at"] == first


def test_the_whole_thing_can_be_left(client, db):
    """
    Marked complete rather than abandoned half-done: a flow that reappears
    after being dismissed is not optional, whatever its buttons say.
    """
    user = _me(client, db)
    onboarding_service.skip_all(user)

    assert onboarding_service.state(user)["complete"] is True
    assert onboarding_service.needs_onboarding(user) is False


# ---------------------------------------------------------------------------
# Arriving at it
# ---------------------------------------------------------------------------

def test_a_brand_new_account_is_sent_to_it(client, db):
    user = _me(client, db)
    db.profiles.pop(str(user), None)
    assert onboarding_service.needs_onboarding(user) is True


def test_somebody_who_has_started_is_not_sent_back(client, db):
    """
    Only the FIRST sign-in is intercepted. Being dropped into a form on every
    return is how people learn to click through one without reading it.
    """
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {})

    assert onboarding_service.needs_onboarding(user) is False


def test_an_anonymous_visitor_is_never_sent_to_it():
    """There is nowhere to store the answers of somebody without an account."""
    assert onboarding_service.needs_onboarding(None) is False


def test_an_anonymous_visitor_cannot_open_it(anon_client):
    assert anon_client.get("/welcome").status_code in (302, 401, 403)


def test_a_step_you_have_not_reached_redirects(client, db):
    """A URL anybody can edit should not show step 5 to somebody on step 1."""
    _me(client, db)
    resp = client.get("/welcome/5")

    assert resp.status_code == 302
    assert "/welcome/1" in resp.headers["Location"]


def test_a_nonsense_step_redirects_rather_than_erroring(client, db):
    _me(client, db)
    assert client.get("/welcome/99").status_code == 302


# ---------------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------------

def test_the_flow_works_without_javascript(client, db):
    """
    A first-run flow that needs JavaScript to be completed is one some people
    cannot complete. The only script on the page narrows a long list.
    """
    _me(client, db)
    body = client.get("/welcome/1").get_data(as_text=True)

    assert '<form method="post"' in body
    assert 'name="csrf_token"' in body


def test_the_chips_are_real_checkboxes(client, db):
    """
    Not a div with a click handler: it tabs, toggles with space, submits with
    the form, and is announced as the checkbox it is.
    """
    _me(client, db)
    body = client.get("/welcome/1").get_data(as_text=True)
    assert 'type="checkbox" name="fields"' in body


def test_leaving_is_its_own_form(client, db):
    """
    Outside the answer form, so pressing Enter in a text field never abandons
    onboarding by accident.
    """
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {})     # step 2 is unreachable before this

    body = client.get("/welcome/2").get_data(as_text=True)
    assert 'id="onboarding-skip-all"' in body


def test_previous_answers_come_back(client, db):
    user = _me(client, db)
    onboarding_service.save_step(user, 1, {"fields": ["Medicine"]})

    body = client.get("/welcome/1").get_data(as_text=True)
    chip = body.split('value="Medicine"')[1][:40]
    assert "checked" in chip


def test_nothing_reads_these_answers_yet():
    """
    §8g marks the "For You" surface documentation-only for Phase 3. Collecting
    first is the order: an empty interest table makes personalisation
    impossible to build, and a personalisation surface over no data is
    impossible to judge.
    """
    services = (ROOT / "dashboard" / "services").glob("*.py")
    readers = [s.name for s in services
               if "get_user_interests" in s.read_text(encoding="utf-8")
               and s.name != "onboarding_service.py"]
    assert readers == [], readers
