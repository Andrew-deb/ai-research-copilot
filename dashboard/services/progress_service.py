"""
dashboard/services/progress_service.py — Reading progress board & notes.

Drives the Kanban page (papers bucketed by reading status) and the note
annotations shown on the paper detail page. Status lifecycle matches the
schema CHECK constraint and the MCP progress_service exactly.
"""

import logging

from exceptions import PaperNotFoundError, ValidationError
from repositories import lakebase

try:
    from shared_resource.services import note_service as shared_notes
    from shared_resource import exceptions as domain_errors
except ModuleNotFoundError:
    from mcp_server.shared_resource.services import note_service as shared_notes
    from mcp_server.shared_resource import exceptions as domain_errors

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
NOTE_TAG_MAX_CHARS = 40
NOTE_MAX_TAGS = 12


def clean_tags(raw) -> list[str]:
    """
    Tags from a comma-separated field or a list, tidied into something a person
    will recognise the second time they type it.

    Lowercased and whitespace-collapsed, because "Lit Review", "lit review" and
    "lit  review" are one label as far as their author is concerned, and three
    as far as an index is concerned. Order is preserved and duplicates dropped:
    the order somebody typed them is a weak signal, but it is the only one
    available and alphabetising would discard it for nothing.
    """
    if isinstance(raw, str):
        raw = raw.split(",")
    seen: list[str] = []
    for item in (raw or []):
        tag = " ".join(str(item).lower().split())
        if not tag or tag in seen:
            continue
        if len(tag) > NOTE_TAG_MAX_CHARS:
            raise ValidationError(
                f"Tags can be at most {NOTE_TAG_MAX_CHARS} characters ('{tag[:24]}…').")
        seen.append(tag)
        if len(seen) > NOTE_MAX_TAGS:
            raise ValidationError(f"A note can carry at most {NOTE_MAX_TAGS} tags.")
    return seen


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
              title: str | None = None, tags=None) -> dict:
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
    return lakebase.save_note(user_id, paper_id or None, text,
                              _clean_title(title), clean_tags(tags))


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
        "tags": list(note.get("tags") or []),
        "pinned": bool(note.get("pinned")),
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
    return _group(lakebase.get_all_notes(user_id))


def _group(rows: list[dict]) -> list[dict]:
    """
    Note rows, gathered under the paper each is about.

    Shared by the whole list and by a filtered one, so a search result reads
    like the page with fewer rows rather than like a different screen. Row
    order is preserved, which is what carries the ranking through: a filtered
    query arrives ordered by relevance, an unfiltered one by recency, and
    grouping must not quietly re-sort either.
    """
    grouped: dict[str | None, dict] = {}
    for note in rows:
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


NOTE_SCOPES = ("paper", "standalone", "pinned")


def find_notes(user_id: str, query: str | None = None, tags=None,
               scope: str | None = None) -> list[dict]:
    """
    The notes page, narrowed.

    Grouped exactly as `all_notes` groups them, so a filtered page reads like
    the unfiltered one with fewer rows rather than like a different screen.
    """
    # An unrecognised scope is ignored rather than refused: it arrives from a
    # URL anybody can edit, and a mistyped filter should show everything rather
    # than an error page.
    rows = lakebase.search_notes(user_id, (query or "").strip() or None,
                                 clean_tags(tags) or None,
                                 scope if scope in NOTE_SCOPES else None)
    return _group(rows)


def to_markdown(groups: list[dict]) -> str:
    """
    Notes as one Markdown document, grouped exactly as the page groups them.

    Markdown rather than JSON or CSV: this is prose, and the point of an export
    is that the file is readable and useful somewhere else — pasted into a
    draft, opened in any editor, committed beside a paper. A JSON dump would be
    a backup rather than a document, and CSV cannot hold a paragraph without
    fighting it.

    What a note is ABOUT travels with it. A note reading "the retriever is the
    weak point" is close to meaningless once separated from the paper it was
    written against, so the paper, its venue and its year head each section.
    """
    from datetime import datetime, timezone

    out = ["# Notes",
           "",
           f"Exported {datetime.now(timezone.utc).strftime('%d %B %Y')}",
           ""]

    for group in groups:
        if group["paper_id"]:
            heading = group["title"] or "Untitled paper"
            meta = " · ".join(str(p) for p in
                              (group.get("venue"), group.get("publication_year")) if p)
            out.append(f"## {heading}")
            if meta:
                out.append(f"*{meta}*")
        else:
            out.append("## Not about a paper")
        out.append("")

        for note in group["notes"]:
            if note.get("title"):
                out.append(f"### {note['title']}")
            when = note["created_at"]
            stamp = when.strftime("%d %b %Y") if hasattr(when, "strftime") else str(when)
            marks = [stamp]
            if note.get("pinned"):
                marks.append("pinned")
            if note.get("edited"):
                marks.append("edited")
            if note.get("tags"):
                marks.append(" ".join("#" + t for t in note["tags"]))
            out.append(f"*{' · '.join(marks)}*")
            out.append("")
            # The body verbatim: it was written as Markdown and stays that way,
            # so a list is still a list on the other side.
            out.append(note["note_text"])
            out.append("")

    return "\n".join(out).rstrip() + "\n"


def export_one(user_id: str, note_id: str) -> dict:
    """
    One note as a Markdown document, with a filename taken from its own name.

    Reuses `to_markdown` on a group of one rather than growing a second
    renderer: a single note and a library of them should come out looking the
    same, and two renderers drift.

    404 when it is not theirs, as everywhere else in this module.
    """
    row = lakebase.get_note(user_id, note_id)
    if not row:
        raise PaperNotFoundError("That note could not be found.")

    group = _group([row])
    return {"markdown": to_markdown(group), "filename": _filename(row)}


def _filename(note: dict) -> str:
    """
    A filename a person can read in a folder listing.

    Built from the note's title, or its first line when it has none, because
    forty files called `note.md` are forty files nobody can tell apart. Only
    letters, digits, spaces and dashes survive: everything else is either
    illegal on some filesystem or a path separator, and a filename is not a
    place to find out which.
    """
    import re

    stem = (note.get("title") or "").strip() or display_title(note)
    stem = re.sub(r"[^A-Za-z0-9 \-]+", "", stem).strip()
    stem = re.sub(r"\s+", "-", stem)[:60].strip("-")
    return f"{stem or 'note'}.md"


def tag_cloud(user_id: str) -> list[dict]:
    """Every tag in use, commonest first, for the filter row."""
    return [{"tag": row["tag"], "notes": row["notes"]}
            for row in lakebase.get_note_tags(user_id)]


def notes_summary(groups: list[dict]) -> dict:
    """Totals for the page header — how much is actually here."""
    notes = [n for g in groups for n in g["notes"]]
    return {
        "notes": len(notes),
        "words": sum(n["words"] for n in notes),
        "papers": sum(1 for g in groups if g["paper_id"]),
    }


def _note_mutation(operation, *args):
    """Keep dashboard presentation/errors while delegating business operations."""
    try:
        return operation(lakebase, *args)
    except domain_errors.ValidationError as exc:
        raise ValidationError(str(exc)) from exc
    except domain_errors.NoteNotFoundError as exc:
        # Preserve the dashboard's established 404 exception contract.
        raise PaperNotFoundError(str(exc)) from exc


def update_note(user_id: str, note_id: str, note_text: str,
                title: str | None = None, tags=None) -> dict:
    return _present(_note_mutation(shared_notes.update_note, user_id, note_id, note_text, title, tags))


def set_pinned(user_id: str, note_id: str, pinned: bool) -> dict:
    return _present(_note_mutation(shared_notes.set_pinned, user_id, note_id, bool(pinned)))


def delete_note(user_id: str, note_id: str) -> None:
    _note_mutation(shared_notes.delete_note, user_id, note_id)


def list_notes(user_id: str, paper_id: str) -> list[dict]:
    return lakebase.get_notes_for_paper(user_id, paper_id)
