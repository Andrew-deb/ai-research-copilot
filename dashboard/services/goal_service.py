"""
dashboard/services/goal_service.py — Learning goal management.

Learning goals drive discovery: each goal is embedded (title + description) and
matched against the paper catalog with pgvector, so the goals page can show
"papers that match this goal" without the user running a search.
"""

import logging

from exceptions import GoalNotFoundError, ValidationError
from repositories import lakebase
from services import search_service

logger = logging.getLogger(__name__)

VALID_STATUSES = {"active", "completed", "archived"}
_MATCH_TOP_K = 8
_MATCH_MIN_SIMILARITY = 0.25


def _match_query(goal: dict) -> str:
    parts = [goal.get("title") or ""]
    if goal.get("description"):
        parts.append(goal["description"])
    return ". ".join(p.strip() for p in parts if p.strip())


def list_goals(user_id: str, with_matches: bool = False) -> list[dict]:
    """
    All goals for the user. `with_matches` runs a per-goal embed + vector search
    (one model call + one pgvector query *each*) — off by default so the goals
    page loads instantly; the per-goal "Show matching papers" control fetches
    them on demand via get_goal_detail.
    """
    goals = lakebase.get_learning_goals(user_id)
    if not with_matches:
        return goals

    for goal in goals:
        try:
            matches = search_service.semantic_paper_matches(
                _match_query(goal), top_k=_MATCH_TOP_K, min_similarity=_MATCH_MIN_SIMILARITY
            )
            goal["matched_paper_count"] = len(matches)
        except Exception as exc:  # never let a bad embed hide the goals list
            logger.debug("Goal match count failed for %s: %s", goal.get("goal_id"), exc)
            goal["matched_paper_count"] = None
    return goals


def create_goal(user_id: str, title: str, description: str | None = None) -> dict:
    if not title or not title.strip():
        raise ValidationError("Learning goal title cannot be empty.")
    if len(title.strip()) > 300:
        raise ValidationError("Learning goal title is too long (max 300 characters).")
    return lakebase.create_learning_goal(
        user_id=user_id, title=title.strip(), description=(description or "").strip() or None
    )


def get_goal_detail(user_id: str, goal_id: str) -> dict:
    """The goal plus the papers pgvector says are most relevant to it."""
    goal = lakebase.get_learning_goal(goal_id, user_id)
    if not goal:
        raise GoalNotFoundError(f"Learning goal '{goal_id}' not found.")

    matches: list[dict] = []
    try:
        matches = search_service.semantic_paper_matches(
            _match_query(goal), top_k=_MATCH_TOP_K, min_similarity=_MATCH_MIN_SIMILARITY
        )
    except Exception as exc:
        logger.debug("Goal match lookup failed for %s: %s", goal_id, exc)

    return {"goal": goal, "matched_papers": matches}


def set_status(user_id: str, goal_id: str, status: str) -> dict:
    clean = (status or "").strip().lower()
    if clean not in VALID_STATUSES:
        raise ValidationError(f"Invalid status '{status}'. Must be one of {sorted(VALID_STATUSES)}.")
    updated = lakebase.update_goal_status(goal_id, user_id, clean)
    if not updated:
        raise GoalNotFoundError(f"Learning goal '{goal_id}' not found.")
    return updated
