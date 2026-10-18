"""Authenticated private files: thin HTTP adapters; no storage or parsing logic."""

import io
from flask import Blueprint, jsonify, request, send_file, render_template
from exceptions import ValidationError
from middleware.auth import require_user_id
from middleware.capabilities import require_capability, UPLOADS_WRITE
from services import (
    upload_service,
    conversation_service,
    attachment_context,
    document_library,
)
import upload_config

bp = Blueprint("uploads", __name__)


@bp.post("/uploads/conversations")
@require_capability(UPLOADS_WRITE)
def start_conversation():
    upload_service.require_enabled()
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode", "research")
    if mode not in ("research", "wick"):
        raise ValidationError("Unknown chat mode.")
    conversation = conversation_service.start(
        require_user_id(),
        "Private files",
        origin="assistant" if mode == "wick" else "agent",
    )
    return jsonify(conversation_id=str(conversation["conversation_id"])), 201


@bp.post("/uploads")
@require_capability(UPLOADS_WRITE)
def upload_file():
    upload_service.require_enabled()
    file = request.files.get("file")
    if not file:
        raise ValidationError("Choose one file to upload.")
    return (
        jsonify(
            upload_service.upload(
                require_user_id(), request.form.get("conversation_id"), file
            )
        ),
        202,
    )


@bp.get("/uploads/conversations/<uuid:conversation_id>")
@require_capability(UPLOADS_WRITE)
def list_files(conversation_id):
    upload_service.require_enabled()
    return jsonify(
        items=upload_service.list_files(require_user_id(), str(conversation_id))
    )


@bp.get("/uploads/conversations/<uuid:conversation_id>/download")
@require_capability(UPLOADS_WRITE)
def download_all(conversation_id):
    from services import upload_archive

    output = upload_archive.build(require_user_id(), str(conversation_id))
    try:
        response = send_file(
            output,
            mimetype="application/zip",
            as_attachment=True,
            download_name="conversation-files.zip",
            max_age=0,
        )
    except Exception:
        output.close()
        raise
    response.call_on_close(output.close)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/uploads/<uuid:document_id>/download")
@require_capability(UPLOADS_WRITE)
def download_file(document_id):
    upload_service.require_enabled()
    row, data = upload_service.download(require_user_id(), str(document_id))
    response = send_file(
        io.BytesIO(data),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=row["filename"],
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/uploads/<uuid:document_id>/preview")
@require_capability(UPLOADS_WRITE)
def preview_original(document_id):
    """Serve only validated PDFs inline, through the same owned download boundary."""
    upload_service.require_enabled()
    row, data = upload_service.download(require_user_id(), str(document_id))
    if not row["filename"].lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
        raise ValidationError("Only PDF files support this preview.")
    response = send_file(
        io.BytesIO(data),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=row["filename"],
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'self'; sandbox"
    )
    return response


@bp.delete("/uploads/<uuid:document_id>")
@require_capability(UPLOADS_WRITE)
def delete_file(document_id):
    upload_service.require_enabled()
    return jsonify(upload_service.delete(require_user_id(), str(document_id))), 202


@bp.post("/uploads/<uuid:document_id>/retry")
@require_capability(UPLOADS_WRITE)
def retry_file(document_id):
    upload_service.require_enabled()
    return jsonify(upload_service.retry(require_user_id(), str(document_id))), 202


def register_upload_context(app):
    @app.context_processor
    def inject():
        return {
            "private_uploads_enabled": upload_config.ENABLED,
            "document_indexing_enabled": upload_config.INDEXING_ENABLED,
            "upload_max_mib": upload_config.LIMITS.max_file_bytes // (1024 * 1024),
            "upload_retention_days": upload_config.LIMITS.retention_days,
        }


@bp.get("/uploads/<uuid:document_id>/pages/<int:page>")
@require_capability(UPLOADS_WRITE)
def preview_page(document_id, page):
    from flask import render_template

    owner = require_user_id()
    result = attachment_context.preview(owner, str(document_id), page)
    response = render_template("upload_page.html", document=result, page=page)
    return (
        response,
        200,
        {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@bp.post("/uploads/<uuid:document_id>/retain")
@require_capability(UPLOADS_WRITE)
def retain_file(document_id):
    return jsonify(document_library.save(require_user_id(), str(document_id)))


@bp.get("/documents")
@require_capability(UPLOADS_WRITE)
def documents_page():
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        raise ValidationError("Choose a valid documents page.") from None
    query = request.args.get("q", "")
    mode = request.args.get("mode", "keyword")
    if mode not in ("keyword", "semantic"):
        raise ValidationError("Unknown document search mode.")
    if query and mode == "semantic":
        from services import document_indexing, quota_service
        from middleware.capabilities import consume_quota

        document_indexing.require_enabled()
        document_indexing.validate_query(query)
        consume_quota(quota_service.SEMANTIC_SEARCH)
        results = document_indexing.search(require_user_id(), query)
        return (
            render_template(
                "documents.html",
                documents={"items": [], "page": 1, "has_more": False},
                semantic_results=results,
                query=query,
                mode=mode,
            ),
            200,
            {"Cache-Control": "private, no-store"},
        )
    documents = (
        document_library.list_documents(require_user_id(), page)
        if not query
        else document_library.options(require_user_id(), query, page)
    )
    if query:
        documents = {
            "items": documents["items"],
            "page": page,
            "has_more": bool(documents["next_cursor"]),
        }
    return (
        render_template("documents.html", documents=documents, query=query, mode=mode),
        200,
        {"Cache-Control": "private, no-store"},
    )


@bp.get("/documents/<uuid:document_id>")
@require_capability(UPLOADS_WRITE)
def document_view(document_id):
    upload_service.require_enabled()
    row = upload_service.owned(require_user_id(), str(document_id))
    if not row.get("retained_at") or row["status"] != "ready":
        raise ValidationError("This saved document is unavailable.")
    from services import collection_service

    return (
        render_template(
            "document.html",
            document=upload_service.receipt(row),
            collections=collection_service.collections_for(require_user_id())["own"],
        ),
        200,
        {"Cache-Control": "private, no-store"},
    )


@bp.get("/documents/options")
@require_capability(UPLOADS_WRITE)
def document_options():
    try:
        page = int(request.args.get("cursor") or "1")
    except ValueError:
        raise ValidationError("Invalid document page.") from None
    return (
        jsonify(
            document_library.options(require_user_id(), request.args.get("q", ""), page)
        ),
        200,
        {"Cache-Control": "private, no-store"},
    )


@bp.get("/uploads/<uuid:document_id>/metadata")
@require_capability(UPLOADS_WRITE)
def document_metadata(document_id):
    upload_service.require_enabled()
    return jsonify(
        upload_service.receipt(
            upload_service.owned(require_user_id(), str(document_id))
        )
    )


@bp.post("/documents/<uuid:document_id>/index")
@require_capability(UPLOADS_WRITE)
def index_document(document_id):
    from services import document_indexing

    return (
        jsonify(document_indexing.request_index(require_user_id(), str(document_id))),
        202,
    )


@bp.get("/documents/<uuid:document_id>/index")
@require_capability(UPLOADS_WRITE)
def index_status(document_id):
    upload_service.require_enabled()
    from services import document_indexing

    return (
        jsonify(document_indexing.state(require_user_id(), str(document_id))),
        200,
        {"Cache-Control": "private, no-store"},
    )


@bp.get("/documents/search")
@require_capability(UPLOADS_WRITE)
def search_documents():
    from services import document_indexing
    from services import quota_service
    from middleware.capabilities import consume_quota

    query = request.args.get("q", "")
    document_indexing.require_enabled()
    document_indexing.validate_query(query)
    consume_quota(quota_service.SEMANTIC_SEARCH)
    results = document_indexing.search(require_user_id(), query)
    return jsonify(items=results), 200, {"Cache-Control": "private, no-store"}


@bp.post("/documents/<uuid:document_id>/collections")
@require_capability(UPLOADS_WRITE)
def organise_document(document_id):
    from services import document_collections
    from routes.helpers import action_response, form_or_json
    from flask import url_for

    result = document_collections.modify(
        require_user_id(),
        form_or_json("collection_id").get("collection_id"),
        str(document_id),
    )
    return action_response(
        result,
        redirect_to=url_for("uploads.document_view", document_id=document_id),
        flash_message="Document added to collection.",
    )
