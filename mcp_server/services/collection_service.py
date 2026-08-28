"""
mcp_server/services/collection_service.py — Collection Management Service.

Handles creating collections, managing papers within collections, ordering sequences,
and retrieving collection contents for users.
"""

import logging
from typing import List, Optional

from mcp_server.exceptions import CollectionNotFoundError, PaperNotFoundError, ValidationError
from mcp_server.repositories import lakebase

logger = logging.getLogger(__name__)


def create_collection(user_id: str, name: str, description: Optional[str] = None) -> dict:
    """Create a new paper collection for a user."""
    if not user_id:
        raise ValidationError("User ID is required.")
    if not name or not name.strip():
        raise ValidationError("Collection name cannot be empty.")

    logger.info(f"Creating collection '{name}' for user {user_id}")
    return lakebase.create_collection(user_id=user_id, name=name.strip(), description=description)


def list_collections(user_id: str) -> List[dict]:
    """Retrieve all collections belonging to a user."""
    if not user_id:
        raise ValidationError("User ID is required.")
    return lakebase.get_collections(user_id=user_id)


def get_collection_details(collection_id: str, user_id: str) -> dict:
    """
    Retrieve collection metadata and the ordered list of papers within it.
    """
    if not collection_id:
        raise ValidationError("Collection ID is required.")

    collection = lakebase.get_collection(collection_id=collection_id, user_id=user_id)
    if not collection:
        raise CollectionNotFoundError(f"Collection '{collection_id}' not found for user.")

    papers = lakebase.get_collection_papers(collection_id)
    collection["papers"] = papers
    collection["paper_count"] = len(papers)
    return collection


def add_paper_to_collection(collection_id: str, paper_id: str, sequence_order: int = 0, user_id: Optional[str] = None) -> dict:
    """
    Add a paper to a collection. Validates existence of collection and paper.
    """
    if not collection_id or not paper_id:
        raise ValidationError("Both collection_id and paper_id are required.")

    if user_id:
        collection = lakebase.get_collection(collection_id, user_id)
        if not collection:
            raise CollectionNotFoundError(f"Collection '{collection_id}' not found.")

    paper = lakebase.get_paper(paper_id)
    if not paper:
        raise PaperNotFoundError(f"Paper '{paper_id}' not found in catalog.")

    lakebase.add_paper_to_collection(collection_id, paper_id, sequence_order)
    return {
        "status": "success",
        "message": f"Paper '{paper['title']}' added to collection.",
        "collection_id": collection_id,
        "paper_id": paper_id,
        "sequence_order": sequence_order
    }


def remove_paper_from_collection(collection_id: str, paper_id: str, user_id: Optional[str] = None) -> dict:
    """Remove a paper from a collection."""
    if not collection_id or not paper_id:
        raise ValidationError("Both collection_id and paper_id are required.")

    if user_id:
        collection = lakebase.get_collection(collection_id, user_id)
        if not collection:
            raise CollectionNotFoundError(f"Collection '{collection_id}' not found.")

    rows_deleted = lakebase.remove_paper_from_collection(collection_id, paper_id)
    return {
        "status": "success",
        "message": "Paper removed from collection.",
        "rows_affected": rows_deleted
    }
