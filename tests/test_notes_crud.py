"""
tests/test_notes_crud.py — notes you can revise, remove, and write about nothing.

The notes feature could only append. You could write a note against a paper and
never change it, never delete it, and never write one that was not about a
paper at all — `notes.paper_id` was NOT NULL, so every thought had to pick a
study to belong to.

Notes are the one thing in this application a person *authors*. Everything else
is a record of something external that can be fetched again; a note cannot. So
the operations that matter most here are the destructive ones, and the tests
that matter most are the ones about who is allowed to perform them.
"""

import pytest

from exceptions import PaperNotFoundError, ValidationError
from services import progress_service

XHR = {"X-Requested-With": "XMLHttpRequest"}


def _me(client, db) -> str:
    """The signed-in user. The dev client makes its account on first request."""
    client.get("/dashboard")
    return next(iter(db.users_by_id))


# ---------------------------------------------------------------------------
# Counting words
# ---------------------------------------------------------------------------

def test_words_are_counted_the_way_a_person_counts_them():
    assert progress_service.word_count("three little words") == 3


def test_a_bulleted_note_is_not_one_enormous_word():
    """
    Splitting on " " rather than on whitespace reports one word per line for a
    list, which is most of what a research note actually looks like.
    """
    assert progress_service.word_count("- first\n- second\n- third") == 6


def test_an_empty_note_counts_nothing():
    for empty in ("", "   ", None):
        assert progress_service.word_count(empty) == 0


# ---------------------------------------------------------------------------
# Revising
# ---------------------------------------------------------------------------

def test_a_note_can_be_revised(client, db):
    paper = db.seed_paper(title="A Paper")
    note = db.save_note(_me(client, db), str(paper["paper_id"]), "first thought")

    updated = progress_service.update_note(
        next(iter(db.users_by_id)), note["note_id"], "a better thought")

    assert updated["note_text"] == "a better thought"
    assert updated["words"] == 3


def test_a_revised_note_says_so(client, db):
    """
    "When did I last think about this" is a question people ask of their own
    notes constantly, and an unedited note looked identical to a rewritten one.
    """
    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    note = db.save_note(user, str(paper["paper_id"]), "first")

    fresh = progress_service.all_notes(user)[0]["notes"][0]
    assert fresh["edited"] is False

    progress_service.update_note(user, note["note_id"], "second")
    assert progress_service.all_notes(user)[0]["notes"][0]["edited"] is True


def test_an_edit_cannot_empty_a_note(client, db):
    """
    The same mistake as saving an empty one, and it should fail the same way
    rather than quietly leaving a blank behind.
    """
    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    note = db.save_note(user, str(paper["paper_id"]), "something")

    with pytest.raises(ValidationError):
        progress_service.update_note(user, note["note_id"], "   ")


def test_a_note_that_is_not_yours_cannot_be_revised(client, db):
    """
    The ownership check is in the WHERE clause, not a read-then-decide: only the
    database can make the check and the change the same act.
    """
    paper = db.seed_paper(title="A Paper")
    me = _me(client, db)
    stranger = db.get_or_create_user("someone@example.com", "Someone")
    theirs = db.save_note(stranger["user_id"], str(paper["paper_id"]), "not yours")

    with pytest.raises(PaperNotFoundError):
        progress_service.update_note(me, theirs["note_id"], "rewritten")

    assert db.notes[str(theirs["note_id"])]["note_text"] == "not yours"


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------

def test_a_note_can_be_deleted(client, db):
    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    note = db.save_note(user, str(paper["paper_id"]), "delete me")

    progress_service.delete_note(user, note["note_id"])
    assert str(note["note_id"]) not in db.notes


def test_someone_elses_note_cannot_be_deleted(client, db):
    paper = db.seed_paper(title="A Paper")
    me = _me(client, db)
    stranger = db.get_or_create_user("someone@example.com", "Someone")
    theirs = db.save_note(stranger["user_id"], str(paper["paper_id"]), "keep me")

    with pytest.raises(PaperNotFoundError):
        progress_service.delete_note(me, theirs["note_id"])

    assert str(theirs["note_id"]) in db.notes


def test_a_missing_note_and_a_stranger_s_note_answer_alike(client, db):
    """
    Distinguishing them would confirm that a note exists to somebody who is not
    allowed to read it.
    """
    me = _me(client, db)
    with pytest.raises(PaperNotFoundError):
        progress_service.delete_note(me, "00000000-0000-0000-0000-000000000000")


def test_deleting_is_confirmed_in_the_browser():
    """
    Notes are authored, so a deleted one is gone in a way a fetched record never
    is. main.js already intercepts [data-confirm] site-wide.
    """
    import pathlib
    markup = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "templates"
              / "notes.html").read_text(encoding="utf-8")
    assert "data-confirm=" in markup


# ---------------------------------------------------------------------------
# Notes that are not about a paper
# ---------------------------------------------------------------------------

def test_a_note_need_not_be_about_a_paper(client, db):
    """
    A question to chase, a comparison across three papers, a reminder about a
    method. Forcing those onto an arbitrary paper files them where their author
    will not look.
    """
    user = _me(client, db)
    note = progress_service.save_note(user, None, "Chase the 2019 replication.")
    assert note["paper_id"] is None


def test_standalone_notes_group_on_their_own(client, db):
    user = _me(client, db)
    progress_service.save_note(user, None, "a loose thought")

    groups = progress_service.all_notes(user)
    assert len(groups) == 1
    assert groups[0]["paper_id"] is None


def test_a_standalone_note_still_reaches_the_page(client, db):
    """
    get_all_notes used to INNER JOIN papers, which was correct while paper_id
    was NOT NULL and would now drop every standalone note from the page that
    exists to show them.
    """
    progress_service.save_note(_me(client, db), None, "a loose thought")
    body = client.get("/notes").get_data(as_text=True)

    assert "a loose thought" in body
    assert "Not about a paper" in body


def test_a_note_about_a_missing_paper_is_refused(client, db):
    with pytest.raises(PaperNotFoundError):
        progress_service.save_note(_me(client, db),
                                   "00000000-0000-0000-0000-000000000000", "hello")


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------

def test_the_page_says_how_much_is_there(client, db):
    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "one two three")
    db.save_note(user, None, "four five")

    summary = progress_service.notes_summary(progress_service.all_notes(user))
    assert summary["notes"] == 2
    assert summary["words"] == 5
    assert summary["papers"] == 1        # the standalone group is not a paper


def test_word_counts_survive_the_round_trip(client, db):
    """
    The count rendered by the server and the one notes.js shows while typing
    must agree, or it changes on reload for no visible reason.
    """
    paper = db.seed_paper(title="A Paper")
    user = _me(client, db)
    db.save_note(user, str(paper["paper_id"]), "- first\n- second")

    body = client.get("/notes").get_data(as_text=True)
    assert "4 words" in body
