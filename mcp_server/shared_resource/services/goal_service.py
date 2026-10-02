"""Shared goal operations, independent of MCP and Flask."""

from typing import List, Optional

from ..exceptions import GoalNotFoundError, ValidationError


def create_learning_goal(repository, user_id: str, title: str, description: Optional[str] = None) -> dict:
    """Create a new learning goal for a user."""
    if not user_id:
        raise ValidationError("User ID is required.")
    if not isinstance(title, str) or not title.strip():
        raise ValidationError("Learning goal title cannot be empty.")

    if len(title.strip()) > 300:
        raise ValidationError("Learning goal title is too long (max 300 characters).")
    if description is not None and not isinstance(description, str):
        raise ValidationError("Learning goal description must be text.")
    return repository.create_learning_goal(
        user_id=user_id, title=title.strip(), description=(description or "").strip() or None)



def get_learning_goals(repository, user_id: str, status: Optional[str] = None) -> List[dict]:
    """Retrieve learning goals for a user (optionally filtered by active/completed/archived)."""
    if not user_id:
        raise ValidationError("User ID is required.")
    return repository.get_learning_goals(user_id=user_id, status=status)



def update_goal_status(repository, goal_id: str, user_id: str, status: str) -> dict:
    """Update goal status to 'active', 'completed', or 'archived'."""
    if not user_id:
        raise ValidationError("User ID is required.")
    if not isinstance(status, str):
        raise ValidationError("Learning goal status must be text.")
    status = status.strip().lower()
    valid_statuses = {"active", "completed", "archived"}
    if status not in valid_statuses:
        raise ValidationError(f"Invalid status '{status}'. Must be one of {valid_statuses}")

    updated = repository.update_goal_status(goal_id=goal_id, user_id=user_id, status=status)
    if not updated:
        raise GoalNotFoundError(f"Goal '{goal_id}' not found for user.")
    return updated
