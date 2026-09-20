"""
dashboard/services/collection_service.py — Collections & reading plans.

Collection CRUD, paper membership, manual drag-reorder, and a reading-plan
generator. The plan heuristic is deliberately kept identical to
mcp_server/services/planning_service.generate_reading_plan so the dashboard
button and the agent tool produce the same ordering — each Databricks App
owns its own copy of the logic (no runtime dependency between the two apps).
"""

import logging

from exceptions import (
    CapabilityDeniedError,
    CollectionNotFoundError,
    PaperNotFoundError,
    ValidationError,
)
from repositories import lakebase

logger = logging.getLogger(__name__)


def _require_collection(collection_id: str, user_id: str | None) -> dict:
    """
    A collection this caller may READ: their own, or a curated demo one.

    Curated collections are owned by the system account, so an ownership-only
    lookup reported them as missing — they existed, were listed, and 404ed when
    opened. Read access is deliberately wider than write access.
    """
    if user_id:
        collection = lakebase.get_collection(collection_id, user_id)
        if collection:
            return collection

    curated = lakebase.get_curated_collection(collection_id)
    if curated:
        return curated

    raise CollectionNotFoundError(f"Collection '{collection_id}' not found.")


def _require_writable_collection(collection_id: str, user_id: str | None) -> dict:
    """
    A collection this caller may MODIFY.

    Curated collections are read-only for everyone, signed-in users included, and
    say so. Letting the ownership check reject them would produce "not found" for
    something plainly on screen — an error that sends the reader looking for a bug
    rather than telling them the rule.
    """
    collection = _require_collection(collection_id, user_id)
    if collection.get("is_curated"):
        raise CapabilityDeniedError(
            "This is a shared example collection, so it cannot be edited. "
            "Create your own collection to save papers into.",
            capability="library:write",
            requires_auth=False,
        )
    if not user_id or str(collection.get("user_id")) != str(user_id):
        raise CollectionNotFoundError(f"Collection '{collection_id}' not found.")
    return collection


# =============================================================================
# CRUD
# =============================================================================

def list_collections(user_id: str | None) -> list[dict]:
    """
    Curated examples first, then the caller's own.

    An anonymous visitor sees only the curated ones, which is what makes the page
    worth opening at all rather than an empty shell with a sign-in prompt.
    """
    curated = [{**c, "is_curated": True} for c in lakebase.get_curated_collections()]
    own = lakebase.get_collections(user_id) if user_id else []
    return curated + [c for c in own if not c.get("is_curated")]


def create_collection(user_id: str, name: str, description: str | None = None) -> dict:
    if not name or not name.strip():
        raise ValidationError("Collection name cannot be empty.")
    if len(name.strip()) > 200:
        raise ValidationError("Collection name is too long (max 200 characters).")
    return lakebase.create_collection(
        user_id=user_id, name=name.strip(), description=(description or "").strip() or None
    )


def get_collection_detail(user_id: str | None, collection_id: str) -> dict:
    collection = _require_collection(collection_id, user_id)
    papers = lakebase.get_collection_papers(collection_id)
    collection["papers"] = papers
    collection["paper_count"] = len(papers)
    return collection


# =============================================================================
# Membership
# =============================================================================

def add_paper(user_id: str, collection_id: str, paper_id: str) -> dict:
    _require_writable_collection(collection_id, user_id)
    if not lakebase.get_paper(paper_id):
        raise PaperNotFoundError(f"Paper '{paper_id}' not found in the catalog.")

    # Append at the end, with the position computed in SQL. Fetching every paper in
    # the collection just to take max(sequence_order) meant a full join across papers
    # and reading_progress crossing the wire to produce one integer.
    next_order = lakebase.append_paper_to_collection(collection_id, paper_id)
    return {"status": "ok", "collection_id": collection_id, "paper_id": paper_id, "sequence_order": next_order}


def remove_paper(user_id: str, collection_id: str, paper_id: str) -> dict:
    _require_writable_collection(collection_id, user_id)
    removed = lakebase.remove_paper_from_collection(collection_id, paper_id)
    return {"status": "ok", "rows_affected": removed}


def reorder(user_id: str, collection_id: str, ordered_paper_ids: list[str]) -> dict:
    """Persist a manual drag-reorder: position in the list becomes sequence_order."""
    _require_writable_collection(collection_id, user_id)
    if not ordered_paper_ids:
        raise ValidationError("No paper order supplied.")
    # One statement for the whole collection. Per-paper UPDATEs meant N round trips
    # to a remote database, and a failure part-way left the order half-applied.
    lakebase.update_paper_sequences(collection_id, ordered_paper_ids)
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
    _require_writable_collection(collection_id, user_id)
    papers = lakebase.get_collection_papers(collection_id)
    if not papers:
        raise ValidationError("Cannot generate a reading plan for an empty collection.")

    sequenced = sorted(papers, key=_sort_key)
    half = max(len(sequenced) // 2, 1)

    # Persist the whole ordering in one statement, before building the response.
    # This was an UPDATE per paper inside the loop below - a 20-paper collection
    # was 20 sequential round trips.
    lakebase.update_paper_sequences(collection_id, [p["paper_id"] for p in sequenced])

    plan = []
    for order, paper in enumerate(sequenced, start=1):
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
