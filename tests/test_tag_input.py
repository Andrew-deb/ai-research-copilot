"""
tests/test_tag_input.py — adding entries one at a time.

What this replaced: `MAX_TOPICS = 5` rendered as five empty text boxes, in
onboarding and again in settings. A storage cap presented as a demand, with four
blank rectangles facing somebody who has one project.

The widget posts the same field name the fixed boxes did, so the server needed
no changes — which is the property worth holding onto, and the one these tests
pin down. Everything below is about the rendered contract and the two places
that share it; the browser behaviour (Enter commits, remove deletes) lives in
tag-input.js and is asserted here only as "the page carries the script that does
it".
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "dashboard" / "templates"


def _start_onboarding(client):
    """Step 2 is only reachable once step 1 has been answered."""
    client.get("/welcome")
    client.post("/welcome/1", data={"fields": ["Medicine"]}, follow_redirects=True)
    return client.get("/welcome/2", follow_redirects=True).get_data(as_text=True)


# ---------------------------------------------------------------------------
# The fixed boxes are gone from both places
# ---------------------------------------------------------------------------

def test_onboarding_no_longer_renders_a_row_of_empty_boxes(client, db):
    body = _start_onboarding(client)

    assert 'id="topic-0"' not in body
    assert "topic-rows" not in body
    assert "data-tag-input" in body


def test_settings_uses_the_same_widget(client, db):
    client.get("/dashboard")
    body = client.get("/settings").get_data(as_text=True)

    assert "topic-rows" not in body
    assert "data-tag-input" in body


def test_both_pages_load_the_script_that_runs_it(client, db):
    client.get("/dashboard")
    assert "tag-input.js" in client.get("/settings").get_data(as_text=True)
    assert "tag-input.js" in _start_onboarding(client)


def test_the_stylesheet_has_no_rules_for_the_old_layout():
    """
    Dead CSS for markup nothing emits is how a "fix" turns out to have been
    applied to one of the two pages.
    """
    css = (ROOT / "dashboard" / "static" / "css"
           / "base.css").read_text(encoding="utf-8")
    assert "topic-rows" not in css


# ---------------------------------------------------------------------------
# What it renders
# ---------------------------------------------------------------------------

def test_nothing_is_listed_before_anything_is_added(client, db):
    """
    The whole point. An empty list is hidden rather than rendered as blanks.
    """
    body = _start_onboarding(client)
    assert "tag-list" in body and "hidden" in body
    assert "tag-item" not in body


def test_existing_answers_come_back_as_removable_entries(client, db):
    client.get("/dashboard")
    user = next(iter(db.users_by_id))
    from services import settings_service
    settings_service.update_research(user, {"topics": ["causal inference"]})

    body = client.get("/settings").get_data(as_text=True)

    assert "causal inference" in body
    assert 'name="topics" value="causal inference"' in body
    assert "data-tag-remove" in body


def test_the_cap_is_stated_rather_than_drawn(client, db):
    """
    "Up to 5" as a sentence, not as five rectangles.
    """
    body = _start_onboarding(client)
    assert 'data-max="5"' in body
    assert "Up to 5" in body


def test_entries_post_under_the_field_name_the_server_already_expects(client, db):
    """
    Hidden inputs inside each list item, so the form submits `topics` exactly as
    the five boxes did and nothing server-side had to change.
    """
    widget = (TEMPLATES / "_tag_input.html").read_text(encoding="utf-8")
    assert 'type="hidden" name="{{ name }}"' in widget


# ---------------------------------------------------------------------------
# The script's contract
# ---------------------------------------------------------------------------

def test_enter_is_prevented_from_submitting_the_form():
    """
    In a form with one text input, Enter submits. Without the cancel, the first
    topic somebody typed would save a half-filled form instead of being added.
    """
    js = (ROOT / "dashboard" / "static" / "js"
          / "tag-input.js").read_text(encoding="utf-8")
    assert "preventDefault" in js


def test_an_uncommitted_entry_is_not_silently_dropped():
    """
    Somebody who types a topic and presses Save rather than Enter has still told
    us the topic. Discarding it would be the widget losing an answer.
    """
    js = (ROOT / "dashboard" / "static" / "js"
          / "tag-input.js").read_text(encoding="utf-8")
    assert 'addEventListener("submit"' in js


@pytest.mark.parametrize("behaviour", ["data-tag-remove", "data-tag-commit",
                                       "data-tag-entry", "data-tag-list"])
def test_the_script_and_the_template_agree_on_their_hooks(behaviour):
    """
    Both sides name these attributes; a rename in one file alone would leave a
    widget that renders and does nothing.
    """
    js = (ROOT / "dashboard" / "static" / "js"
          / "tag-input.js").read_text(encoding="utf-8")
    widget = (TEMPLATES / "_tag_input.html").read_text(encoding="utf-8")

    assert behaviour in js
    assert behaviour in widget
