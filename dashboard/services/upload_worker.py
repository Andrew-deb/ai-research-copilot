"""Durable job runner. Restartable database leases, bounded parser subprocesses."""

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import upload_config
from repositories import uploads
from services import upload_storage

logger = logging.getLogger(__name__)


def extract_bytes(data, media_type):
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "LANG": "C.UTF-8",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="alfred-parse-") as directory:
            process = subprocess.run(
                [sys.executable, "-m", "services.upload_extractor", media_type],
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=20,
                check=False,
                cwd=directory,
            )
        if process.returncode != 0:
            return None, "extraction_limit"
        output = json.loads(process.stdout)
        if not output.get("ok"):
            return None, output.get("error", "invalid_document")
        return output["result"], None
    except (subprocess.TimeoutExpired, ValueError, KeyError):
        return None, "extraction_limit"


def process_one():
    job = uploads.claim()
    if not job:
        return False
    try:
        storage = upload_storage.for_location(job["storage_location_id"])
        if job["action"] == "delete":
            storage.delete(job["object_key"])
            storage.delete(job["artifact_key"])
            uploads.finish(job)
            return True
        if job["attempts"] > 3:
            uploads.finish(job, error="processing_interrupted")
            return True
        data = storage.read(job["object_key"], upload_config.LIMITS.max_file_bytes)
        if hashlib.sha256(data).hexdigest() != job["content_hash"]:
            uploads.finish(job, error="invalid_document")
            return True
        result, error = extract_bytes(data, job["media_type"])
        if error:
            uploads.finish(job, error=error)
            return True
        artifact = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(artifact) > upload_config.LIMITS.max_artifact_bytes:
            uploads.finish(job, error="extraction_limit")
            return True
        storage.put(job["artifact_key"], artifact, "application/json")
        uploads.finish(
            job,
            result={
                "artifact_bytes": len(artifact),
                "artifact_hash": hashlib.sha256(artifact).hexdigest(),
                "page_count": len(result["pages"]),
                "version": result["version"],
            },
        )
    except upload_storage.StorageUnavailable:
        uploads.finish(job, error="storage_unavailable")
    except Exception:
        # Do not log SDK exception text, filenames or private content.
        logger.warning(
            "Private upload job failed; its database lease permits recovery."
        )
        # Keep lease/state for restart recovery rather than losing the queued job.
    return True


def run(stop):
    while not stop.is_set():
        try:
            uploads.enqueue_cleanup()
            worked = process_one()
        except Exception:
            logger.warning(
                "Private upload worker unavailable; durable jobs remain queued."
            )
            worked = False
        stop.wait(1 if worked else 10)


def start():
    stop = threading.Event()
    thread = threading.Thread(
        target=run, args=(stop,), name="private-upload-worker", daemon=True
    )
    thread.start()
    return stop


if __name__ == "__main__":
    if not upload_config.ENABLED:
        raise SystemExit(
            "Set UPLOADS_ENABLED after applying the migration and configuring private storage."
        )
    upload_config.validate()
    upload_storage.for_location(upload_config.LOCATION_ID)
    run(threading.Event())
