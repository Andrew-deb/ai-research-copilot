"""
dashboard/services/progress_service.py — Reading progress board & notes.

Drives the Kanban page (papers bucketed by reading status) and the note
annotations shown on the paper detail page. Status lifecycle matches the
schema CHECK constraint and the MCP progress_service exactly.
"""

import logging

from exceptions import PaperNotFoundError, ValidationError
from repositories import lakebase

logger = logging.getLogger(__name__)

VALID_STATUSES = ["not_started", "reading", "completed", "skipped"]


def get_board(user_id: str) -> dict:
    """
    The Kanban board: papers in the user's collections plus anything they have
    explicitly given a status, bucketed into columns.

    Uses `get_reading_board`, not `get_user_progress`. The latter returns only rows
    the user has already touched, which meant a collected paper never appeared until
    its status had been set somewhere else - so the board showed nothing to drag
    while instructing the user to drag things.
    """
    rows = lakebase.get_reading_board(user_id)

    columns: dict[str, list[dict]] = {status: [] for status in VALID_STATUSES}
    for row in rows:
        columns.setdefault(row["status"], []).append(row)

    return {
        "columns": columns,
        "stats": {status: len(columns[status]) for status in VALID_STATUSES},
        "total": len(rows),
    }


def set_status(user_id: str, paper_id: str, status: str) -> dict:
    clean = (status or "").strip().lower()
    if clean not in VALID_STATUSES:
        raise ValidationError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}.")
    if not lakebase.get_paper(paper_id):
        raise PaperNotFoundError(f"Paper '{paper_id}' not found.")
    return lakebase.upsert_reading_progress(user_id, paper_id, clean)


NOTE_MAX_CHARS = 10_000
NOTE_TITLE_MAX_CHARS = 200


def _clean_title(title: str | None) -> str | None:
    """
    A title, or nothing.

    Optional on purpose: a note jotted in three words needs no name, and
    demanding one would turn the quick capture the notepad exists for into a
    two-field form. Blank and absent are the same thing, so an emptied title
    field clears the name rather than storing "".
    """
    text = (title or "").strip()
    if not text:
        return None
    if len(text) > NOTE_TITLE_MAX_CHARS:
        raise ValidationError(
            f"That title is too long (max {NOTE_TITLE_MAX_CHARS} characters).")
    return text


def display_title(note: dict) -> str:
    """
    What to show in a list.

    Falls back to the first line of the body rather than to "Untitled": the
    first line is what the reader was going to skim anyway, and a column of
    "Untitled" tells them nothing about which note is which.
    """
    if note.get("title"):
        return note["title"]
    first = (note.get("note_text") or "").strip().splitlines()
    line = first[0] if first else ""
    return (line[:77] + "…") if len(line) > 78 else (line or "Untitled note")


def save_note(user_id: str, paper_id: str | None, note_text: str,
              title: str | None = None) -> dict:
    """
    Write a note, about a paper or about nothing in particular.

    `paper_id` is optional since sql/16. Plenty of what a researcher wants to
    record is not about one study — a question to chase, a comparison across
    three papers, a reminder about a method — and making those pick an arbitrary
    paper files them where their author will not look for them.

    The paper is still checked when one is given: a note attached to something
    that does not exist would vanish from the page it was written on.
    """
    text = (note_text or "").strip()
    if not text:
        raise ValidationError("A note cannot be empty.")
    if len(text) > NOTE_MAX_CHARS:
        raise ValidationError(f"That note is too long (max {NOTE_MAX_CHARS:,} characters).")
    if paper_id and not lakebase.get_paper(paper_id):
        raise PaperNotFoundError(f"Paper '{paper_id}' not found.")
    return lakebase.save_note(user_id, paper_id or None, text, _clean_title(title))


def word_count(text: str | None) -> int:
    """
    Words, counted the way a person counts them.

    `split()` on any whitespace rather than on spaces: a note written as a
    bulleted list is mostly newlines, and splitting on " " would report one
    enormous word per line. Close enough is the right target here — this is a
    sense of size, not a billing figure.
    """
    return len((text or "").split())


def _present(note: dict) -> dict:
    """One note, in the shape the page renders."""
    return {
        "note_id": str(note["note_id"]),
        "note_text": note["note_text"],
        "title": note.get("title"),
        "display_title": display_title(note),
        "created_at": note["created_at"],
        "updated_at": note.get("updated_at"),
        # Revised notes say so. Equal timestamps mean it was written once and
        # left alone, which is not worth the visual noise of an "edited" mark.
        "edited": bool(note.get("updated_at")
                       and note["updated_at"] != note["created_at"]),
        "words": word_count(note["note_text"]),
    }


def all_notes(user_id: str) -> list[dict]:
    """
    Everything this person has written, for the notes page.

    Grouped by paper here rather than in the template, because "what did I say
    about this paper" is the question the page answers and Jinja is a poor place
    to do it. Order is preserved: papers appear by their most recent note, so
    what you were last thinking about is at the top.

    Notes with no paper collect under a single group with `paper_id: None`. It
    sorts wherever its newest note puts it rather than being pinned to the top
    or bottom, because a standalone note is an ordinary note - the absence of a
    paper is not a category of importance.
    """
    grouped: dict[str | None, dict] = {}
    for note in lakebase.get_all_notes(user_id):
        key = str(note["paper_id"]) if note.get("paper_id") else None
        if key not in grouped:
            grouped[key] = {
                "paper_id": key,
                "title": note.get("paper_title"),
                "venue": note.get("venue"),
                "publication_year": note.get("publication_year"),
                "notes": [],
            }
        grouped[key]["notes"].append(_present(note))

    groups = list(grouped.values())
    for group in groups:
        group["words"] = sum(n["words"] for n in group["notes"])
    return groups


def notes_summary(groups: list[dict]) -> dict:
    """Totals for the page header — how much is actually here."""
    notes = [n for g in groups for n in g["notes"]]
    return {
        "notes": len(notes),
        "words": sum(n["words"] for n in notes),
        "papers": sum(1 for g in groups if g["paper_id"]),
    }


def update_note(user_id: str, note_id: str, note_text: str,
                title: str | None = None) -> dict:
    """
    Revise a note.

    Validated exactly as saving is, because an edit that empties a note is the
    same mistake as saving an empty one and should fail the same way rather
    than quietly leaving a blank behind.
    """
    text = (note_text or "").strip()
    if not text:
        raise ValidationError("A note cannot be empty.")
    if len(text) > NOTE_MAX_CHARS:
        raise ValidationError(f"That note is too long (max {NOTE_MAX_CHARS:,} characters).")

    note = lakebase.update_note(user_id, note_id, text, _clean_title(title))
    if not note:
        # Not theirs, or not there. Both are 404 to the caller: distinguishing
        # them would confirm the note exists to someone who cannot read it.
        raise PaperNotFoundError("That note could not be found.")
    return _present(note)


def delete_note(user_id: str, note_id: str) -> None:
    """Remove a note. Missing and not-yours are the same answer, as above."""
    if not lakebase.delete_note(user_id, note_id):
        raise PaperNotFoundError("That note could not be found.")


def list_notes(user_id: str, paper_id: str) -> list[dict]:
    return lakebase.get_notes_for_paper(user_id, paper_id)
