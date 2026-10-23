"""Databricks API App probe. Databricks' ingress enforces OAuth/CAN USE."""

import logging
import os
from pathlib import Path
import resource
import re
import tempfile
import threading
import time

from flask import Flask, jsonify, request
from runtime import DIMENSION, MODEL, Encoder


def create_app(encoder, version):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536

    @app.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def health():
        return jsonify(service="embedding-probe")

    @app.get("/api/ready")
    def ready():
        return jsonify(
            state=encoder.state,
            model=MODEL,
            dimension=DIMENSION,
            version=version,
            load_seconds=encoder.load_seconds,
            peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        ), (200 if encoder.state == "ready" else 503)

    @app.post("/api/embed")
    def embed():
        payload = request.get_json(silent=True)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"text", "kind"}
            or payload.get("kind") not in ("query", "document")
            or not isinstance(payload.get("text"), str)
            or not payload["text"].strip()
            or len(payload["text"]) > 16000
        ):
            return (
                jsonify(
                    error="Provide text (1–16000 characters) and query/document kind"
                ),
                400,
            )
        if encoder.state != "ready":
            return jsonify(error="Embedding model unavailable"), 503
        if not encoder.lock.acquire(blocking=False):
            return jsonify(error="Embedding model busy"), 429, {"Retry-After": "1"}
        try:
            started = time.perf_counter()
            vector = encoder.encode(payload["text"], payload["kind"])
            return jsonify(
                embedding=vector,
                version=version,
                dimension=DIMENSION,
                elapsed_seconds=time.perf_counter() - started,
            )
        except ValueError:
            return jsonify(error="Input or embedding violates the model contract"), 422
        except Exception:
            logging.error("Embedding inference failed")
            return jsonify(error="Embedding inference failed"), 503
        finally:
            encoder.lock.release()

    return app


def main():
    path = Path(os.environ["EMBEDDING_MODEL_PATH"])
    version = os.environ["EMBEDDING_MODEL_VERSION"].strip()
    source = os.getenv("EMBEDDING_VOLUME_PATH", "")
    if not re.fullmatch(r"[0-9a-f]{64}", version) or not source.startswith("/Volumes/"):
        raise ValueError("A Volume snapshot and its SHA256 fingerprint are required")
    encoder = Encoder()

    def load():
        try:
            if source:
                from databricks.sdk import WorkspaceClient
                from staging import stage_model

                path.mkdir(parents=True, exist_ok=True)
                staged = Path(tempfile.mkdtemp(prefix="snapshot-", dir=path)) / "model"
                fingerprint = stage_model(source, staged, WorkspaceClient())
                logging.warning("Staged model snapshot sha256=%s", fingerprint)
                if fingerprint != version:
                    raise ValueError(
                        "Staged snapshot does not match configured version"
                    )
            encoder.load(str(staged if source else path))
        except Exception:
            encoder.state = "failed"
            logging.error(
                "Embedding model load failed; inspect staged model and dependencies"
            )

    threading.Thread(target=load, daemon=True).start()
    from waitress import serve

    serve(
        create_app(encoder, version),
        host="0.0.0.0",
        port=int(os.getenv("DATABRICKS_APP_PORT", "8000")),
        threads=4,
        max_request_body_size=65536,
        channel_timeout=60,
    )


if __name__ == "__main__":
    main()
