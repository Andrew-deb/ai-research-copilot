"""Authenticated, bounded transport to the dedicated embedding App."""

import json
import logging
import math
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests
import config
from exceptions import EmbeddingServiceError

logger = logging.getLogger(__name__)
_auth = None
_auth_lock = threading.Lock()
_ready = False
_UNAVAILABLE = "Semantic search is temporarily unavailable. Please try again shortly."


def _after_fork():
    global _auth, _auth_lock, _ready
    _auth = None
    _auth_lock = threading.Lock()
    _ready = False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _fail(reason, message=_UNAVAILABLE):
    global _ready
    _ready = False
    logger.warning("Databricks embedding unavailable: %s", reason)
    raise EmbeddingServiceError(message) from None


def _origin():
    try:
        parsed = urlparse(config.DATABRICKS_EMBEDDING_URL)
        port = parsed.port
    except ValueError:
        _fail("invalid App origin")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".databricksapps.com")
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        _fail("invalid App origin")
    if not re.fullmatch(r"[0-9a-f]{64}", config.DATABRICKS_EMBEDDING_VERSION):
        _fail("missing or invalid snapshot fingerprint")
    return config.DATABRICKS_EMBEDDING_URL.rstrip("/")


def _headers():
    global _auth
    if not all(
        (
            config.DATABRICKS_HOST,
            config.DATABRICKS_CLIENT_ID,
            config.DATABRICKS_CLIENT_SECRET,
        )
    ):
        _fail("missing service credentials")
    try:
        if _auth is None:
            with _auth_lock:
                if _auth is None:
                    from databricks.sdk.core import Config

                    _auth = Config(
                        host=config.DATABRICKS_HOST,
                        client_id=config.DATABRICKS_CLIENT_ID,
                        client_secret=config.DATABRICKS_CLIENT_SECRET,
                        auth_type="oauth-m2m",
                        http_timeout_seconds=5,
                        retry_timeout_seconds=5,
                    )
        return dict(_auth.authenticate())
    except Exception:
        _fail("OAuth authentication failed")


def _request(method, path, payload=None):
    origin = _origin()
    try:
        timeout = float(config.DATABRICKS_EMBEDDING_TIMEOUT_SECONDS)
        if not 0 < timeout <= 60:
            _fail("invalid request timeout")
        # One retry for brief contention only. Long Retry-After values fail fast.
        for attempt in range(2):
            response = requests.request(
                method,
                origin + path,
                headers=_headers(),
                json=payload,
                timeout=(5, timeout),
                allow_redirects=False,
            )
            if response.status_code == 429 and attempt == 0:
                try:
                    wait = float(response.headers.get("Retry-After", "1"))
                except (ValueError, TypeError):
                    wait = 1
                if 0 <= wait <= 1:
                    time.sleep(wait)
                    continue
            if response.status_code == 429:
                _fail("busy", "Semantic search is busy. Please try again in a moment.")
            if response.status_code in (401, 403):
                _fail("App refused service authentication or permissions")
            if response.status_code != 200:
                _fail(f"App returned HTTP {response.status_code}")
            return response.json()
    except EmbeddingServiceError:
        raise
    except (requests.RequestException, ValueError, TypeError):
        _fail("request failed or response was not JSON")


def warmup():
    """Readiness only: no dummy inference and no automatic App restart."""
    global _ready
    payload = _request("GET", "/api/ready")
    if (
        not isinstance(payload, dict)
        or payload.get("state") != "ready"
        or payload.get("model") != config.EMBEDDING_MODEL
        or payload.get("dimension") != config.EMBEDDING_DIMENSION
        or payload.get("version") != config.DATABRICKS_EMBEDDING_VERSION
    ):
        _fail("readiness model contract mismatch")
    _ready = True


def model_version():
    return config.DATABRICKS_EMBEDDING_VERSION


def is_loaded():
    """Last observed readiness; a subsequent request failure clears it."""
    return _ready


def encode(text, kind):
    global _ready
    body = {"text": text, "kind": kind}
    if (
        kind not in ("query", "document")
        or not isinstance(text, str)
        or not text.strip()
        or len(text) > 16000
        or len(json.dumps(body).encode("utf-8")) > 65536
    ):
        _fail(
            "input exceeds App contract",
            "This text is too long or empty. Please use a shorter passage.",
        )
    if not _ready:
        warmup()
    payload = _request("POST", "/api/embed", body)
    if (
        not isinstance(payload, dict)
        or payload.get("version") != config.DATABRICKS_EMBEDDING_VERSION
        or payload.get("dimension") != config.EMBEDDING_DIMENSION
    ):
        _fail("inference model contract mismatch")
    vector = payload.get("embedding")
    if (
        not isinstance(vector, list)
        or len(vector) != config.EMBEDDING_DIMENSION
        or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
    ):
        _fail("invalid vector components")
    if not math.isclose(sum(x * x for x in vector), 1, abs_tol=1e-4):
        _fail("vector is not normalized")
    _ready = True
    return vector
