"""Research compatibility adapters; domain operations take an explicit repository."""

from typing import List, Optional

from shared_resource.services import collection_service as domain
from repositories import lakebase


def create_collection(user_id: str, name: str, description: Optional[str] = None) -> dict:
    """Create a new paper collection for a user."""
    return domain.create_collection(lakebase, user_id=user_id, name=name, description=description)


def list_collections(user_id: str) -> List[dict]:
    """Retrieve all collections belonging to a user."""
    return domain.list_collections(lakebase, user_id=user_id)


def get_collection_details(collection_id: str, user_id: str) -> dict:
    """Retrieve collection metadata and the ordered list of papers within it."""
    return domain.get_collection_details(lakebase, collection_id=collection_id, user_id=user_id)


def add_paper_to_collection(collection_id: str, paper_id: str, sequence_order: int = 0, user_id: Optional[str] = None) -> dict:
    """Add a paper to a collection. Validates existence of collection and paper."""
    return domain.add_paper_to_collection(
        lakebase,
        collection_id=collection_id,
        paper_id=paper_id,
        sequence_order=sequence_order,
        user_id=user_id,
    )


def remove_paper_from_collection(collection_id: str, paper_id: str, user_id: Optional[str] = None) -> dict:
    """Remove a paper from a collection."""
    return domain.remove_paper_from_collection(
        lakebase,
        collection_id=collection_id,
        paper_id=paper_id,
        user_id=user_id,
    )
