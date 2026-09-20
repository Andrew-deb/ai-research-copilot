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


def save_note(user_id: str, paper_id: str, note_text: str) -> dict:
    if not note_text or not note_text.strip():
        raise ValidationError("Note text cannot be empty.")
    if not lakebase.get_paper(paper_id):
        raise PaperNotFoundError(f"Paper '{paper_id}' not found.")
    return lakebase.save_note(user_id, paper_id, note_text.strip())


def list_notes(user_id: str, paper_id: str) -> list[dict]:
    return lakebase.get_notes_for_paper(user_id, paper_id)
