"""
tests/test_command_palette.py — one box that finds anything, from anywhere.

The palette answers two questions with one field, because from the person's
side they are one question — "get me to the thing I am thinking of":

  * **where** — Dashboard, Collections, the notes page;
  * **what** — a paper, a note, a collection, a past conversation.

Two properties matter more than the features:

**Everything behind it is a keyword lookup.** It runs on every keystroke, so a
semantic search there would be slow, metered, and would spend somebody's search
quota on navigating their own application.

**Capability decides what is searchable, not tier checks scattered through the
UI.** An anonymous visitor has no notes and no history, so those sections do
not exist for them rather than returning empty ones.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _js() -> str:
    return (ROOT / "dashboard" / "static" / "js"
            / "palette.js").read_text(encoding="utf-8")


def _sections(client, q=""):
    return client.get(f"/command?q={q}", headers=XHR).get_json()["sections"]


def _titles(sections):
    return [s["title"] for s in sections]


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# Getting somewhere
# ---------------------------------------------------------------------------

def test_an_empty_palette_offers_somewhere_to_go(client, db):
    """
    Opening it with nothing typed is a menu. Running every search for an empty
    string would return the newest of everything, which answers no question
    anybody asked.
    """
    sections = _sections(client)
    assert _titles(sections) == ["Go to"]
    assert any(i["label"] == "Dashboard" for i in sections[0]["items"])


def test_destinations_come_before_anything_found(client, db):
    """
    Somebody typing "coll" almost certainly wants the Collections page, not a
    paper with "collaborative" in its title, and being wrong costs one more
    keystroke.
    """
    db.seed_paper(title="Collaborative filtering at scale")
    _me(client, db)

    assert _titles(_sections(client, "coll"))[0] == "Go to"


def test_a_page_is_findable_by_what_it_holds(client, db):
    """
    Somebody looking for their library types "library". The page is called
    Collections.
    """
    _me(client, db)
    items = _sections(client, "library")[0]["items"]
    assert any(i["label"] == "Collections" for i in items)


def test_a_signed_out_visitor_is_not_sent_somewhere_empty(anon_client):
    labels = [i["label"] for i in _sections(anon_client)[0]["items"]]
    assert "Notes" not in labels
    assert "Progress" not in labels
    assert "Search" in labels


# ---------------------------------------------------------------------------
# Finding things
# ---------------------------------------------------------------------------

def test_papers_are_searchable(client, db):
    db.seed_paper(title="Retrieval-Augmented Generation")
    _me(client, db)

    sections = _sections(client, "retrieval")
    papers = next(s for s in sections if s["title"] == "Papers")
    assert papers["items"][0]["label"] == "Retrieval-Augmented Generation"


def test_notes_are_searchable_when_you_have_any(client, db):
    from services import progress_service
    progress_service.save_note(_me(client, db), None, "the retriever is the weak point")

    assert "Notes" in _titles(_sections(client, "retriever"))


def test_an_anonymous_visitor_has_no_notes_section(anon_client, db):
    """Not an empty section — no section. They cannot have notes at all."""
    assert "Notes" not in _titles(_sections(anon_client, "anything"))
    assert "Conversations" not in _titles(_sections(anon_client, "anything"))


def test_collections_are_searchable(client, db):
    user = _me(client, db)
    db.create_collection(user, "Transformer Foundations")

    sections = _sections(client, "transformer")
    assert "Collections" in _titles(sections)


def test_an_empty_section_is_never_shown(client, db):
    """A heading over nothing is a promise the palette did not keep."""
    _me(client, db)
    for section in _sections(client, "zzzznothingmatchesthis"):
        assert section["items"], section["title"]


def test_nobody_finds_somebody_elses_notes(client, db):
    _me(client, db)
    stranger = db.get_or_create_user("someone@example.com", "Someone")
    db.save_note(stranger["user_id"], None, "not yours to find", None, [])

    sections = _sections(client, "yours")
    assert "Notes" not in _titles(sections)


def test_one_broken_section_does_not_take_the_palette_down(client, db, monkeypatch):
    """
    This runs on every keystroke. A slow or failing lookup should cost its own
    results and nothing else — least of all the navigation, which is the half
    people use most.
    """
    from repositories import lakebase

    def explode(*a, **k):
        raise RuntimeError("index is rebuilding")

    monkeypatch.setattr(lakebase, "search_papers_by_text", explode)
    _me(client, db)

    # A query that matches a destination, so the surviving half is visible.
    sections = _sections(client, "collections")
    assert "Go to" in _titles(sections)
    assert "Papers" not in _titles(sections)


# ---------------------------------------------------------------------------
# What it costs
# ---------------------------------------------------------------------------

def test_the_palette_is_not_metered(client, db):
    """
    Metering navigation would spend somebody's search allowance on finding
    their way around their own application.
    """
    _me(client, db)
    before = len(db.ai_operations)

    for _ in range(5):
        _sections(client, "retrieval")

    assert len(db.ai_operations) == before


def test_nothing_behind_it_is_a_semantic_search():
    """An embedding call per keystroke would be slow, metered, and pointless."""
    source = (ROOT / "dashboard" / "services"
              / "command_service.py").read_text(encoding="utf-8")
    # Comments and docstrings stripped: the module explains at length WHY it is
    # not semantic, and a test that cannot tell code from the prose about it
    # fails on the explanation.
    code = re.sub(r'"""[\s\S]*?"""', "", source)
    code = re.sub(r"#.*", "", code)

    assert "semantic" not in code.lower()
    assert "embedding" not in code.lower()


# ---------------------------------------------------------------------------
# Reaching it
# ---------------------------------------------------------------------------

def test_it_is_on_every_page(client, db):
    for path in ("/dashboard", "/collections", "/notes", "/goals"):
        body = client.get(path).get_data(as_text=True)
        assert 'id="palette"' in body, path


def test_the_chat_page_keeps_its_own_control(client, db):
    """
    That slot already holds the agent/search toggle there. The palette stays
    reachable from the keyboard, which is how it is mostly opened anyway.
    """
    body = client.get("/chat").get_data(as_text=True)
    assert 'id="palette-open"' not in body      # no trigger in the slot
    assert 'id="palette"' in body               # but the dialog is there


def test_the_shortcut_is_the_one_people_already_press():
    js = _js()
    assert "metaKey" in js and "ctrlKey" in js
    assert '"k"' in js


def test_a_stale_answer_cannot_overwrite_a_newer_one():
    """
    Typing is faster than the network. Without a sequence check the results for
    "ret" can land after those for "retriever" and replace them.
    """
    js = _js()
    assert "sequence" in js
    assert "mine !== sequence" in js


def test_enter_goes_somewhere_straight_after_typing():
    """The first row is selected on render, or Enter does nothing."""
    assert "move(0)" in _js()


def test_the_mouse_and_the_keyboard_agree():
    """
    Hover moves the selection, so there is never a highlighted row and a
    different row that Enter would take.
    """
    assert 'results.addEventListener("mousemove"' in _js()


def test_closing_gives_focus_back():
    """A dialog that drops focus at the top of the document costs more than the
    shortcut saves."""
    js = _js()
    assert "lastFocused" in js


def test_result_text_is_escaped():
    """Paper titles and note text are data, and the list is built with innerHTML."""
    js = _js()
    assert "escapeHtml(item.label)" in js
    assert "escapeHtml(section.title)" in js
