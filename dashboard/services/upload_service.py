"""Private attachment lifecycle; repositories persist, providers store bytes."""

import hashlib
import unicodedata
from uuid import UUID, uuid4
from exceptions import ValidationError, ExternalAPIError, UploadNotFoundError
from repositories import lakebase, uploads
from services import upload_storage
import upload_config

ERRORS = {
    "no_extractable_text": "No selectable text found. Scanned PDFs need OCR, which is not supported yet.",
    "encrypted_pdf": "Password-protected PDFs are not supported.",
    "too_many_pages": "PDFs must have at most 200 pages.",
    "too_much_text": "This document contains too much text for the current limit.",
    "invalid_text": "Use a UTF-8 text or Markdown file.",
    "invalid_document": "The document could not be parsed.",
    "extraction_limit": "Processing exceeded its time or memory limit.",
    "storage_unavailable": "Private storage is unavailable. You can retry before expiry.",
    "processing_interrupted": "Processing was interrupted repeatedly. You can retry before expiry.",
}


def require_enabled():
    if not upload_config.ENABLED:
        raise ExternalAPIError("Private uploads are not available yet.")
    upload_config.validate()


def identifier(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Invalid file or conversation reference.") from None


def owned(owner, document_id):
    row = uploads.get_owned(owner, identifier(document_id))
    if not row:
        raise UploadNotFoundError("That file or conversation is unavailable.")
    return row


def receipt(row):
    expired = row.get("expired", False)
    return {
        "document_id": str(row["document_id"]),
        "conversation_id": (
            str(row["conversation_id"]) if row.get("conversation_id") else None
        ),
        "filename": row["filename"],
        "size_bytes": row["original_bytes"],
        "status": (
            "expired"
            if expired and row["status"] not in ("deleted", "deleting")
            else row["status"]
        ),
        "expires_at": row["expires_at"].isoformat(),
        "page_count": row.get("page_count"),
        "error": ERRORS.get(row.get("error_code")),
        "agent_access": False,
    }


def list_files(owner, conversation_id):
    cid = identifier(conversation_id)
    if not lakebase.get_conversation(owner, cid):
        raise UploadNotFoundError("That file or conversation is unavailable.")
    return [receipt(row) for row in uploads.list_owned(owner, cid)]


def validate_file(filename, data):
    filename = unicodedata.normalize("NFC", filename or "").strip()
    if (
        not filename
        or len(filename) > 160
        or any(ord(c) < 32 for c in filename)
        or "/" in filename
        or "\\" in filename
    ):
        raise ValidationError(
            "Choose a file with a plain filename of at most 160 characters."
        )
    suffix = filename.rsplit(".", 1)[-1].lower()
    types = {"pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown"}
    if (
        suffix not in types
        or not data
        or len(data) > upload_config.LIMITS.max_file_bytes
    ):
        raise ValidationError(
            "Upload a nonempty PDF, TXT or Markdown file up to the configured size limit."
        )
    if suffix == "pdf" and not data.startswith(b"%PDF-"):
        raise ValidationError("The file does not have a valid PDF signature.")
    if suffix != "pdf":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError("Text files must use UTF-8.") from None
        if "\x00" in text or any(ord(c) < 32 and c not in "\r\n\t" for c in text):
            raise ValidationError("Binary files cannot be uploaded as text.")
    return filename, types[suffix]


def upload(owner, conversation_id, file):
    require_enabled()
    cid = identifier(conversation_id)
    # Validate ownership before reading/storing user-controlled bytes.
    if not lakebase.get_conversation(owner, cid):
        raise UploadNotFoundError("That file or conversation is unavailable.")
    data = file.stream.read(upload_config.LIMITS.max_file_bytes + 1)
    filename, media_type = validate_file(file.filename, data)
    try:
        storage = upload_storage.for_location(upload_config.LOCATION_ID)
    except upload_storage.StorageUnavailable as exc:
        raise ExternalAPIError(str(exc)) from None
    doc = str(uuid4())
    row = uploads.reserve(
        owner,
        cid,
        {
            "document_id": doc,
            "filename": filename,
            "media_type": media_type,
            "storage_location_id": upload_config.LOCATION_ID,
            "object_key": f"private/{doc}/original",
            "artifact_key": f"private/{doc}/text-v1.json",
            "original_bytes": len(data),
            "content_hash": hashlib.sha256(data).hexdigest(),
        },
        upload_config.LIMITS,
    )
    try:
        storage.put(row["object_key"], data, media_type)
        uploads.queue_extraction(owner, doc)
    except Exception:
        # Keep the logical keys and reservation until durable cleanup confirms deletion.
        uploads.request_delete(owner, doc)
        raise ExternalAPIError(
            "The upload could not be completed. Cleanup is queued; please try again later."
        ) from None
    finally:
        # Even cancellation waits for the provider write to finish before cleanup.
        uploads.release_upload(owner, doc)
    row["status"] = "queued"
    return receipt(row)


def download(owner, document_id):
    row = owned(owner, document_id)
    if row.get("expired") or row["status"] not in (
        "ready",
        "failed",
        "queued",
        "processing",
    ):
        raise ValidationError("This file is unavailable or has expired.")
    try:
        content = upload_storage.for_location(row["storage_location_id"]).read(
            row["object_key"], upload_config.LIMITS.max_file_bytes
        )
    except upload_storage.StorageUnavailable as exc:
        raise ExternalAPIError(str(exc)) from None
    if hashlib.sha256(content).hexdigest() != row["content_hash"]:
        raise ExternalAPIError("File integrity verification failed.")
    return row, content


def delete(owner, document_id):
    row = owned(owner, document_id)
    if row["status"] == "deleted":
        return {"status": "deleted", "document_id": str(row["document_id"])}
    uploads.request_delete(owner, str(row["document_id"]))
    return {"status": "deleting", "document_id": str(row["document_id"])}


def retry(owner, document_id):
    row = owned(owner, document_id)
    uploads.retry(owner, str(row["document_id"]))
    return {"status": "queued", "document_id": str(row["document_id"])}
