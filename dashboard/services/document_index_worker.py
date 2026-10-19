"""Independent leased index worker; never blocks the upload/expiry worker."""

import logging
import threading
import embedding
import upload_config
from repositories import document_index
from services import document_artifacts, document_indexing

try:
    from shared_resource.services.document_chunks import split
except ModuleNotFoundError:
    from mcp_server.shared_resource.services.document_chunks import split

logger = logging.getLogger(__name__)


def process_one(stop=None):
    if not upload_config.INDEXING_ENABLED or not upload_config.INDEX_MODEL_VERSION:
        return False
    job = document_index.claim()
    if not job:
        return False
    try:
        if (
            job["contract"] != document_indexing.contract()
            or job["artifact_hash"] != job["indexed_hash"]
        ):
            document_index.finish(job, error="contract_changed")
            return True
        if job["attempts"] >= 3:
            document_index.finish(job, error="processing_interrupted")
            return True
        chunks = split(document_artifacts.load(job))
        for chunk in chunks:
            if (stop and stop.is_set()) or not document_index.renew(job):
                return True
            chunk["vector"] = embedding.encode_document(chunk["text"])
        if not document_index.renew(job):
            return True
        document_index.finish(job, chunks=chunks)
    except Exception as error:
        # Never expose private text, provider credentials or raw SDK responses.
        logger.warning("Document indexing failed (%s).", type(error).__name__)
        document_index.finish(job, error="indexing_failed")
    return True


def run(stop):
    while not stop.is_set():
        try:
            worked = process_one(stop)
        except Exception:
            worked = False
            logger.warning(
                "Document indexing worker unavailable; leases preserve queued work."
            )
        stop.wait(1 if worked else 10)


def start():
    stop = threading.Event()
    threading.Thread(
        target=run, args=(stop,), name="document-index-worker", daemon=True
    ).start()
    return stop
