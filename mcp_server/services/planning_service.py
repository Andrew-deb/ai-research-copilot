"""
mcp_server/services/planning_service.py — Curriculum & Learning Plan Service.

Generates pedagogically sequenced reading plans, optimizes study paths based on
citation dependencies and publication chronology, and manages user learning goals.
"""

import logging
from typing import List, Optional

from mcp_server.exceptions import CollectionNotFoundError, GoalNotFoundError, ValidationError
from mcp_server.repositories import lakebase

logger = logging.getLogger(__name__)


def generate_reading_plan(collection_id: str, user_id: Optional[str] = None) -> dict:
    """
    Generate an optimal curriculum/reading plan for a collection of papers.
    Sequencing Algorithm:
    1. Foundational / High-impact papers first (high influence score & citation count).
    2. Earlier publication years as prerequisites before specialized modern architectures.
    3. Updates sequence_order in Lakebase and returns the pedagogical curriculum.
    """
    if not collection_id:
        raise ValidationError("Collection ID is required.")

    if user_id:
        col = lakebase.get_collection(collection_id, user_id)
        if not col:
            raise CollectionNotFoundError(f"Collection '{collection_id}' not found.")

    papers = lakebase.get_collection_papers(collection_id)
    if not papers:
        raise ValidationError("Cannot generate reading plan for an empty collection.")

    # Sorting key:
    # 1. Publication year (ascending - foundational first)
    # 2. Influence score / Citation count (descending - seminal works before niche variants)
    def sort_key(p):
        year = p.get("publication_year") or 9999
        influence = p.get("influence_score") or 0.0
        cites = p.get("citation_count") or 0
        score = (influence * 10) + cites
        return (year, -score)

    sequenced_papers = sorted(papers, key=sort_key)

    plan_items = []
    for order, paper in enumerate(sequenced_papers, 1):
        paper_id = paper["paper_id"]
        lakebase.update_paper_sequence(collection_id, paper_id, order)
        
        stage = "Foundations" if order == 1 else ("Core Architecture" if order <= len(sequenced_papers)//2 else "Advanced Applications")
        plan_items.append({
            "sequence_order": order,
            "stage": stage,
            "paper_id": paper_id,
            "title": paper["title"],
            "publication_year": paper.get("publication_year"),
            "venue": paper.get("venue"),
            "tldr": paper.get("tldr"),
            "citation_count": paper.get("citation_count", 0),
        })

    return {
        "collection_id": collection_id,
        "total_papers": len(plan_items),
        "reading_plan": plan_items,
        "strategy": "Chronological Foundations with Citation Impact Weighting"
    }


def reorder_reading_plan(collection_id: str, paper_orders: List[dict], user_id: Optional[str] = None) -> dict:
    """
    Manually update paper reading sequences.
    paper_orders is a list of {'paper_id': str, 'sequence_order': int}.
    """
    if not collection_id or not paper_orders:
        raise ValidationError("Collection ID and paper_orders are required.")

    for item in paper_orders:
        pid = item.get("paper_id")
        seq = item.get("sequence_order", 0)
        if pid:
            lakebase.update_paper_sequence(collection_id, pid, seq)

    return {"status": "success", "message": "Reading plan reordered."}


def create_learning_goal(user_id: str, title: str, description: Optional[str] = None) -> dict:
    """Create a new learning goal for a user."""
    if not user_id:
        raise ValidationError("User ID is required.")
    if not title or not title.strip():
        raise ValidationError("Learning goal title cannot be empty.")

    return lakebase.create_learning_goal(user_id=user_id, title=title.strip(), description=description)


def get_learning_goals(user_id: str, status: Optional[str] = None) -> List[dict]:
    """Retrieve learning goals for a user (optionally filtered by active/completed/archived)."""
    if not user_id:
        raise ValidationError("User ID is required.")
    return lakebase.get_learning_goals(user_id=user_id, status=status)


def update_goal_status(goal_id: str, user_id: str, status: str) -> dict:
    """Update goal status to 'active', 'completed', or 'archived'."""
    valid_statuses = {"active", "completed", "archived"}
    if status not in valid_statuses:
        raise ValidationError(f"Invalid status '{status}'. Must be one of {valid_statuses}")

    updated = lakebase.update_goal_status(goal_id=goal_id, user_id=user_id, status=status)
    if not updated:
        raise GoalNotFoundError(f"Goal '{goal_id}' not found for user.")
    return updated
