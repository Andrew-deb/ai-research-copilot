"""
tests/test_notes_notepad.py — the docked panel, the notepad, and note titles.

Three changes, agreed from a prototype:

**Docked, not floating.** The panel takes its own column and `.main-col` gives
up that width, exactly as the sidebar has always worked. It was an overlay with
a scrim, and a scrim is a statement that the page behind it is unusable until
you deal with this — the opposite of what a notes panel is for. You are meant
to keep reading the paper you are writing about.

**Two views, one at a time.** Recent notes first; the + at the top left swaps
them for a notepad. The notepad REPLACES the list rather than appearing beneath
it, so the writing surface gets the whole panel and there is only ever one
thing to read. Nothing is written until Save.

**Titles.** Optional, because demanding one would turn quick capture into a
two-field form, and a note jotted in three words needs no name.
"""

import pathlib
import re

import pytest

from services import progress_service

ROOT = pathlib.Path(__file__).resolve().parents[1]
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _css() -> str:
    return (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")


def _js(name: str) -> str:
    return (ROOT / "dashboard" / "static" / "js" / name).read_text(encoding="utf-8")


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# Docked
# ---------------------------------------------------------------------------

def test_the_page_gives_up_width_instead_of_being_covered():
    css = _css()
    assert "body.notes-docked .main-col" in css
    assert "margin-right: var(--notes-w)" in css


def test_the_panel_sits_on_the_sidebar_s_layer_not_a_modal_s():
    """
    A modal layer would be a claim that nothing behind it matters. The panel is
    furniture, like the sidebar, so it shares its z-index.
    """
    rule = re.search(r"\.notes-panel\s*\{([^}]*)\}", _css()).group(1)
    assert "z-index: 40" in rule


def test_nothing_dims_the_page():
    """
    Scoped to the notes scrim: `.sidebar-scrim` is a different thing and still
    correct, because the mobile sidebar genuinely does cover the page.
    """
    assert "notes-panel-scrim" not in _css()

    # Comments stripped first: the file explains at length why there is no
    # scrim, and a test that cannot tell code from the prose about it fails for
    # the wrong reason.
    code = re.sub(r"/\*.*?\*/", "", _js("notes-panel.js"), flags=re.S)
    code = re.sub(r"//.*", "", code)
    assert "scrim" not in code


def test_a_narrow_screen_stops_pretending_to_dock():
    """392px of panel beside 0px of paper is not a split worth keeping."""
    # Found by content, not by position. There are several 900px blocks — one
    # collapses the sidebar, another hides the resize handles — and picking the
    # first or the last only works until somebody adds another.
    blocks = _css().split("@media (max-width: 900px)")[1:]
    narrow = next((b for b in blocks if ".notes-panel" in b[:400]), "")

    assert narrow, "no 900px block governs the notes panel"
    assert "width: 100vw" in narrow[:400]
    assert "margin-right: 0" in narrow[:400]


def test_the_panel_stays_open_across_pages():
    """
    Docked is a working arrangement rather than a dialog. Closing itself on
    every navigation would defeat the one thing it is for — keeping notes to
    hand while you move around.
    """
    js = _js("notes-panel.js")
    assert "localStorage" in js
    assert "remembered()" in js


def test_reading_the_stored_state_cannot_break_the_panel():
    """localStorage throws outright in some privacy modes."""
    js = _js("notes-panel.js")
    remembered = js.split("function remembered()")[1][:220]
    assert "catch" in remembered


# ---------------------------------------------------------------------------
# Two views
# ---------------------------------------------------------------------------

def test_the_panel_opens_on_the_list(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="notes-view-list"' in body
    assert 'id="notes-view-pad"' in body
    assert re.search(r'id="notes-view-pad"[^>]*hidden', body)


def test_the_add_and_back_controls_share_one_slot():
    """
    So the control you want is always in the same place rather than moving
    depending on which view you are in.
    """
    js = _js("notes-panel.js")
    for line in ('newBtn.hidden = false', 'backBtn.hidden = true',
                 'newBtn.hidden = true', 'backBtn.hidden = false'):
        assert line in js, line


def test_the_notepad_replaces_the_list_rather_than_joining_it():
    js = _js("notes-panel.js")
    pad = js.split("function showPad(")[1][:300]
    assert "listView.hidden = true" in pad
    assert "padView.hidden = false" in pad


def test_escape_does_not_throw_away_writing():
    """
    Escape closes the panel from the list. Doing so from inside the notepad
    would discard whatever had been typed, with no way back.
    """
    js = _js("notes-panel.js")
    assert "padView.hidden" in js.split('e.key === "Escape"')[1][:160]


def test_discarding_asks_only_when_there_is_something_to_lose():
    """
    Asked against `dirty()` rather than against whether the box has any text:
    typing a word and deleting it again leaves nothing to lose, and a prompt
    about it is one nobody can answer sensibly.
    """
    for name in ("notes-panel.js", "notes-page.js"):
        block = _js(name).split("Discard this note?")[0][-220:]
        assert "dirty()" in block, name


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def test_a_note_can_be_named(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "the body", "A name")
    assert note["title"] == "A name"


def test_a_title_is_optional(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "the body")
    assert note["title"] is None


def test_an_emptied_title_clears_rather_than_storing_blank(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "the body", "A name")
    updated = progress_service.update_note(user, note["note_id"], "the body", "   ")
    assert updated["title"] is None


def test_an_untitled_note_falls_back_to_its_first_line():
    """
    A column of "Untitled" tells the reader nothing about which note is which.
    The first line is what they were going to skim anyway.
    """
    shown = progress_service.display_title(
        {"title": None, "note_text": "The retriever is the weak point.\nSecond line."})
    assert shown == "The retriever is the weak point."


def test_a_long_first_line_is_cut_rather_than_wrapped():
    shown = progress_service.display_title({"title": None, "note_text": "word " * 60})
    assert len(shown) <= 80
    assert shown.endswith("…")


def test_an_overlong_title_is_refused(client, db):
    from exceptions import ValidationError
    with pytest.raises(ValidationError):
        progress_service.save_note(_me(client, db), None, "body", "x" * 400)


def test_the_paper_s_title_is_not_mistaken_for_the_note_s(client, db):
    """
    Both columns are called `title`. Unaliased, one silently shadows the other
    and every note appears to be named after the paper it is attached to.
    """
    paper = db.seed_paper(title="Attention Is All You Need")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "a note", None)

    group = client.get("/notes", headers=XHR).get_json()["papers"][0]
    assert group["title"] == "Attention Is All You Need"     # the paper
    assert group["notes"][0]["title"] is None                 # the note


def test_a_title_survives_the_round_trip(client, db):
    _me(client, db)
    client.post("/notes", json={"note_text": "body", "title": "Named"}, headers=XHR)

    body = client.get("/notes").get_data(as_text=True)
    assert "Named" in body


# ---------------------------------------------------------------------------
# The page follows the panel
# ---------------------------------------------------------------------------

def test_the_page_has_the_same_two_views(client, db):
    body = client.get("/notes").get_data(as_text=True)
    assert 'id="page-view-list"' in body
    assert 'id="page-view-pad"' in body
    assert 'id="page-note-new"' in body


def test_the_page_notepad_is_not_hidden_by_markup():
    """
    Collapsed at runtime instead, which is what keeps the page working without
    JavaScript: no JS means both views show, not that the + does nothing.
    """
    markup = (ROOT / "dashboard" / "templates" / "notes.html").read_text(encoding="utf-8")
    pad = re.search(r'<form class="notes-view notes-page-pad"[^>]*>', markup).group(0)
    assert "hidden" in pad          # the markup does hide it...

    js = _js("notes-page.js")
    assert "padView.hidden = true" in js    # ...and JS is what manages it after


def test_editing_retargets_the_form_only_on_submit():
    """
    The form posts to the create endpoint. Retargeting when the notepad OPENS
    would leave a discarded edit pointing at that note, and the next new note
    would overwrite it.
    """
    js = _js("notes-page.js")
    submit = js.split('padView.addEventListener("submit"')[1][:900]
    assert 'setAttribute("action"' in submit
