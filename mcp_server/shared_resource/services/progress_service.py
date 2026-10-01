"""
shared_resource/services/progress_service.py — Reading Progress Service.

Manages per-user reading progress lifecycles (not_started -> reading -> completed/skipped).
"""

from typing import List

from ..exceptions import ValidationError
from .paper_service import require_paper

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
