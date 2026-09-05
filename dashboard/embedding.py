"""
dashboard/embedding.py — Query embedding client for semantic search.

SRP: turns free-text into a 768-dim query vector. Nothing else — no SQL, no Flask.

Why this module exists at all
-----------------------------
The Spark pipeline (notebooks/ingest_papers_embeddings.py) writes every stored
vector with `nomic-ai/modernbert-embed-base`, the `search_document: ` prefix and
`normalize_embeddings=True`. A query vector must come from the *same model*, with
the matching `search_query: ` prefix and the *same normalisation*, or cosine
distance in pgvector silently stops meaning anything — results still come back,
just badly ranked. Everything here exists to
keep that guarantee while letting the web tier run somewhere small.

Two backends, one guarantee
---------------------------
  local   Runs sentence-transformers in-process. Exact, offline, but drags in
          torch: ~2 GB image and ~500 MB RAM per worker. Fine on a laptop or a
          Databricks App; will not fit a 512 MB container.

  hf_api  Calls the Hugging Face Inference API for the *same model*. No torch,
          ~100 MB RAM, so it fits a free Render/Fly instance. We L2-normalise
          the response ourselves rather than trusting the endpoint to do it,
          which is what keeps the two backends interchangeable.

Selected by `EMBEDDING_BACKEND` (local | hf_api | auto). "auto" picks hf_api
when an HF token is configured, else local.
"""

import logging
import math
import threading

import requests

from config import (
    EMBEDDING_BACKEND,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDING_QUERY_PREFIX,
    HF_API_TOKEN,
    HF_EMBEDDING_URL,
    HF_TIMEOUT_SECONDS,
)
from exceptions import EmbeddingError

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()
_api_ready = False


# =============================================================================
# Backend selection
# =============================================================================

def active_backend() -> str:
    """Resolve "auto" to a concrete backend name."""
    if EMBEDDING_BACKEND in ("local", "hf_api"):
        return EMBEDDING_BACKEND
    return "hf_api" if HF_API_TOKEN else "local"


# =============================================================================
# Shared helpers
# =============================================================================

def _l2_normalize(vector: list[float]) -> list[float]:
    """
    Scale to unit length, matching the pipeline's `normalize_embeddings=True`.

    Applied to both backends on purpose: sentence-transformers normalises for us,
    but the Inference API's pooling behaviour is not contractually guaranteed, and
    a query vector of the wrong magnitude would skew every cosine distance. Doing
    it here costs microseconds and removes the doubt.
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        raise EmbeddingError("Embedding backend returned a zero vector.")
    return [component / norm for component in vector]


def _validate(vector: list[float]) -> list[float]:
    if len(vector) != EMBEDDING_DIMENSION:
        raise EmbeddingError(
            f"Embedding backend returned {len(vector)} dimensions, expected "
            f"{EMBEDDING_DIMENSION}. The stored vectors were built with "
            f"{EMBEDDING_MODEL}; the query model must match."
        )
    return _l2_normalize(vector)


# =============================================================================
# Backend: local sentence-transformers
# =============================================================================

def _get_model():
    """Lazily construct and cache the SentenceTransformer singleton."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                import time

                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbeddingError(
                        "EMBEDDING_BACKEND=local needs sentence-transformers, which is not "
                        "installed. Either `pip install sentence-transformers` or set "
                        "EMBEDDING_BACKEND=hf_api with an HF_API_TOKEN."
                    ) from exc

                logger.info("Loading embedding model %r in-process...", EMBEDDING_MODEL)
                started = time.perf_counter()
                _model = SentenceTransformer(EMBEDDING_MODEL, cache_folder="/tmp/.cache/huggingface")
                logger.info("Embedding model ready in %.1fs", time.perf_counter() - started)
    return _model


def _encode_local(text: str) -> list[float]:
    return _get_model().encode(text, normalize_embeddings=True).tolist()


# =============================================================================
# Backend: Hugging Face Inference API
# =============================================================================

def _flatten_token_embeddings(payload) -> list[float]:
    """
    Reduce whatever shape the endpoint returned to a single sentence vector.

    feature-extraction returns a flat [768] for sentence-transformers models, but
    can return per-token embeddings ([tokens][768], or [1][tokens][768]) depending
    on the model revision and endpoint. Mean-pool those, which is what
    modernbert-embed-base's own pooling layer does.
    """
    if not isinstance(payload, list) or not payload:
        raise EmbeddingError(f"Unexpected embedding response shape: {type(payload).__name__}")

    if isinstance(payload[0], (int, float)):          # [768]
        return [float(x) for x in payload]

    if isinstance(payload[0], list) and payload[0] and isinstance(payload[0][0], list):
        payload = payload[0]                           # [1][tokens][768] -> [tokens][768]

    if not (isinstance(payload[0], list) and payload[0] and isinstance(payload[0][0], (int, float))):
        raise EmbeddingError("Unexpected embedding response nesting.")

    width = len(payload[0])
    return [sum(row[i] for row in payload) / len(payload) for i in range(width)]


def _encode_hf_api(text: str) -> list[float]:
    global _api_ready
    if not HF_API_TOKEN:
        raise EmbeddingError(
            "EMBEDDING_BACKEND=hf_api but HF_API_TOKEN is not set. Create a read token at "
            "https://huggingface.co/settings/tokens and set HF_API_TOKEN."
        )

    try:
        response = requests.post(
            HF_EMBEDDING_URL,
            headers={
                "Authorization": f"Bearer {HF_API_TOKEN}",
                # Block until the model is warm instead of returning 503 on a cold start.
                "x-wait-for-model": "true",
            },
            json={"inputs": text},
            timeout=HF_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise EmbeddingError(f"Embedding request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise EmbeddingError("Embedding endpoint returned a non-JSON response.") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise EmbeddingError(f"Embedding endpoint error: {payload['error']}")

    vector = _flatten_token_embeddings(payload)
    _api_ready = True
    return vector


# =============================================================================
# Public API
# =============================================================================

def is_loaded() -> bool:
    """
    True when a query can be embedded without paying a cold-start cost.
    Reported by /healthz.
    """
    return _api_ready if active_backend() == "hf_api" else _model is not None


def warmup() -> None:
    """
    Pay the cold start now — from a background thread at startup — instead of
    making the first user search wait. Local: loads the model (~5-20s).
    hf_api: one tiny request that wakes the hosted model.

    Never raises: a failed warmup must not stop the app from serving.
    """
    backend = active_backend()
    try:
        if backend == "hf_api":
            _encode_hf_api(EMBEDDING_QUERY_PREFIX + "warmup")
            logger.info("Embedding backend ready (hf_api, %s)", EMBEDDING_MODEL)
        else:
            _get_model()
    except Exception as exc:  # noqa: BLE001 - startup must survive this
        logger.warning("Embedding warmup failed (%s): %s", backend, exc)


def encode_query(text: str) -> list[float]:
    """
    Embed one query string into a unit-normalised 768-dim vector, in the same
    space as the vectors the pipeline stored.

    The model is asymmetric: it expects a query to be marked with
    `search_query: ` and a document with `search_document: `. The prefix is added
    here, for both backends, and is never stored - `paper_embeddings.chunk_text`
    holds clean text because it is rendered directly as the UI result snippet.
    """
    if not text or not text.strip():
        raise EmbeddingError("Cannot embed an empty query.")

    prefixed = EMBEDDING_QUERY_PREFIX + text.strip()
    backend = active_backend()
    raw = _encode_hf_api(prefixed) if backend == "hf_api" else _encode_local(prefixed)
    return _validate(raw)
