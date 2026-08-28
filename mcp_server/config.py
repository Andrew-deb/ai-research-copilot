"""
mcp_server/config.py — Centralized Configuration for the MCP Server

PURPOSE:
    Single source of truth for all configuration values — environment
    variables, secret scope names, API settings, and constants.

    NO module in this project reads os.getenv() or secrets directly.
    Everything goes through this module. This means:
      - To change where a value comes from, you change ONE file
      - All config is visible in one place for review
      - Testing is easier: mock this module, not scattered os.getenv() calls

LOADING STRATEGY:
    1. Try Databricks secret scope (production — when running as a
       Databricks App, WorkspaceClient is available)
    2. Fall back to os.getenv() (local development with .env file)

    This means the same codebase works both locally AND on Databricks
    without any code changes — just different credentials sources.
"""

import base64
import logging
import os

from dotenv import load_dotenv

# Load .env for local development (no-op if file doesn't exist, which is
# fine when running as a Databricks App where secrets come from scopes)
load_dotenv()

logger = logging.getLogger(__name__)


def _get_secret(scope: str, key: str, env_fallback: str) -> str | None:
    """
    Retrieve a secret from Databricks secret scope, falling back to env var.

    Databricks secrets are base64-encoded strings. We decode them here
    (same pattern used in Day 2 and Day 3 labs).

    Args:
        scope:        Databricks secret scope name
        key:          Secret key within the scope
        env_fallback: Environment variable name to use if scope is unavailable

    Returns:
        The secret value as a plain string, or None if not found anywhere.
    """
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        raw = w.secrets.get_secret(scope=scope, key=key)
        # Databricks SDK returns base64-encoded value
        return base64.b64decode(raw.value).decode("utf-8")
    except Exception:
        # Not running on Databricks, or secret scope doesn't exist yet
        # Fall back to local .env / environment variable
        pass

    return os.getenv(env_fallback)


# =============================================================================
# DATABASE (Lakebase)
# =============================================================================

DATABASE_URL: str | None = _get_secret(
    scope="database",
    key="lakebase-url",
    env_fallback="DATABASE_URL",
)

# =============================================================================
# OPENALEX
# =============================================================================

OPENALEX_BASE_URL: str = "https://api.openalex.org"

OPENALEX_EMAIL: str = os.getenv("OPENALEX_EMAIL", "user@research-copilot.dev")

# Polite delay between OpenAlex requests (seconds).
# OpenAlex's polite pool allows 10 req/sec; we use 0.12s to stay safe.
OPENALEX_RATE_LIMIT_DELAY: float = float(os.getenv("OPENALEX_RATE_LIMIT_DELAY", "0.12"))

# =============================================================================
# SEMANTIC SCHOLAR
# =============================================================================

S2_BASE_URL: str = "https://api.semanticscholar.org"

S2_API_KEY: str | None = _get_secret(
    scope="semantic-scholar",
    key="api-key",
    env_fallback="SEMANTIC_SCHOLAR_API_KEY",
)

# Delay between S2 requests. Authenticated tier: 1 req/sec → 1.1s to be safe.
# Unauthenticated tier: 100 req/5min → ~3.1s. We use 1.1s (assumes API key).
S2_RATE_LIMIT_DELAY: float = float(os.getenv("S2_RATE_LIMIT_DELAY", "1.1"))

# =============================================================================
# WIKIPEDIA
# =============================================================================

WIKIPEDIA_BASE_URL: str = "https://en.wikipedia.org/api/rest_v1"

# Small courtesy delay between Wikipedia calls (no formal rate limit for us)
WIKIPEDIA_RATE_LIMIT_DELAY: float = float(os.getenv("WIKIPEDIA_RATE_LIMIT_DELAY", "0.1"))

# =============================================================================
# OPENROUTER (LLM for RAG — used by dashboard, not MCP server directly)
# =============================================================================

OPENROUTER_API_KEY: str | None = _get_secret(
    scope="openrouter",
    key="api-key",
    env_fallback="OPENROUTER_API_KEY",
)

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")

# =============================================================================
# MCP SERVER
# =============================================================================

MCP_SERVER_NAME: str = "ai-research-copilot"
MCP_SERVER_VERSION: str = "1.0.0"

# =============================================================================
# EMBEDDING MODEL
# =============================================================================

# Must match the model used when the HNSW index was built.
# Changing this requires rebuilding all embeddings and the index.
EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION: int = 384
CHUNK_SIZE: int = 800
CHUNK_OVERLAP: int = 100

# =============================================================================
# VALIDATION — warn on startup if critical config is missing
# =============================================================================

if not DATABASE_URL:
    logger.warning(
        "DATABASE_URL is not set. Database operations will fail. "
        "Set it in your .env file or Databricks secret scope 'database/lakebase-url'."
    )

if not S2_API_KEY:
    logger.warning(
        "SEMANTIC_SCHOLAR_API_KEY is not set. S2 calls will use "
        "the unauthenticated tier (100 req/5min). "
        "Set S2_RATE_LIMIT_DELAY=3.1 to avoid rate limit errors."
    )
