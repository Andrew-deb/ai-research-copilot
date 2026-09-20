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
# The model is fixed by the data: the Spark pipeline writes 768-dim unit-normalised
# vectors with modernbert-embed-base, so query vectors must come from the same model
# or cosine distance in pgvector stops meaning anything.
EMBEDDING_MODEL: str = "nomic-ai/modernbert-embed-base"
EMBEDDING_DIMENSION: int = 768

# ModernBERT-embed is asymmetric: a query must be marked as a query. The pipeline
# applies "search_document: " to the other side. Omitting either does not error -
# it silently degrades ranking, so both live in config, never inline.
EMBEDDING_QUERY_PREFIX: str = "search_query: "

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

# =============================================================================
# Identity (Phase 3.1)
# =============================================================================
# The dashboard is deployed on Render, not as a Databricks App, so there is no
# OAuth proxy injecting X-Forwarded-Email. The app authenticates users itself via
# Google, and authenticates *itself* to Databricks separately and server-side.
# Those two boundaries never meet; see
# context/decisions/authentication_and_demo_design.md.

APP_ENV: str = os.getenv("APP_ENV", "local").strip().lower()
IS_PRODUCTION: bool = APP_ENV == "production"

# --- Google OAuth ---
GOOGLE_CLIENT_ID: str | None = _get_secret("google", "client-id", "GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET: str | None = _get_secret("google", "client-secret", "GOOGLE_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL: str = "https://accounts.google.com/.well-known/openid-configuration"

# --- Development shortcut ---
# Skips the Google round trip and resolves every request to a fixed local
# identity, so unrelated debugging does not require signing in. NEVER production:
# see the guard at the bottom of this module.
#
# The address MUST stay `demo@research-copilot.dev`. It is not arbitrary - three
# things key on it and they have to agree:
#
#     setup_db.py                              seeds this row
#     mcp_server/middleware/request_context.py the agent writes as this user
#     this bypass                              the dashboard reads as this user
#
# Renaming it once already split the identity in two: the agent wrote to one
# library while the dashboard displayed another, and every collection, note and
# reading-progress row created before the rename appeared to have vanished. The
# data was never lost, just attached to a user the dashboard had stopped being.
#
# The *config name* is DEV_USER_EMAIL because this is the development identity,
# distinct from the public anonymous demo tier. The *value* is a shared contract.
ALLOW_DEV_USER_BYPASS: bool = os.getenv("ALLOW_DEV_USER_BYPASS", "true").lower() == "true"
DEV_USER_EMAIL: str = os.getenv("DEV_USER_EMAIL", "demo@research-copilot.dev")
DEV_USER_NAME: str = os.getenv("DEV_USER_NAME", "Demo Researcher")

# --- Public anonymous demo ---
# Defaults OFF in 3.1 and is switched on in 3.2, when the capability layer that
# stops an anonymous visitor mutating anything actually exists. Shipping it on
# before those guards land would give every visitor write access.
ALLOW_ANONYMOUS_DEMO: bool = os.getenv("ALLOW_ANONYMOUS_DEMO", "false").lower() == "true"

# --- Session cookie ---
# Lax rather than Strict on purpose: the Google callback is a top-level GET
# navigation from accounts.google.com, and Strict would withhold the cookie on
# arrival - losing the OAuth `state` and breaking sign-in. Lax still withholds
# the cookie on cross-site POST, which is where the CSRF risk lives.
SESSION_COOKIE_SECURE: bool = IS_PRODUCTION
SESSION_COOKIE_HTTPONLY: bool = True
SESSION_COOKIE_SAMESITE: str = "Lax"
SESSION_LIFETIME_DAYS: int = int(os.getenv("SESSION_LIFETIME_DAYS", "14"))

# =============================================================================
# Boot-time safety checks
# =============================================================================
# These raise rather than warn. A misconfigured production deploy should fail its
# health check and never serve traffic, because every alternative here is worse
# than being down: the first would make every visitor the dev user, and the
# second would sign session cookies with a value published in this repository.

if IS_PRODUCTION and ALLOW_DEV_USER_BYPASS:
    raise RuntimeError(
        "ALLOW_DEV_USER_BYPASS cannot be enabled when APP_ENV=production. "
        "It resolves every request to the dev user without authentication. "
        "Unset it in the Render environment."
    )

if IS_PRODUCTION and SECRET_KEY == "dev-secret-change-in-production":
    raise RuntimeError(
        "FLASK_SECRET_KEY is still the development default while APP_ENV=production. "
        "It signs session cookies, so anyone could forge a session. "
        "Render generates one automatically - check the environment."
    )

if IS_PRODUCTION and not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
    raise RuntimeError(
        "APP_ENV=production requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. "
        "Without them nobody can sign in, and with ALLOW_DEV_USER_BYPASS refused "
        "the application has no way to identify anyone."
    )

if not DATABASE_URL:
    logger.warning("DATABASE_URL not set — database operations will fail.")
if not OPENROUTER_API_KEY:
    logger.warning("OPENROUTER_API_KEY not set — RAG summaries will be unavailable.")
if not IS_PRODUCTION and ALLOW_DEV_USER_BYPASS:
    logger.warning(
        "ALLOW_DEV_USER_BYPASS is on — every request resolves to %s without "
        "signing in. Set ALLOW_DEV_USER_BYPASS=false to exercise the real Google flow.",
        DEV_USER_EMAIL,
    )
