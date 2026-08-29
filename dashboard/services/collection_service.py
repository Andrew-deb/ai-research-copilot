"""
dashboard/services/collection_service.py — Collections & reading plans.

Collection CRUD, paper membership, manual drag-reorder, and a reading-plan
generator. The plan heuristic is deliberately kept identical to
mcp_server/services/planning_service.generate_reading_plan so the dashboard
button and the agent tool produce the same ordering — each Databricks App
owns its own copy of the logic (no runtime dependency between the two apps).
"""

import logging

from dashboard.exceptions import CollectionNotFoundError, PaperNotFoundError, ValidationError
from dashboard.repositories import lakebase

logger = logging.getLogger(__name__)


def _require_collection(collection_id: str, user_id: str) -> dict:
    collection = lakebase.get_collection(collection_id, user_id)
    if not collection:
        raise CollectionNotFoundError(f"Collection '{collection_id}' not found.")
    return collection


# =============================================================================
# CRUD
# =============================================================================

def list_collections(user_id: str) -> list[dict]:
    return lakebase.get_collections(user_id)


def create_collection(user_id: str, name: str, description: str | None = None) -> dict:
    if not name or not name.strip():
        raise ValidationError("Collection name cannot be empty.")
    if len(name.strip()) > 200:
        raise ValidationError("Collection name is too long (max 200 characters).")
    return lakebase.create_collection(
        user_id=user_id, name=name.strip(), description=(description or "").strip() or None
    )


def get_collection_detail(user_id: str, collection_id: str) -> dict:
    collection = _require_collection(collection_id, user_id)
    papers = lakebase.get_collection_papers(collection_id)
    collection["papers"] = papers
    collection["paper_count"] = len(papers)
    return collection


# =============================================================================
# Membership
# =============================================================================

def add_paper(user_id: str, collection_id: str, paper_id: str) -> dict:
    _require_collection(collection_id, user_id)
    if not lakebase.get_paper(paper_id):
        raise PaperNotFoundError(f"Paper '{paper_id}' not found in the catalog.")

    # Append to the end of the current sequence.
    existing = lakebase.get_collection_papers(collection_id)
    next_order = max((p.get("sequence_order") or 0 for p in existing), default=0) + 1
    lakebase.add_paper_to_collection(collection_id, paper_id, next_order)
    return {"status": "ok", "collection_id": collection_id, "paper_id": paper_id, "sequence_order": next_order}


def remove_paper(user_id: str, collection_id: str, paper_id: str) -> dict:
    _require_collection(collection_id, user_id)
    removed = lakebase.remove_paper_from_collection(collection_id, paper_id)
    return {"status": "ok", "rows_affected": removed}


def reorder(user_id: str, collection_id: str, ordered_paper_ids: list[str]) -> dict:
    """Persist a manual drag-reorder: position in the list becomes sequence_order."""
    _require_collection(collection_id, user_id)
    if not ordered_paper_ids:
        raise ValidationError("No paper order supplied.")
    for order, paper_id in enumerate(ordered_paper_ids, start=1):
        lakebase.update_paper_sequence(collection_id, paper_id, order)
    return {"status": "ok", "count": len(ordered_paper_ids)}


# =============================================================================
# Reading plan
# =============================================================================

def _sort_key(paper: dict):
    # 1. Publication year ascending — foundations before modern variants.
    # 2. (influence_score * 10 + citation_count) descending — seminal before niche.
    year = paper.get("publication_year") or 9999
    influence = paper.get("influence_score") or 0.0
    cites = paper.get("citation_count") or 0
    return (year, -((influence * 10) + cites))


def generate_reading_plan(user_id: str, collection_id: str) -> dict:
    """Sequence the collection pedagogically and persist the new sequence_order."""
    _require_collection(collection_id, user_id)
    papers = lakebase.get_collection_papers(collection_id)
    if not papers:
        raise ValidationError("Cannot generate a reading plan for an empty collection.")

    sequenced = sorted(papers, key=_sort_key)
    half = max(len(sequenced) // 2, 1)

    plan = []
    for order, paper in enumerate(sequenced, start=1):
        lakebase.update_paper_sequence(collection_id, paper["paper_id"], order)
        if order == 1:
            stage = "Foundations"
        elif order <= half:
            stage = "Core Architecture"
        else:
            stage = "Advanced Applications"
        plan.append({
            "sequence_order": order,
            "stage": stage,
            "paper_id": str(paper["paper_id"]),
            "title": paper["title"],
            "publication_year": paper.get("publication_year"),
            "venue": paper.get("venue"),
            "tldr": paper.get("tldr"),
            "citation_count": paper.get("citation_count", 0),
        })

    return {
        "collection_id": collection_id,
        "total_papers": len(plan),
        "strategy": "Chronological Foundations with Citation Impact Weighting",
        "reading_plan": plan,
    }
