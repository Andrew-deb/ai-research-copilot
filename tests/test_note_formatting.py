"""
tests/test_note_formatting.py — bold, italic and headings in the notepad.

**A Markdown toolbar, not a rich-text editor**, and the choice is forced rather
than preferred. Notes are stored as Markdown, searched through a tsvector built
from the raw text, exported as Markdown, and read back by an agent that handles
Markdown well. A contenteditable surface would store HTML and break all four,
and would need its own sanitising on the way in as well as the way out.

So the field stays a plain `<textarea>` and the buttons type into it — which
also means the notepad keeps working with the script absent: the buttons go,
the typing does not. Anyone who already knows the syntax can ignore the bar;
anyone who does not can press a button and see what it wrote.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _js() -> str:
    return (ROOT / "dashboard" / "static" / "js"
            / "notes.js").read_text(encoding="utf-8")

def _handler(js: str) -> str:
    """
    The formatting keydown listener, on its own.

    Sliced to the listener's own closing line rather than the first `});`, which
    also closes the `apply({...})` calls inside it, and rather than a character
    count, which silently cut the italic branch off when the handler grew.
    """
    body = js.split("The two shortcuts")[1]
    return body.split("\n  });")[0]



def _partial() -> str:
    return (ROOT / "dashboard" / "templates"
            / "_note_toolbar.html").read_text(encoding="utf-8")


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# What it writes
# ---------------------------------------------------------------------------

def test_the_field_is_still_a_textarea(client, db):
    """
    The whole reason the toolbar writes Markdown. A contenteditable would store
    HTML, and the search index, the export and the agent all read the raw text.
    """
    body = client.get("/notes").get_data(as_text=True)
    assert re.search(r'<textarea[^>]*id="page-pad-body"', body)
    assert "contenteditable" not in body


def test_both_notepads_offer_the_same_formatting(client, db):
    """One partial, so the page and the panel cannot drift apart."""
    body = client.get("/notes").get_data(as_text=True)
    assert body.count('class="note-format"') == 2       # page pad + panel pad

    for field in ("page-pad-body", "notes-pad-body"):
        assert f'data-format-for="{field}"' in body


def test_it_covers_what_a_research_note_needs():
    partial = _partial()
    for action in ("bold", "italic", "code", "h1", "h2", "bullet", "number", "quote"):
        assert f'data-format="{action}"' in partial, action


def test_every_button_has_a_name():
    """
    A toolbar of single letters and glyphs is announced as "B I <> H1" without
    them.
    """
    partial = _partial()
    buttons = re.findall(r"<button[^>]*data-format=[^>]*>", partial)
    assert buttons
    for button in buttons:
        assert "aria-label=" in button, button


# ---------------------------------------------------------------------------
# How it behaves
# ---------------------------------------------------------------------------

def test_pressing_bold_twice_unwraps():
    """What every editor does, and what the second press is obviously for."""
    js = _js()
    assert "before === mark && after === mark" in js


def test_the_caret_lands_inside_the_marks():
    """
    With nothing selected, a caret left AFTER the closing mark means the next
    keystroke escapes the formatting just applied.
    """
    js = _js()
    block = js.split("field.value = value.slice(0, start) + mark")[1][:400]
    assert "select(start + mark.length" in block


def test_line_prefixes_take_the_whole_line():
    """
    A heading applied to three characters in the middle of a sentence is not a
    heading. The selection widens to the lines it touches first.
    """
    js = _js()
    assert 'value.lastIndexOf("\\n", start - 1) + 1' in js


def test_a_numbered_list_counts():
    js = _js()
    assert "action.numbered ? (i + 1)" in js


def test_applying_a_prefix_twice_removes_it():
    js = _js()
    assert "line.indexOf(prefix) === 0" in js
    assert "line.slice(prefix.length)" in js


def test_the_word_count_and_the_unsaved_guard_both_notice():
    """
    Neither fires for a value set from script, so a formatted note would look
    unedited to the guard and uncounted to the word count.
    """
    js = _js()
    done = js.split("function done()")[1][:320]
    assert 'new Event("input", { bubbles: true })' in done


def test_the_two_shortcuts_people_press_without_being_told():
    """
    Asked of the registry now, so they can be reassigned in Settings. The
    literal keys moved there; what stays here is that both are still wired.
    """
    js = _js()
    shortcuts = _handler(js)
    assert 'keysFor("note.bold")' in shortcuts
    assert 'keysFor("note.italic")' in shortcuts


def test_the_shortcuts_still_work_without_the_registry():
    """
    A formatting key is not worth losing to a script that failed to fetch, so
    the hardcoded pair survives as a fallback behind the lookup.
    """
    js = _js()
    shortcuts = _handler(js)
    assert '"b"' in shortcuts and '"i"' in shortcuts


def test_the_shortcuts_do_not_steal_other_combinations():
    """
    Ctrl+Shift+B and Ctrl+Alt+I belong to the browser, not to this. The registry
    path gets this from `matches`, which compares every modifier including the
    ones a chord does not ask for; the fallback checks them itself.
    """
    js = _js()
    guard = _handler(js)
    assert "e.shiftKey" in guard and "e.altKey" in guard

    registry = (ROOT / "dashboard" / "static" / "js"
                / "shortcuts.js").read_text(encoding="utf-8")
    assert "event.shiftKey === want.shift" in registry


# ---------------------------------------------------------------------------
# Without the script
# ---------------------------------------------------------------------------

def test_the_notepad_still_works_with_no_javascript(client, db):
    """
    The buttons go; the typing does not. A rich-text surface would have taken
    the field with it.
    """
    body = client.get("/notes").get_data(as_text=True)

    # The whole tag, not the text either side of the id — the attributes are in
    # whatever order the template happens to write them.
    tag = re.search(r'<form[^>]*id="page-view-pad"[^>]*>', body)
    assert tag, "the notepad form is not a form"
    assert 'method="post"' in tag.group(0)
    assert 'action="' in tag.group(0)

    pad = body.split('id="page-view-pad"')[1].split("</form>")[0]
    assert 'name="note_text"' in pad


def test_the_toolbar_is_wired_from_the_markup():
    """
    Found by attribute rather than by a hard-coded pair of ids, so a third
    notepad added later needs no JavaScript change.
    """
    js = _js()
    assert 'querySelectorAll("[data-format-for]")' in js
