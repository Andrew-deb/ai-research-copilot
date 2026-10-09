"""Authenticated private files: thin HTTP adapters; no storage or parsing logic."""

import io
from flask import Blueprint, jsonify, request, send_file
from exceptions import ValidationError
from middleware.auth import require_user_id
from middleware.capabilities import require_capability, UPLOADS_WRITE
from services import upload_service, conversation_service
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
            "upload_max_mib": upload_config.LIMITS.max_file_bytes // (1024 * 1024),
            "upload_retention_days": upload_config.LIMITS.retention_days,
        }
