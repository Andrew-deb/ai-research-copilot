"""dashboard/routes/goals.py — Learning goals (`/goals`)."""

from flask import Blueprint, jsonify, render_template, url_for

from dashboard.middleware.auth import current_user_id
from dashboard.routes.helpers import action_response, form_or_json
from dashboard.services import goal_service

bp = Blueprint("goals", __name__, url_prefix="/goals")


@bp.get("")
def list_goals():
    goals = goal_service.list_goals(current_user_id())
    return render_template("goals.html", goals=goals)


@bp.post("")
def create_goal():
    data = form_or_json("title", "description")
    goal = goal_service.create_goal(current_user_id(), data["title"], data.get("description"))
    return action_response(
        {"goal": goal},
        redirect_to=url_for("goals.list_goals"),
        flash_message=f"Learning goal '{goal['title']}' created.",
    )


@bp.get("/<goal_id>/matches")
def goal_matches(goal_id: str):
    """JSON — papers pgvector matches to this goal, for the expandable panel on goals.html."""
    detail = goal_service.get_goal_detail(current_user_id(), goal_id)
    return jsonify({
        "goal_id": goal_id,
        "matched_papers": [
            {
                "paper_id": str(p["paper_id"]),
                "title": p["title"],
                "publication_year": p.get("publication_year"),
                "venue": p.get("venue"),
                "similarity": p.get("similarity"),
            }
            for p in detail["matched_papers"]
        ],
    })


@bp.post("/<goal_id>/status")
def update_status(goal_id: str):
    data = form_or_json("status")
    goal = goal_service.set_status(current_user_id(), goal_id, data["status"])
    return action_response(
        {"goal": goal},
        redirect_to=url_for("goals.list_goals"),
        flash_message=f"Goal marked {goal['status']}.",
    )
