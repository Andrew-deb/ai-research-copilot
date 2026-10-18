"""
shared_resource/config.py — Shared database and embedding-contract configuration.

Reads each value from the environment, falling back to a Databricks secret
scope when the environment is silent — that order matters, and `_get_secret`
explains why. Provider-specific settings live in research/config.py; server
startup owns transport settings.
"""

import base64
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# A bounded wait for one secret lookup. This runs at import, before anything can
# serve a request, so it must never be the reason a process is slow to start.
_SECRET_TIMEOUT_SECONDS = 5


def _get_secret(scope: str, key: str, env_fallback: str) -> str | None:
    """
    The environment first, a Databricks secret scope only if it is silent.

    The same order, and for the same reason, as dashboard/config.py - which
    explains it at length and is worth reading before changing this. In short:
    asking Databricks first costs one authenticated round trip per value at
    import, measured at 5.7 to 8.4 seconds each. On the dashboard that was about
    48 seconds per process start, it pushed a cold start past the 30-second
    budget of the schedule that keeps the service awake, and the database was
    eventually suspended for want of a successful ping.

    This module is imported by the dashboard as well as by the MCP server, so it
    inherits the same exposure.
    """
    value = os.getenv(env_fallback)
    if value:
        return value

    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config as _DatabricksConfig

        client = WorkspaceClient(
            config=_DatabricksConfig(http_timeout_seconds=_SECRET_TIMEOUT_SECONDS))
        raw = client.secrets.get_secret(scope=scope, key=key)
        return base64.b64decode(raw.value).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - configuration must not fail to import
        # Named rather than swallowed: `except Exception: pass` is why eight
        # failing lookups in the dashboard went unnoticed for weeks.
        logger.info("No secret %s/%s and no %s in the environment (%s)",
                    scope, key, env_fallback, type(exc).__name__)
        return None


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
