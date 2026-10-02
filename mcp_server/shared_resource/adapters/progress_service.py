"""Shared MCP runtime bindings; domain operations take an explicit repository."""

from typing import List, Optional

from ..services import progress_service as domain
from ..services import note_service as notes
from ..repositories import lakebase

VALID_STATUSES = domain.VALID_STATUSES


def mark_paper_status(user_id: str, paper_id: str, status: str) -> dict:
    """Update reading status for a paper ('not_started', 'reading', 'completed', 'skipped')."""
    return domain.mark_paper_status(lakebase, user_id=user_id, paper_id=paper_id, status=status)


def get_reading_progress(user_id: str) -> List[dict]:
    """Retrieve full reading progress history for a user."""
    return domain.get_reading_progress(lakebase, user_id=user_id)


def save_note(user_id: str, paper_id: str, note_text: str) -> dict:
    """Save an annotation/note for a paper."""
    return notes.save_note(lakebase, user_id=user_id, paper_id=paper_id, note_text=note_text)


def get_notes_for_paper(user_id: str, paper_id: str) -> List[dict]:
    """Retrieve all notes written by a user for a specific paper."""
    return notes.get_notes_for_paper(lakebase, user_id=user_id, paper_id=paper_id)


def search_notes(user_id: str, query: Optional[str] = None) -> List[dict]:
    """List or search notes belonging to a user."""
    return notes.search_notes(lakebase, user_id=user_id, query=query)
