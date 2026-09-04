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
# The model is fixed by the data: the Spark pipeline wrote 384-dim unit-normalised
# vectors with all-MiniLM-L6-v2, so query vectors must come from the same model or
# cosine distance in pgvector stops meaning anything.
EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION: int = 384

# How to produce query vectors:
#   "local"  - sentence-transformers in-process (needs torch, ~500 MB RAM)
#   "hf_api" - Hugging Face Inference API (no torch; fits small containers)
#   "auto"   - hf_api when HF_API_TOKEN is set, else local
EMBEDDING_BACKEND: str = os.getenv("EMBEDDING_BACKEND", "auto").strip().lower()
HF_API_TOKEN: str | None = _get_secret("huggingface", "api-token", "HF_API_TOKEN")
HF_EMBEDDING_URL: str = os.getenv(
    "HF_EMBEDDING_URL",
    f"https://router.huggingface.co/hf-inference/models/{EMBEDDING_MODEL}/pipeline/feature-extraction",
)
HF_TIMEOUT_SECONDS: int = int(os.getenv("HF_TIMEOUT_SECONDS", "30"))
# Load the model in a background thread at startup instead of on the first
# request that needs it. Default on when not in debug.
EMBEDDING_PRELOAD: bool = os.getenv(
    "EMBEDDING_PRELOAD", "false" if os.getenv("FLASK_DEBUG", "false").lower() == "true" else "true"
).lower() == "true"

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
