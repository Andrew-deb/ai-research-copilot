"""
alfred_domain/progress_service.py — Reading Progress & Annotation Service.

Manages per-user reading progress lifecycles (not_started -> reading -> completed/skipped)
and free-form researcher notes with semantic search capabilities.
"""

import logging
from typing import List, Optional

from .errors import NoteNotFoundError, PaperNotFoundError, ValidationError
from .papers import require_paper

logger = logging.getLogger(__name__)

VALID_STATUSES = {"not_started", "reading", "completed", "skipped"}


def mark_paper_status(repository, user_id: str, paper_id: str, status: str) -> dict:
    """
    Update reading status for a paper ('not_started', 'reading', 'completed', 'skipped').
    """
    if not user_id or not paper_id:
        raise ValidationError("Both user_id and paper_id are required.")

    clean_status = (status or "").strip().lower()
    if clean_status not in VALID_STATUSES:
        raise ValidationError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")

    paper = require_paper(repository, paper_id)

    progress = repository.upsert_reading_progress(user_id=user_id, paper_id=paper_id, status=clean_status)
    return {
        "status": "success",
        "paper_id": paper_id,
        "title": paper["title"],
        "reading_status": progress["status"],
        "updated_at": progress["updated_at"].isoformat() if hasattr(progress["updated_at"], "isoformat") else str(progress["updated_at"])
    }


def get_reading_progress(repository, user_id: str) -> List[dict]:
    """Retrieve full reading progress history for a user."""
    if not user_id:
        raise ValidationError("User ID is required.")
    return repository.get_user_progress(user_id=user_id)


def save_note(repository, user_id: str, paper_id: str, note_text: str) -> dict:
    """Save an annotation/note for a paper."""
    if not user_id or not paper_id:
        raise ValidationError("Both user_id and paper_id are required.")
    if not note_text or not note_text.strip():
        raise ValidationError("Note text cannot be empty.")

    paper = require_paper(repository, paper_id)

    note = repository.save_note(user_id=user_id, paper_id=paper_id, note_text=note_text.strip())
    return {
        "status": "success",
        "note_id": note["note_id"],
        "paper_id": paper_id,
        "paper_title": paper["title"],
        "note_text": note["note_text"],
        "created_at": note["created_at"].isoformat() if hasattr(note["created_at"], "isoformat") else str(note["created_at"])
    }


def get_notes_for_paper(repository, user_id: str, paper_id: str) -> List[dict]:
    """Retrieve all notes written by a user for a specific paper."""
    if not user_id or not paper_id:
        raise ValidationError("Both user_id and paper_id are required.")
    return repository.get_notes_for_paper(user_id=user_id, paper_id=paper_id)


def search_notes(repository, user_id: str, query: Optional[str] = None) -> List[dict]:
    """List or search notes belonging to a user."""
    if not user_id:
        raise ValidationError("User ID is required.")
    return repository.get_user_notes(user_id=user_id)
