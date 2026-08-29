"""dashboard/routes/progress.py — Reading progress board and note annotations."""

from flask import Blueprint, render_template, request, url_for

from dashboard.middleware.auth import current_user_id
from dashboard.routes.helpers import action_response, form_or_json
from dashboard.services import progress_service

bp = Blueprint("progress", __name__)


@bp.get("/progress")
def board():
    data = progress_service.get_board(current_user_id())
    return render_template("progress.html", board=data)


@bp.post("/paper/<paper_id>/status")
def set_status(paper_id: str):
    data = form_or_json("status")
    progress = progress_service.set_status(current_user_id(), paper_id, data["status"])
    return action_response(
        {"progress": {"paper_id": paper_id, "status": progress["status"]}},
        redirect_to=request.referrer or url_for("progress.board"),
        flash_message=f"Marked as {progress['status'].replace('_', ' ')}.",
    )


@bp.post("/paper/<paper_id>/notes")
def add_note(paper_id: str):
    data = form_or_json("note_text")
    note = progress_service.save_note(current_user_id(), paper_id, data["note_text"])
    return action_response(
        {"note": {"note_id": str(note["note_id"]), "note_text": note["note_text"]}},
        redirect_to=url_for("search.paper_detail", paper_id=paper_id),
        flash_message="Note saved.",
    )
