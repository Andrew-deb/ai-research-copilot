"""
dashboard/embedding.py — Query embedding client for semantic search.

SRP: Turns free-text into a 384-dim vector. Nothing else.
     No SQL, no HTTP, no Flask.

The Spark pipeline (notebooks/ingest_papers_embeddings.py) embeds paper
abstracts and notes offline with `sentence-transformers/all-MiniLM-L6-v2`
and unit-normalisation. The dashboard must embed the *query* with the exact
same model and normalisation so cosine distance in pgvector is meaningful.

The model (~90 MB) is loaded lazily on first call and cached for the life of
the process, so importing this module stays cheap and unit tests that never
call `encode_query()` never pull the model.
"""

import logging
import threading

from dashboard.config import EMBEDDING_DIMENSION, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Lazily construct and cache the SentenceTransformer singleton."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model '%s'...", EMBEDDING_MODEL)
                _model = SentenceTransformer(EMBEDDING_MODEL, cache_folder="/tmp/.cache/huggingface")
    return _model


def encode_query(text: str) -> list[float]:
    """
    Embed a single query string into a unit-normalised 384-dim vector.

    Mirrors the pipeline's `encode(..., normalize_embeddings=True)` call so the
    query vector lives in the same space as the stored chunk vectors.
    """
    if not text or not text.strip():
        raise ValueError("Cannot embed an empty query.")

    vector = _get_model().encode(text.strip(), normalize_embeddings=True).tolist()

    if len(vector) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Embedding model returned {len(vector)} dims, expected {EMBEDDING_DIMENSION}."
        )
    return vector
