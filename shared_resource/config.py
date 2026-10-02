"""
shared_resource/config.py — Shared database and embedding-contract configuration.

Loads secrets from Databricks secret scope (production) with fallback to
.env variables (local development). Provider-specific settings live in research/config.py; server startup owns transport settings.
"""

import base64
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_secret(scope: str, key: str, env_fallback: str) -> str | None:
    """Try Databricks secret scope first, fall back to environment variable."""
    try:
        from databricks.sdk import WorkspaceClient
        raw = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
        return base64.b64decode(raw.value).decode("utf-8")
    except Exception:
        pass
    return os.getenv(env_fallback)


# --- Lakebase ---
DATABASE_URL: str | None = _get_secret("database", "lakebase-url", "DATABASE_URL")

# --- Embedding ---
# Fixed by the data: the pipeline writes 768-dim unit-normalised vectors with this
# model, so any query vector must come from the same model or cosine distance in
# pgvector stops meaning anything.
EMBEDDING_MODEL: str = "nomic-ai/modernbert-embed-base"
EMBEDDING_DIMENSION: int = 768

# ModernBERT-embed is asymmetric: queries and documents carry different task
# prefixes. Omitting them does not error - it silently degrades relevance.
EMBEDDING_QUERY_PREFIX: str = "search_query: "
EMBEDDING_DOCUMENT_PREFIX: str = "search_document: "

# The model reads 8192 tokens (~32k chars), so an entire abstract fits in one chunk.
CHUNK_SIZE: int = 4000
CHUNK_OVERLAP: int = 400

# --- Startup warnings ---
if not DATABASE_URL:
    logger.warning("DATABASE_URL not set — database operations will fail.")
