"""dashboard/routes/progress.py — Reading progress board and note annotations."""

from flask import Blueprint, Response, jsonify, render_template, request, url_for

from middleware.capabilities import require_capability, require_quota
from middleware.auth import current_user_id
from routes.helpers import action_response, form_or_json, wants_json
from services import progress_service

bp = Blueprint("progress", __name__)


@bp.get("/progress")
def board():
    data = progress_service.get_board(current_user_id())
    return render_template("progress.html", board=data)


@bp.get("/notes")
@require_capability("notes:write")
def notes():
    """
    Everything you have written, in one place.

    Gated on notes:write rather than a read capability because there is no such
    thing as reading someone else's notes here - the page only ever shows your
    own, and an anonymous visitor has none and no way to make any. Sending them
    to the capability prompt is more honest than an empty page.
    """
    user_id = current_user_id()
    query = (request.args.get("q") or "").strip()
    tags = [t for t in request.args.getlist("tag") if t.strip()]
    scope = (request.args.get("scope") or "").strip()

    # One call either way. A filtered page is the same page with fewer rows,
    # not a separate search screen, so it renders through the same template.
    groups = (progress_service.find_notes(user_id, query, tags, scope)
              if (query or tags or scope) else progress_service.all_notes(user_id))
    summary = progress_service.notes_summary(groups)

    # The slide-over panel reads the same route rather than a parallel one, so
    # the page and the panel can never show different notes - one query, one
    # shape, negotiated by Accept. A second endpoint would be a second place to
    # remember when the shape changes.
    if wants_json():
        return jsonify({"papers": groups, "summary": summary})

    return render_template("notes.html", papers=groups, summary=summary,
                           query=query, active_tags=tags, scope=scope,
                           all_tags=progress_service.tag_cloud(user_id))


@bp.post("/notes")
@require_capability("notes:write")
def create_note():
    """
    Write a note that is not about any particular paper.

    `paper_id` is accepted but optional, so the same endpoint serves the panel
    opened from a paper page - where a note should attach to what you are
    reading - and the notes page itself, where there is nothing to attach to.
    """
    data = form_or_json("note_text", "paper_id", "title", "tags")
    note = progress_service.save_note(current_user_id(),
                                      (data.get("paper_id") or "").strip() or None,
                                      data["note_text"],
                                      data.get("title"),
                                      data.get("tags"))
    return action_response(
        {"note": {"note_id": str(note["note_id"]), "note_text": note["note_text"]}},
        redirect_to=url_for("progress.notes"),
        flash_message="Note saved.",
    )


@bp.post("/notes/<note_id>")
@require_capability("notes:write")
def edit_note(note_id: str):
    """Revise a note. 404 when it is not yours - see progress_service."""
    data = form_or_json("note_text", "title", "tags")
    note = progress_service.update_note(current_user_id(), note_id,
                                        data["note_text"], data.get("title"),
                                        data.get("tags"))
    return action_response(
        {"note": note},
        redirect_to=url_for("progress.notes"),
        flash_message="Note updated.",
    )


@bp.post("/notes/<note_id>/pin")
@require_capability("notes:write")
def pin_note(note_id: str):
    """
    Pin or unpin.

    The wanted state is sent, not a toggle: two tabs showing the same note
    would otherwise each flip from their own stale idea of it and land on
    whichever arrived last.
    """
    data = form_or_json("pinned")
    wanted = data.get("pinned")
    wanted = wanted if isinstance(wanted, bool) else str(wanted).lower() in ("1", "true", "on")

    note = progress_service.set_pinned(current_user_id(), note_id, wanted)
    return action_response(
        {"note": note},
        redirect_to=url_for("progress.notes"),
        flash_message="Pinned." if wanted else "Unpinned.",
    )


@bp.get("/notes/export")
@require_capability("notes:write")
def export_notes():
    """
    Everything currently on the page, as one Markdown file.

    It exports the FILTER, not the library: if you searched for "retriever" and
    then export, you get those notes. Exporting everything regardless would
    make the button mean something different from what the page in front of you
    shows, which is the kind of surprise that costs trust in an export.
    """
    user_id = current_user_id()
    query = (request.args.get("q") or "").strip()
    tags = [t for t in request.args.getlist("tag") if t.strip()]
    scope = (request.args.get("scope") or "").strip()

    groups = (progress_service.find_notes(user_id, query, tags, scope)
              if (query or tags or scope) else progress_service.all_notes(user_id))

    return Response(
        progress_service.to_markdown(groups),
        mimetype="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="notes.md"'},
    )


@bp.post("/notes/<note_id>/delete")
@require_capability("notes:write")
def remove_note(note_id: str):
    """
    Delete a note.

    POST rather than DELETE, and its own URL rather than a method override,
    because the page submits it as a plain form - the same reason the rest of
    this blueprint does. The browser confirms first; this does not, because a
    confirmation the server cannot see is not a confirmation it can enforce.
    """
    progress_service.delete_note(current_user_id(), note_id)
    return action_response(
        {"deleted": note_id},
        redirect_to=url_for("progress.notes"),
        flash_message="Note deleted.",
    )


@bp.post("/paper/<paper_id>/status")
@require_capability("progress:write")
def set_status(paper_id: str):
    data = form_or_json("status")
    progress = progress_service.set_status(current_user_id(), paper_id, data["status"])
    return action_response(
        {"progress": {"paper_id": paper_id, "status": progress["status"]}},
        redirect_to=request.referrer or url_for("progress.board"),
        flash_message=f"Marked as {progress['status'].replace('_', ' ')}.",
    )


@bp.post("/paper/<paper_id>/notes")
@require_capability("notes:write")
def add_note(paper_id: str):
    data = form_or_json("note_text")
    note = progress_service.save_note(current_user_id(), paper_id, data["note_text"])
    return action_response(
        {"note": {"note_id": str(note["note_id"]), "note_text": note["note_text"]}},
        redirect_to=url_for("search.paper_detail", paper_id=paper_id),
        flash_message="Note saved.",
    )
