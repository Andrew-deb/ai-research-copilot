"""dashboard/routes/collections.py — Collections and reading plans."""

from flask import Blueprint, jsonify, render_template, request, url_for

from middleware.capabilities import require_capability, require_quota
from middleware.auth import current_user_id
from routes.helpers import action_response, form_or_json
from services import collection_service, workspace_paper_service

bp = Blueprint("collections", __name__)


@bp.get("/collections")
def list_collections():
    split = collection_service.collections_for(current_user_id())
    return render_template("collections.html",
                           collections=split["own"],
                           examples=split["examples"])


@bp.post("/collections")
@require_capability("library:write")
def create_collection():
    data = form_or_json("name", "description")
    collection = collection_service.create_collection(
        current_user_id(), data["name"], data.get("description")
    )
    return action_response(
        {"collection": collection},
        redirect_to=url_for("collections.collection_detail", collection_id=collection["collection_id"]),
        flash_message=f"Collection '{collection['name']}' created.",
    )


@bp.get("/collection/<collection_id>")
def collection_detail(collection_id: str):
    detail = collection_service.get_collection_detail(current_user_id(), collection_id)
    return render_template("collection_detail.html", collection=detail)


@bp.post("/collection/<collection_id>/papers")
@require_capability("library:write")
def add_paper(collection_id: str):
    data = form_or_json("paper_id")
    result = collection_service.add_paper(current_user_id(), collection_id, data["paper_id"])
    return action_response(
        result,
        redirect_to=url_for("collections.collection_detail", collection_id=collection_id),
        flash_message="Paper added to collection.",
    )


@bp.post("/collection/<collection_id>/papers/<paper_id>/remove")
@require_capability("library:write")
def remove_paper(collection_id: str, paper_id: str):
    result = collection_service.remove_paper(current_user_id(), collection_id, paper_id)
    return action_response(
        result,
        redirect_to=url_for("collections.collection_detail", collection_id=collection_id),
        flash_message="Paper removed from collection.",
    )


@bp.post("/collection/<collection_id>/plan")
@require_capability("library:write")
def generate_plan(collection_id: str):
    plan = collection_service.generate_reading_plan(current_user_id(), collection_id)
    return action_response(
        plan,
        redirect_to=url_for("collections.collection_detail", collection_id=collection_id),
        flash_message=f"Reading plan generated for {plan['total_papers']} papers.",
    )


@bp.post("/collection/<collection_id>/reorder")
@require_capability("library:write")
def reorder(collection_id: str):
    payload = request.get_json(silent=True) or {}
    ordered = payload.get("ordered_paper_ids") or request.form.getlist("ordered_paper_ids")
    result = collection_service.reorder(current_user_id(), collection_id, ordered)
    return action_response(
        result,
        redirect_to=url_for("collections.collection_detail", collection_id=collection_id),
        flash_message="Reading order updated.",
    )


@bp.get("/collection/<collection_id>/paper-options")
@require_capability("library:write")
def paper_options(collection_id):
    collection_service._require_writable_collection(collection_id, current_user_id())
    scope = request.args.get("scope", "corpus")
    if scope not in {"corpus", "saved"}:
        from exceptions import ValidationError
        raise ValidationError("Unknown paper search scope.")
    result = workspace_paper_service.search(current_user_id(), request.args.get("q", ""),
                                           scope == "saved", cursor=request.args.get("cursor"))
    existing = {str(p["paper_id"]) for p in collection_service.get_collection_detail(
        current_user_id(), collection_id)["papers"]}
    for paper in result["items"]:
        paper["already_added"] = str(paper["paper_id"]) in existing
        paper["url"] = url_for("search.paper_detail", paper_id=paper["paper_id"])
    return jsonify(result)
