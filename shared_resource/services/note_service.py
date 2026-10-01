"""Shared note operations, independent of MCP and Flask."""

from typing import List, Optional

from ..exceptions import ValidationError
from .paper_service import require_paper

NOTE_MAX_CHARS = 10_000
NOTE_TITLE_MAX_CHARS = 200
NOTE_TAG_MAX_CHARS = 40
NOTE_MAX_TAGS = 12


def clean_tags(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    if raw is not None and not isinstance(raw, (list, tuple)):
        raise ValidationError("Tags must be a list or comma-separated text.")
    seen = []
    for item in raw or []:
        if not isinstance(item, str):
            raise ValidationError("Each tag must be text.")
        tag = " ".join(item.lower().split())
        if not tag or tag in seen:
            continue
        if len(tag) > NOTE_TAG_MAX_CHARS:
            raise ValidationError(f"Tags can be at most {NOTE_TAG_MAX_CHARS} characters.")
        seen.append(tag)
        if len(seen) > NOTE_MAX_TAGS:
            raise ValidationError(f"A note can carry at most {NOTE_MAX_TAGS} tags.")
    return seen


def create_note(repository, user_id: str, note_text: str, paper_id: str | None = None,
                title: str | None = None, tags=None) -> dict:
    """Create a bounded standalone or paper-linked note in the acting user's workspace."""
    if not user_id:
        raise ValidationError("User ID is required.")
    if not isinstance(note_text, str) or not note_text.strip():
        raise ValidationError("Note text cannot be empty.")
    text = note_text.strip()
    if len(text) > NOTE_MAX_CHARS:
        raise ValidationError(f"That note is too long (max {NOTE_MAX_CHARS:,} characters).")
    if title is not None and not isinstance(title, str):
        raise ValidationError("Note title must be text.")
    clean_title = (title or "").strip() or None
    if clean_title and len(clean_title) > NOTE_TITLE_MAX_CHARS:
        raise ValidationError(f"That title is too long (max {NOTE_TITLE_MAX_CHARS} characters).")
    cleaned_tags = clean_tags(tags)
    paper = require_paper(repository, paper_id) if paper_id else None
    fields = {"user_id": user_id, "paper_id": paper_id or None, "note_text": text}
    # Preserve the existing three-argument repository path when no metadata is supplied.
    if clean_title is not None or cleaned_tags:
        fields.update(title=clean_title, tags=cleaned_tags)
    note = repository.save_note(**fields)
    return {**note, "paper_title": paper["title"] if paper else None}


def save_note(repository, user_id: str, paper_id: str, note_text: str) -> dict:
    """Save an annotation/note for a paper."""
    if not user_id or not paper_id:
        raise ValidationError("Both user_id and paper_id are required.")
    note = create_note(repository, user_id, note_text, paper_id=paper_id)
    return {
        "status": "success",
        "note_id": note["note_id"],
        "paper_id": paper_id,
        "paper_title": note["paper_title"],
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
