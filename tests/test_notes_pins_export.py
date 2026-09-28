"""
tests/test_notes_pins_export.py — the note you keep coming back to, and leaving
with your work.

**Pinning** answers a question search cannot: "the one I am working from this
week" is not distinguished by its words, so no query finds it. Per note rather
than per paper, because what you return to is a particular thought and not
everything you ever wrote about one study.

It orders rather than adding a section. A pinned note sorts first, which floats
its paper's group to the top and puts the note at the head of it, so the page
keeps one structure instead of growing a second list above it that the grouping
does not apply to.

**Export** is Markdown, and it exports what the page is SHOWING — filters
included. An export that quietly handed back everything would mean something
different from the list in front of you, which is the kind of surprise that
costs trust in an export.
"""

import pathlib
import re

import pytest

from exceptions import PaperNotFoundError
from services import progress_service

ROOT = pathlib.Path(__file__).resolve().parents[1]
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------

def test_a_note_can_be_pinned(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "the one I keep opening")

    pinned = progress_service.set_pinned(user, note["note_id"], True)
    assert pinned["pinned"] is True


def test_it_can_be_unpinned_again(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "body")

    progress_service.set_pinned(user, note["note_id"], True)
    assert progress_service.set_pinned(user, note["note_id"], False)["pinned"] is False


def test_a_stranger_cannot_pin_your_note(client, db):
    me = _me(client, db)
    stranger = db.get_or_create_user("someone@example.com", "Someone")
    theirs = db.save_note(stranger["user_id"], None, "not yours", None, [])

    with pytest.raises(PaperNotFoundError):
        progress_service.set_pinned(me, theirs["note_id"], True)


def test_the_wanted_state_is_sent_rather_than_a_toggle(client, db):
    """
    Two tabs showing the same note would each flip from their own stale idea of
    it and land on whichever request arrived last.
    """
    user = _me(client, db)
    note = progress_service.save_note(user, None, "body")

    # Sending true twice leaves it pinned, rather than flipping it back.
    for _ in range(2):
        client.post(f"/notes/{note['note_id']}/pin", json={"pinned": True}, headers=XHR)
    assert db.notes[str(note["note_id"])]["pinned"] is True


def test_a_pinned_note_comes_first(client, db):
    user = _me(client, db)
    progress_service.save_note(user, None, "written later")
    old = progress_service.save_note(user, None, "written earlier")
    progress_service.set_pinned(user, old["note_id"], True)

    groups = progress_service.all_notes(user)
    assert groups[0]["notes"][0]["note_text"] == "written earlier"


def test_a_pin_outranks_search_relevance(client, db):
    """
    A pinned note is one the person has already said matters, which is a
    stronger signal than any ranking function.
    """
    source = (ROOT / "dashboard" / "repositories"
              / "lakebase.py").read_text(encoding="utf-8")
    order = source.split("order = (")[1][:220]
    assert order.index("pinned DESC") < order.index("ts_rank")


def test_the_filter_can_show_only_pinned_notes(client, db):
    user = _me(client, db)
    progress_service.save_note(user, None, "ordinaryone")
    kept = progress_service.save_note(user, None, "keptone")
    progress_service.set_pinned(user, kept["note_id"], True)

    body = client.get("/notes?scope=pinned").get_data(as_text=True)
    assert "keptone" in body
    assert "ordinaryone" not in body


def test_a_pinned_note_is_marked_without_hovering(client, db):
    """
    The actions only appear on hover, so the pin state needs to be visible in
    the row itself or it is invisible until you go looking.
    """
    user = _me(client, db)
    note = progress_service.save_note(user, None, "body", "A title")
    progress_service.set_pinned(user, note["note_id"], True)

    assert "note-pin-mark" in client.get("/notes").get_data(as_text=True)


def test_pinning_works_without_javascript(client, db):
    """A real form posting to a real endpoint, like delete beside it."""
    progress_service.save_note(_me(client, db), None, "something to pin")

    body = client.get("/notes").get_data(as_text=True)
    form = re.search(r'<form[^>]*action="[^"]*/pin"[^>]*>(.*?)</form>', body, re.S)

    assert form, "no pin form on the page"
    assert 'name="csrf_token"' in form.group(1)
    assert 'name="pinned"' in form.group(1)


def test_the_panel_can_pin_too():
    js = (ROOT / "dashboard" / "static" / "js"
          / "notes-panel.js").read_text(encoding="utf-8")
    assert "data-panel-pin" in js
    assert "/pin" in js


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_notes_come_back_as_a_markdown_file(client, db):
    progress_service.save_note(_me(client, db), None, "the retriever is the weak point")

    resp = client.get("/notes/export")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["Content-Type"]
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "notes.md" in resp.headers["Content-Disposition"]


def test_the_body_survives_verbatim(client, db):
    """
    It was written as Markdown and stays that way, so a list is still a list on
    the other side.
    """
    progress_service.save_note(_me(client, db), None, "- first\n- second")

    text = client.get("/notes/export").get_data(as_text=True)
    assert "- first\n- second" in text


def test_what_a_note_is_about_travels_with_it(client, db):
    """
    "The retriever is the weak point" is close to meaningless once separated
    from the paper it was written against.
    """
    paper = db.seed_paper(title="Attention Is All You Need", venue="NeurIPS")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "a note", None, [])

    text = client.get("/notes/export").get_data(as_text=True)
    assert "## Attention Is All You Need" in text
    assert "NeurIPS" in text


def test_titles_tags_and_pins_are_carried(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "body", "A name", "methods")
    progress_service.set_pinned(user, note["note_id"], True)

    text = client.get("/notes/export").get_data(as_text=True)
    assert "### A name" in text
    assert "#methods" in text
    assert "pinned" in text


def test_the_export_follows_the_filter(client, db):
    """
    Search for "retriever", press export, and you get those notes. Handing back
    everything would make the button mean something different from the page.
    """
    user = _me(client, db)
    progress_service.save_note(user, None, "retriever ablations")
    progress_service.save_note(user, None, "unrelated thought")

    text = client.get("/notes/export?q=retriever").get_data(as_text=True)
    assert "retriever ablations" in text
    assert "unrelated thought" not in text


def test_the_export_link_carries_the_current_filter(client, db):
    progress_service.save_note(_me(client, db), None, "retriever ablations")

    body = client.get("/notes?q=retriever").get_data(as_text=True)
    export = re.search(r'href="(/notes/export[^"]*)"', body)
    assert export and "q=retriever" in export.group(1)


def test_an_empty_export_is_still_a_file(client, db):
    """Nothing to export is not an error; it is an empty notebook."""
    _me(client, db)
    resp = client.get("/notes/export")

    assert resp.status_code == 200
    assert "# Notes" in resp.get_data(as_text=True)


def test_nobody_exports_somebody_elses_notes(client, db):
    _me(client, db)
    stranger = db.get_or_create_user("someone@example.com", "Someone")
    db.save_note(stranger["user_id"], None, "not yours to download", None, [])

    assert "not yours to download" not in client.get("/notes/export").get_data(as_text=True)
