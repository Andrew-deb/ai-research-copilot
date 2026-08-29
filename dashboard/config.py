"""
dashboard/config.py — Single source of truth for dashboard configuration.

Same secret-scope-then-env-var pattern as mcp_server/config.py.
Each Databricks App is its own process with its own config module.
"""

import base64
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_secret(scope: str, key: str, env_fallback: str) -> str | None:
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
EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION: int = 384

# --- OpenRouter ---
OPENROUTER_API_KEY: str | None = _get_secret("openrouter", "api-key", "OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")

# --- Flask ---
SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")
DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# --- Auth ---
# When True, a request without the Databricks-injected X-Forwarded-Email header
# is rejected with 401. Leave False for local dev (falls back to the demo user);
# set REQUIRE_FORWARDED_AUTH=true in the Databricks App environment.
REQUIRE_FORWARDED_AUTH: bool = os.getenv("REQUIRE_FORWARDED_AUTH", "false").lower() == "true"
DEMO_USER_EMAIL: str = os.getenv("DEMO_USER_EMAIL", "demo@research-copilot.dev")
DEMO_USER_NAME: str = os.getenv("DEMO_USER_NAME", "Demo Researcher")

if not DATABASE_URL:
    logger.warning("DATABASE_URL not set — database operations will fail.")
if not OPENROUTER_API_KEY:
    logger.warning("OPENROUTER_API_KEY not set — RAG summaries will be unavailable.")
