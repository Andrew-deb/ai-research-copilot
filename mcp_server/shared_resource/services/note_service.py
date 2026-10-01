"""Shared note operations, independent of MCP and Flask."""

from typing import List, Optional

from ..exceptions import ValidationError
from .paper_service import require_paper


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
