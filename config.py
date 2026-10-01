"""
mcp_server/config.py — Single source of truth for all configuration.

Loads secrets from Databricks secret scope (production) with fallback to
.env variables (local development). No other module calls os.getenv() directly.
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

# --- OpenAlex ---
OPENALEX_BASE_URL: str = "https://api.openalex.org"
OPENALEX_EMAIL: str = os.getenv("OPENALEX_EMAIL", "user@research-copilot.dev")
OPENALEX_RATE_LIMIT_DELAY: float = float(os.getenv("OPENALEX_RATE_LIMIT_DELAY", "0.12"))

# --- Semantic Scholar ---
S2_BASE_URL: str = "https://api.semanticscholar.org"
S2_API_KEY: str | None = _get_secret("semantic-scholar", "api-key", "SEMANTIC_SCHOLAR_API_KEY")
# 1.1s = safe delay for authenticated tier (1 req/sec). Use 3.1s without a key.
S2_RATE_LIMIT_DELAY: float = float(os.getenv("S2_RATE_LIMIT_DELAY", "1.1"))

# --- Wikipedia ---
WIKIPEDIA_BASE_URL: str = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_RATE_LIMIT_DELAY: float = float(os.getenv("WIKIPEDIA_RATE_LIMIT_DELAY", "0.1"))

# --- OpenRouter ---
OPENROUTER_API_KEY: str | None = _get_secret("openrouter", "api-key", "OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")

# --- MCP Server ---
MCP_SERVER_NAME: str = "ai-research-copilot"
MCP_SERVER_VERSION: str = "1.0.0"

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
if not S2_API_KEY:
    logger.warning("S2_API_KEY not set — using unauthenticated tier. Set S2_RATE_LIMIT_DELAY=3.1.")
