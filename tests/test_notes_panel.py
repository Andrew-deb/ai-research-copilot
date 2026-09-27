"""
tests/test_notes_panel.py — writing a note without leaving the page.

The pen in the header opens a slide-over. The point is that capture costs
nothing: you are reading a paper or halfway through a conversation, a thought
arrives, and recording it must not mean navigating away and finding your place
again. So it opens OVER the page — a modal or a route change would remove the
very thing you are writing about.

Beside it sits the book, because "how do citations work" is the other question
people ask while doing something else, and it was reachable only from the public
sidebar that signed-in visitors never see.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _shell() -> str:
    return (ROOT / "dashboard" / "templates" / "base.html").read_text(encoding="utf-8")


def _panel_js() -> str:
    return (ROOT / "dashboard" / "static" / "js"
            / "notes-panel.js").read_text(encoding="utf-8")


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# The two header controls
# ---------------------------------------------------------------------------

def test_both_controls_are_in_the_header(client, db):
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="notes-panel-toggle"' in body
    assert 'href="/help"' in body


def test_they_sit_in_the_order_pen_book_theme(client, db):
    """
    Asked for in that order, and the theme toggle stays rightmost — it is the
    one control that belongs to the window rather than to the work.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    actions = body.split('class="topbar-actions"')[1].split("</header>")[0]

    pen = actions.index("notes-panel-toggle")
    book = actions.index('href="/help"')
    theme = actions.index('id="theme-toggle"')

    assert pen < book < theme


def test_a_signed_out_visitor_gets_neither(anon_client):
    """
    Anonymous visitors cannot write a note, and they already reach help from
    the public sidebar. Two controls that either refuse or duplicate.
    """
    body = anon_client.get("/dashboard").get_data(as_text=True)
    assert 'id="notes-panel-toggle"' not in body
    assert 'id="notes-panel"' not in body


def test_the_public_sidebar_keeps_its_help_link(anon_client):
    """The header icons are for the workspace shell; the public one is unchanged."""
    body = anon_client.get("/about").get_data(as_text=True)
    assert 'href="/help"' in body


# ---------------------------------------------------------------------------
# The panel reads the same notes the page does
# ---------------------------------------------------------------------------

def test_the_panel_and_the_page_share_one_route(client, db):
    """
    A second endpoint would be a second place to remember when the shape
    changes, and two sources that can disagree about what you have written.
    """
    paper = db.seed_paper(title="A Paper")
    db.save_note(_me(client, db), str(paper["paper_id"]), "a thought")

    data = client.get("/notes", headers=XHR).get_json()
    assert data["papers"][0]["notes"][0]["note_text"] == "a thought"
    assert data["summary"]["notes"] == 1


def test_the_page_itself_still_renders_html(client, db):
    resp = client.get("/notes")
    assert "text/html" in resp.headers["Content-Type"]
    assert "Your notes" in resp.get_data(as_text=True)


def test_the_panel_sees_only_your_notes(client, db):
    paper = db.seed_paper(title="A Paper")
    _me(client, db)
    stranger = db.get_or_create_user("someone@example.com", "Someone")
    db.save_note(stranger["user_id"], str(paper["paper_id"]), "not yours")

    data = client.get("/notes", headers=XHR).get_json()
    assert data["papers"] == []


# ---------------------------------------------------------------------------
# Attaching to what you are reading
# ---------------------------------------------------------------------------

def test_a_paper_page_offers_something_to_attach_to(client, db):
    """
    The panel reads the paper off the page rather than being told about it, so
    any page showing a paper gets this without the panel knowing which pages
    those are.
    """
    paper = db.seed_paper(title="Attention Is All You Need")
    body = client.get(f"/paper/{paper['paper_id']}").get_data(as_text=True)

    assert f'data-paper-id="{paper["paper_id"]}"' in body
    assert 'data-paper-title="Attention Is All You Need"' in body


def test_the_panel_attaches_by_default_and_can_detach():
    js = _panel_js()
    assert "attachToCurrentPaper" in js
    assert "notes-pad-detach" in js


def test_a_note_can_be_written_without_a_paper(client, db):
    """Detached, or written from a page that has no paper at all."""
    _me(client, db)
    resp = client.post("/notes", json={"note_text": "a loose thought"}, headers=XHR)

    assert resp.status_code == 200
    assert client.get("/notes", headers=XHR).get_json()["papers"][0]["paper_id"] is None


def test_a_note_written_from_a_paper_lands_on_it(client, db):
    paper = db.seed_paper(title="A Paper")
    _me(client, db)

    client.post("/notes", json={"note_text": "about this one",
                                "paper_id": str(paper["paper_id"])}, headers=XHR)

    groups = client.get("/notes", headers=XHR).get_json()["papers"]
    assert groups[0]["paper_id"] == str(paper["paper_id"])


# ---------------------------------------------------------------------------
# Behaving like a panel
# ---------------------------------------------------------------------------

def test_the_panel_is_rendered_but_costs_nothing_unopened(client, db):
    """
    Markup on every workspace page so the pen works everywhere, and the list
    left empty until it is opened — the notes are fetched on first open, not on
    every page load.
    """
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="notes-panel"' in body
    assert re.search(r'id="notes-panel-list"[^>]*>\s*</div>', body)


def test_hidden_is_not_left_to_the_browser():
    """
    `.notes-panel` sets `display: flex`, which beats the browser's own
    `[hidden] { display: none }` — the same rule that once left a renamed
    conversation's old title on screen beside the field replacing it.
    """
    css = (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")
    assert ".notes-panel[hidden]" in css


def test_the_page_gives_up_the_width_rather_than_being_covered():
    """
    This replaces a test about waiting for a slide-out transition. There is no
    transition now, because there is nothing to slide over: the panel is a
    column and `.main-col` carries a margin the same width, exactly as the
    sidebar has always worked.

    That is the whole behaviour change — an overlay with a scrim says the page
    behind it is unusable until you deal with this, which is the opposite of
    what a notes panel is for.
    """
    css = (ROOT / "dashboard" / "static" / "css" / "base.css").read_text(encoding="utf-8")

    assert "body.notes-docked .main-col" in css
    assert "margin-right: var(--notes-w)" in css
    assert "notes-panel-scrim" not in css          # the scrim is gone entirely

    js = _panel_js()
    assert 'classList.add("notes-docked")' in js


def test_escape_closes_it():
    assert 'e.key === "Escape"' in _panel_js()


def test_closing_does_not_strand_the_keyboard():
    """
    Hiding the element focus is inside drops focus to the top of the document.
    Docking never steals focus on open — the page keeps working underneath —
    so this is the only direction that needs handling, and it is handled before
    the panel is hidden rather than after.
    """
    js = _panel_js()
    assert "panel.contains(document.activeElement)" in js
    assert js.index("toggle.focus()") < js.index("panel.hidden = true;\n    document.body")


def test_note_text_is_escaped_before_it_is_rendered():
    """
    The panel builds its list with innerHTML, and a note is text the person
    typed. Interpolating it raw would execute whatever they wrote.
    """
    js = _panel_js()
    assert "escapeHtml" in js
    assert "escapeHtml(note.note_text)" in js
    assert "escapeHtml(group.title)" in js
