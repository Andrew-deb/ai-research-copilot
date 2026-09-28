"""
tests/test_notes_search.py — finding a note again, and reading it as written.

Six notes fit on a screen; sixty do not. Two ways through them, plus one change
to how a note is displayed.

**Tags** are the cut a researcher actually makes — "methods to try", "for the
lit review" — and it runs ACROSS papers, so the paper grouping the page already
has cannot express it.

**Search is full text, not semantic, and that was a decision.** `note_embeddings`
has existed since Phase 1 holding zero rows; nothing ever populated it, and the
paper page has been promising notes are "embedded for cross-note semantic
search" the whole time. Wiring it up would put an embedding call on every save.
It would also be the wrong tool: someone hunting their own note wants the one
containing the words they remember writing, and an embedding deliberately blurs
exactly that. Semantic retrieval over notes stays the agent's job.

**Markdown** is rendered in the browser, not on the server, so the escaped text
is what ships and a page with no libraries shows the note as typed.
"""

import pathlib
import re

import pytest

from exceptions import ValidationError
from services import progress_service

ROOT = pathlib.Path(__file__).resolve().parents[1]
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _me(client, db) -> str:
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# Tidying tags
# ---------------------------------------------------------------------------

def test_tags_arrive_as_a_comma_separated_field():
    assert progress_service.clean_tags("methods, to-read") == ["methods", "to-read"]


def test_one_label_is_one_tag():
    """
    "Lit Review", "lit review" and "lit  review" are one label to the person who
    typed them and three to an index.
    """
    assert progress_service.clean_tags("Lit Review, lit  review, LIT REVIEW") \
        == ["lit review"]


def test_empty_pieces_are_dropped():
    assert progress_service.clean_tags("a,, ,b,") == ["a", "b"]


def test_the_order_typed_is_kept():
    """A weak signal, but the only one there is — alphabetising discards it."""
    assert progress_service.clean_tags("zeta, alpha") == ["zeta", "alpha"]


def test_a_runaway_tag_is_refused():
    with pytest.raises(ValidationError):
        progress_service.clean_tags("x" * 80)


def test_a_note_cannot_carry_unlimited_tags():
    with pytest.raises(ValidationError):
        progress_service.clean_tags(",".join(f"t{i}" for i in range(30)))


# ---------------------------------------------------------------------------
# Tags on a note
# ---------------------------------------------------------------------------

def test_a_note_can_be_tagged(client, db):
    note = progress_service.save_note(_me(client, db), None, "body", None, "methods, rag")
    assert note["tags"] == ["methods", "rag"]


def test_tags_survive_an_edit(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "body", None, "methods")
    updated = progress_service.update_note(user, note["note_id"], "body", None, "rag")
    assert updated["tags"] == ["rag"]


def test_the_tag_list_is_counted_from_the_notes(client, db):
    """
    From `unnest`, not from a tag table: the labels have no existence apart
    from the notes wearing them, so the notes are the only place they can be
    counted from without the two drifting.
    """
    user = _me(client, db)
    progress_service.save_note(user, None, "one", None, "methods, rag")
    progress_service.save_note(user, None, "two", None, "methods")

    cloud = progress_service.tag_cloud(user)
    assert cloud[0] == {"tag": "methods", "notes": 2}


def test_a_deleted_note_takes_its_tags_with_it(client, db):
    user = _me(client, db)
    note = progress_service.save_note(user, None, "one", None, "only-here")
    progress_service.delete_note(user, note["note_id"])

    assert progress_service.tag_cloud(user) == []


# ---------------------------------------------------------------------------
# Narrowing
# ---------------------------------------------------------------------------

def test_searching_finds_the_words_you_wrote(client, db):
    user = _me(client, db)
    progress_service.save_note(user, None, "The retriever is the weak point.")
    progress_service.save_note(user, None, "Something else entirely.")

    found = progress_service.find_notes(user, "retriever")
    assert len(found) == 1
    assert "retriever" in found[0]["notes"][0]["note_text"]


def test_two_tags_narrow_rather_than_widen(client, db):
    """
    Somebody who picks "methods" and "to-read" means the notes that are both.
    Union would hand back more than they started with, which is not a filter.
    """
    user = _me(client, db)
    progress_service.save_note(user, None, "both", None, "methods, to-read")
    progress_service.save_note(user, None, "one", None, "methods")

    found = progress_service.find_notes(user, None, "methods, to-read")
    assert sum(len(g["notes"]) for g in found) == 1


def test_words_and_tags_compose(client, db):
    user = _me(client, db)
    progress_service.save_note(user, None, "retriever ablations", None, "methods")
    progress_service.save_note(user, None, "retriever notes", None, "reading")

    found = progress_service.find_notes(user, "retriever", "methods")
    assert sum(len(g["notes"]) for g in found) == 1


def test_a_filtered_page_is_the_same_page(client, db):
    """
    Grouped exactly as the unfiltered list is, so a result reads like the page
    with fewer rows rather than like a different screen.
    """
    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "retriever notes", None, [])

    found = progress_service.find_notes(user, "retriever")
    assert found[0]["paper_id"] == str(paper["paper_id"])
    assert found[0]["title"] == "A Paper"
    assert "words" in found[0]


def test_a_search_is_a_url(client, db):
    """So it can be bookmarked, reloaded and shared with yourself."""
    user = _me(client, db)
    progress_service.save_note(user, None, "retriever ablations")

    body = client.get("/notes?q=retriever").get_data(as_text=True)
    assert "retriever ablations" in body
    assert "1 match" in body


def test_a_tag_filter_is_a_url_too(client, db):
    user = _me(client, db)
    progress_service.save_note(user, None, "tagged note", None, "methods")
    progress_service.save_note(user, None, "untagged note")

    body = client.get("/notes?tag=methods").get_data(as_text=True)
    assert "tagged note" in body
    assert "untagged note" not in body


def test_a_malformed_search_is_not_an_error(client, db):
    """
    `websearch_to_tsquery` never raises on rubbish. `to_tsquery` would turn a
    stray ampersand in somebody's search box into a 500.
    """
    _me(client, db)

    # The behaviour, not the spelling: several searches that would be syntax
    # errors to `to_tsquery` have to come back as an ordinary empty page.
    for rubbish in ("%26%26%20%7C%7C", "%22unclosed", "%3A%3A%3A", "a%20%26%20%7C%20b"):
        assert client.get("/notes?q=" + rubbish).status_code == 200


def test_the_search_box_is_offered_whatever_you_have_written(client, db):
    """
    This asserted the opposite: the filter was hidden below five notes, on the
    reasoning that a search box above three is furniture. It made the feature
    invisible on exactly the pages where somebody would look to find out
    whether searching was possible at all — which is what happened.
    """
    _me(client, db)
    assert "notes-filter" in client.get("/notes").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Reading a note as it was written
# ---------------------------------------------------------------------------

def test_the_body_ships_escaped_and_is_upgraded_in_the_browser(client, db):
    """
    Rendered client-side, so the text is what ships: a page whose CDN failed
    shows the note exactly as typed rather than nothing at all.
    """
    user = _me(client, db)
    progress_service.save_note(user, None, "- first\n- second")

    body = client.get("/notes").get_data(as_text=True)
    assert 'data-markdown' in body
    assert "- first" in body            # the source, not <li>first</li>


def test_markdown_is_never_rendered_without_the_sanitiser():
    """
    Rendering without DOMPurify turns stored text into live HTML. "It is only
    ever mine" stops being true the moment the agent can write a note too.
    """
    js = (ROOT / "dashboard" / "static" / "js" / "notes.js").read_text(encoding="utf-8")
    block = js.split("data-markdown")[1]
    assert "DOMPurify" in block
    assert "undefined" in block.split("DOMPurify.sanitize")[0]   # the both-or-neither guard


def test_editing_reads_the_source_not_the_rendering(client, db):
    """
    Once the paragraph is Markdown, its textContent is the RENDERING. Editing
    from that would save the rendered text back over the source.
    """
    user = _me(client, db)
    progress_service.save_note(user, None, "- first\n- second")

    body = client.get("/notes").get_data(as_text=True)
    assert "data-note-body=" in body

    js = (ROOT / "dashboard" / "static" / "js"
          / "notes-page.js").read_text(encoding="utf-8")
    assert 'getAttribute("data-note-body")' in js
    assert '.note-text").textContent' not in js


def test_the_libraries_carry_the_same_hashes_as_the_chat_page():
    """
    Two copies of an integrity hash are two chances to get one wrong. They must
    at least agree.
    """
    templates = ROOT / "dashboard" / "templates"
    chat = (templates / "chat.html").read_text(encoding="utf-8")
    notes = (templates / "notes.html").read_text(encoding="utf-8")

    hashes = lambda src: set(re.findall(r'integrity="(sha512-[^"]+)"', src))
    assert hashes(chat) and hashes(chat) == hashes(notes)
