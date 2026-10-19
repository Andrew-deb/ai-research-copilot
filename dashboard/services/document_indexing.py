"""Explicit private indexing and owner-scoped retrieval; no automatic enrollment."""

import json
import embedding
import upload_config
from config import EMBEDDING_MODEL, EMBEDDING_DIMENSION, EMBEDDING_QUERY_PREFIX
from exceptions import ValidationError, EmbeddingError
from repositories import document_index
from services import upload_service

try:
    from shared_resource.services.document_chunks import CHUNK_VERSION
except ModuleNotFoundError:
    from mcp_server.shared_resource.services.document_chunks import CHUNK_VERSION


def contract():
    if not upload_config.INDEX_MODEL_VERSION:
        raise ValidationError(
            "Configure a verified document embedding model version before indexing."
        )
    return json.dumps(
        {
            "model": EMBEDDING_MODEL,
            "version": upload_config.INDEX_MODEL_VERSION,
            "dimension": EMBEDDING_DIMENSION,
            "query_prefix": EMBEDDING_QUERY_PREFIX,
            "document_prefix": "search_document: ",
            "normalized": True,
            "chunks": CHUNK_VERSION,
        },
        sort_keys=True,
    )


def require_enabled():
    upload_service.require_enabled()
    if not upload_config.INDEXING_ENABLED:
        raise ValidationError(
            "Document indexing is disabled until the embedding service is verified."
        )
    return contract()


def request_index(owner, document_id):
    active = require_enabled()
    row = upload_service.owned(owner, document_id)
    if not row.get("retained_at") or row["status"] != "ready":
        raise ValidationError("Save a Ready document before indexing.")
    return document_index.queue(
        owner, str(row["document_id"]), active, upload_config.LIMITS
    )


def state(owner, document_id):
    row = upload_service.owned(owner, document_id)
    if not row.get("retained_at") or row["status"] != "ready":
        raise ValidationError("This saved document is unavailable.")
    result = document_index.state(owner, document_id)
    return {
        "status": result["status"],
        "error": result.get("error_code"),
        "compatible": (
            result.get("contract") == contract()
            if upload_config.INDEX_MODEL_VERSION
            else False
        ),
    }


def search(owner, query, document_id=None, limit=10):
    active = require_enabled()
    validate_query(query, limit)
    if not owner:
        raise ValidationError("Sign in to search private documents.")
    if document_id:
        row = upload_service.owned(owner, document_id)
        if not row.get("retained_at") or row["status"] != "ready":
            raise ValidationError("This saved document is unavailable.")
    vector = embedding.encode_query(query)
    return document_index.search(owner, vector, active, limit, document_id)


def selected_passages(owner, document_id, query):
    """Optional improvement inside an already authorized explicit file selection."""
    if not upload_config.INDEXING_ENABLED or not query.strip():
        return None
    if not upload_config.INDEX_MODEL_VERSION:
        return None
    status = document_index.state(owner, document_id)
    if status.get("status") != "ready" or status.get("contract") != contract():
        return None
    try:
        return [
            {"page": r["page"], "offset": r["offset"], "text": r["text"]}
            for r in search(owner, query, document_id, 4)
        ] or None
    except EmbeddingError:
        # Grounding remains usable with lexical selection if inference is down.
        return None


def validate_query(query, limit=10):
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 500
        or type(limit) is not int
        or not 1 <= limit <= 20
    ):
        raise ValidationError(
            "Use a document search of 1–500 characters and up to 20 results."
        )
