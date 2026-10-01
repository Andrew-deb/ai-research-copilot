"""Research compatibility adapters; domain operations take an explicit repository."""

from typing import List, Optional

from shared_resource.services import planning_service as domain
from shared_resource.services import goal_service as goals
from repositories import lakebase


def generate_reading_plan(collection_id: str, user_id: Optional[str] = None) -> dict:
    """Generate an optimal curriculum/reading plan for a collection of papers.
    Sequencing Algorithm:
    1. Foundational / High-impact papers first (high influence score & citation count).
    2. Earlier publication years as prerequisites before specialized modern architectures.
    3. Updates sequence_order in Lakebase and returns the pedagogical curriculum.
    """
    return domain.generate_reading_plan(lakebase, collection_id=collection_id, user_id=user_id)


def reorder_reading_plan(collection_id: str, paper_orders: List[dict], user_id: Optional[str] = None) -> dict:
    """Manually update paper reading sequences.
    paper_orders is a list of {'paper_id': str, 'sequence_order': int}.
    """
    return domain.reorder_reading_plan(
        lakebase,
        collection_id=collection_id,
        paper_orders=paper_orders,
        user_id=user_id,
    )


def create_learning_goal(user_id: str, title: str, description: Optional[str] = None) -> dict:
    """Create a new learning goal for a user."""
    return goals.create_learning_goal(
        lakebase,
        user_id=user_id,
        title=title,
        description=description,
    )


def get_learning_goals(user_id: str, status: Optional[str] = None) -> List[dict]:
    """Retrieve learning goals for a user (optionally filtered by active/completed/archived)."""
    return goals.get_learning_goals(lakebase, user_id=user_id, status=status)


def update_goal_status(goal_id: str, user_id: str, status: str) -> dict:
    """Update goal status to 'active', 'completed', or 'archived'."""
    return goals.update_goal_status(lakebase, goal_id=goal_id, user_id=user_id, status=status)
