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

# --- Support / donations ---
# Where "Support the project" points: GitHub Sponsors, Ko-fi, Buy Me a Coffee.
# Left blank the donate card is hidden entirely, because a button that leads
# nowhere is worse than no button.
DONATE_URL: str | None = os.getenv("DONATE_URL") or None
DONATE_LABEL: str = os.getenv("DONATE_LABEL", "Support the project")

# --- OpenRouter ---
OPENROUTER_API_KEY: str | None = _get_secret("openrouter", "api-key", "OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
# openai/gpt-oss-120b:free was retired by OpenRouter and now 404s, which broke
# every RAG answer. This default is verified for plain synthesis AND tool
# calling; the paid slug openai/gpt-oss-120b is the fallback if free-tier
# rate limits become a problem, and it is an env change, not a code change.
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "inclusionai/ling-3.0-flash-vl:free")

# =============================================================================
# Databricks service auth + MCP (Phase 3.4)
# =============================================================================
# The research agent runs server-side and calls the MCP server as a service
# principal. The browser never holds a Databricks token, never calls the MCP
# server and never learns its URL — the trust boundary is:
#
#   browser --session cookie--> Render Flask --OAuth M2M--> MCP on Databricks
#           (application user)               (service principal)
#
# Every value is optional. Unset, the agent reports itself unavailable and the
# rest of the product — search, RAG, collections, notes — is untouched. An
# absent integration must never stop the app booting.

DATABRICKS_HOST: str | None = (os.getenv("DATABRICKS_HOST") or "").rstrip("/") or None
DATABRICKS_CLIENT_ID: str | None = _get_secret("databricks", "client-id", "DATABRICKS_CLIENT_ID")
DATABRICKS_CLIENT_SECRET: str | None = _get_secret(
    "databricks", "client-secret", "DATABRICKS_CLIENT_SECRET")

# Must end in /mcp — the app root redirects to a login page and only /mcp speaks
# the protocol.
MCP_SERVER_URL: str | None = os.getenv("MCP_SERVER_URL") or None

# Measured against the deployed app: OAuth token 5.7s cold, initialize 3.3s,
# tools/list 1.5s. The token and the tool schemas are cached and the session is
# reused for a whole turn, which is what keeps that off the per-question path.
MCP_TIMEOUT_SECONDS: int = int(os.getenv("MCP_TIMEOUT_SECONDS", "30"))

# Two limits, because they fail differently. The call ceiling stops a model
# looping on itself; the deadline stops a slow provider from running past
# gunicorn's own timeout and returning nothing at all. Whichever trips first
# ends the turn, and telemetry records which one did.
AGENT_MAX_TOOL_CALLS: int = int(os.getenv("AGENT_MAX_TOOL_CALLS", "6"))
AGENT_DEADLINE_SECONDS: int = int(os.getenv("AGENT_DEADLINE_SECONDS", "75"))

# Raised from the 1024 default after a live answer was cut off mid-sentence:
# "...it is difficult to systematically characterize *when* and *why* agents
# fail" and then nothing. A truncated research answer is worse than a short one,
# because the reader cannot tell which it is.
AGENT_MAX_TOKENS: int = int(os.getenv("AGENT_MAX_TOKENS", "2400"))


def mcp_is_configured() -> bool:
    """True when every value the agent needs to reach the MCP server is present."""
    return bool(DATABRICKS_HOST and DATABRICKS_CLIENT_ID
                and DATABRICKS_CLIENT_SECRET and MCP_SERVER_URL)


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
# On from 3.2, now that the capability layer refuses anonymous writes and the
# quota layer meters anonymous AI use.
ALLOW_ANONYMOUS_DEMO: bool = os.getenv("ALLOW_ANONYMOUS_DEMO", "true").lower() == "true"

# =============================================================================
# Quotas (Phase 3.2)
# =============================================================================
# EVERY NUMBER BELOW IS A PLACEHOLDER, not a production value.
#
# Production values come from the calibration step (plan 3.6), which measures
# what an operation actually consumes and works backwards from the providers'
# free allowances. Choosing them now would be guessing, so they are deliberately
# stingy: an un-calibrated deploy should be restrictive rather than expensive.
#
# Authentication raises the allowance. It never removes it — an authenticated
# user with a runaway script costs exactly as much as an anonymous one.

ANON_SEARCH_PER_DAY: int = int(os.getenv("ANON_SEARCH_PER_DAY", "20"))
ANON_RAG_PER_DAY: int = int(os.getenv("ANON_RAG_PER_DAY", "3"))
ANON_AGENT_PER_DAY: int = int(os.getenv("ANON_AGENT_PER_DAY", "1"))

USER_SEARCH_PER_DAY: int = int(os.getenv("USER_SEARCH_PER_DAY", "200"))
USER_RAG_PER_DAY: int = int(os.getenv("USER_RAG_PER_DAY", "30"))
USER_AGENT_PER_DAY: int = int(os.getenv("USER_AGENT_PER_DAY", "10"))

# The ceilings that protect the budget. On a free tier they protect availability:
# exhausting a provider's daily allowance means the feature is dead until it
# resets, for everyone, including whoever is demonstrating it.
GLOBAL_RAG_PER_DAY: int = int(os.getenv("GLOBAL_RAG_PER_DAY", "200"))
GLOBAL_AGENT_PER_DAY: int = int(os.getenv("GLOBAL_AGENT_PER_DAY", "50"))

# Off switch for local work, where metering only gets in the way.
QUOTAS_ENABLED: bool = os.getenv("QUOTAS_ENABLED", "true").lower() == "true"

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
