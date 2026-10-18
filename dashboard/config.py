"""
dashboard/config.py — Single source of truth for dashboard configuration.

Same env-var-then-secret-scope pattern as mcp_server/shared_resource/config.py.
Each Databricks App is its own process with its own config module.

The order was secret-scope-first until October 2026, and reversing it is the
single largest change this module has had: it took process start from about 55
seconds to 7. `_get_secret` records what the old order cost, because the bill
arrived somewhere nobody was looking - a database branch, not a slow boot.
"""

import base64
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# How long one Databricks secret lookup may take. Short on purpose: this runs at
# import, before anything can serve a request, and the whole point of the
# ordering below is that nothing here delays a cold start.
_SECRET_TIMEOUT_SECONDS = 5


def _get_secret(scope: str, key: str, env_fallback: str) -> str | None:
    """
    One configured value: the environment first, a Databricks secret scope only
    if the environment did not supply it.

    THE ORDER IS THE POINT, and it used to be the other way round.

    Asking Databricks first cost one authenticated round trip per value. This
    module reads eight, so every process start paid eight of them - measured at
    5.7 to 8.4 seconds each, about 48 seconds in total - and then used the
    environment variable anyway, because on this deployment the lookups do not
    merely miss, they fail:

        database  /lakebase-url     PermissionDenied
        openrouter/api-key          PermissionDenied
        google    /client-id        ResourceDoesNotExist

    Forty-eight seconds for a path that has never returned a value here.

    What it cost, concretely. Render's free tier stops an idle service, and the
    next request waits for it to start: measured at 34 to 53 seconds, nearly all
    of it this function. cron-job.org gives a request 30 seconds, so the ping
    meant to keep the service awake timed out before the service could finish
    waking - every time, for weeks. Nothing touched Lakebase, its compute was
    suspended and then disabled, and the branch had to be recreated. A visitor
    opening a cold link paid the same wait.

    Databricks is still consulted when the environment is silent, which is how a
    Databricks App is meant to supply these. It is just no longer asked about
    values we already have.
    """
    value = os.getenv(env_fallback)
    if value:
        return value

    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config as _DatabricksConfig

        # A bounded wait, so a hang cannot reproduce the outage above. Without
        # it the only limit is the SDK's own retry budget, which is minutes.
        client = WorkspaceClient(
            config=_DatabricksConfig(http_timeout_seconds=_SECRET_TIMEOUT_SECONDS))
        raw = client.secrets.get_secret(scope=scope, key=key)
        return base64.b64decode(raw.value).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - configuration must not fail to import
        # Named, not silent. This was `except Exception: pass`, so eight failing
        # lookups produced no evidence at all and went unnoticed until they had
        # cost a database branch. The scope and key are safe to log; the value
        # is never logged, found or not.
        logger.info("No secret %s/%s and no %s in the environment (%s)",
                    scope, key, env_fallback, type(exc).__name__)
        return None


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


def _credentials() -> list[str]:
    """
    Every configured OpenRouter credential, in the order they will be tried.

    PROVIDER FAILOVER. A single credential is a single point of failure for the
    only part of this application that cannot degrade gracefully: revoke it,
    let it expire, exhaust whatever the provider meters, or have the provider
    reject it for a reason of its own, and every AI feature stops at once.
    Accepting more than one lets a request that cannot be served by the first
    be served by the next.

    Backwards compatible in both directions. `OPENROUTER_API_KEY` alone behaves
    exactly as it always has — one credential, tried once, no failover, nothing
    to configure and nothing new to understand. That matters: most deployments,
    including every self-hosted one with a paid key, will have exactly one.

    Two spellings, because deployments differ:

        OPENROUTER_API_KEYS   comma-separated, for hosts that allow long values
        OPENROUTER_API_KEY_1  numbered, for hosts where one secret per field is
        OPENROUTER_API_KEY_2  easier to rotate individually
        ...

    Duplicates are collapsed. The same credential twice is not resilience, and
    trying it again after it has just failed wastes a request to learn
    something already known.
    """
    found: list[str] = []

    for raw in (os.getenv("OPENROUTER_API_KEYS") or "").split(","):
        if raw.strip():
            found.append(raw.strip())

    for index in range(1, 11):
        value = os.getenv(f"OPENROUTER_API_KEY_{index}")
        if value and value.strip():
            found.append(value.strip())

    if OPENROUTER_API_KEY:
        found.append(OPENROUTER_API_KEY)

    ordered: list[str] = []
    for credential in found:
        if credential not in ordered:
            ordered.append(credential)
    return ordered


OPENROUTER_API_KEYS: list[str] = _credentials()
# Two requirements, and the second is the one that is easy to miss.
#
# NOT A `:free` SLUG. Twice a pinned `:free` variant has been retired under us
# and started returning 404 "This model is unavailable for free" on every
# single call: first openai/gpt-oss-120b:free, then
# inclusionai/ling-3.0-flash-vl:free. Both failures were silent, in that
# nothing here changed - the provider withdrew the variant - and total, in that
# every RAG answer and every agent turn stopped at once. A paid slug is not
# withdrawn for being popular, and at $0.021/M prompt and $0.063/M completion
# tokens this is roughly 14,000 questions per dollar, so the saving the free
# variant bought never covered the fragility.
#
# MORE THAN ONE PROVIDER. This is why the model is ling-3.0-flash and not
# ling-3.0-flash-vl, which measured better on every other axis. The VL variant
# is served by exactly one provider, and when that provider's shared pool is
# busy OpenRouter has nowhere to route: measured, it returned
# `engine_overloaded` with `limit_source: upstream_provider_shared_pool`
# continuously for over twenty minutes, on all three credentials at once.
#
# Credential failover cannot help with that, which is the point. Three keys
# protect against a key being revoked, expired or throttled; they are three
# ways of reaching the SAME provider. A single-provider model puts the demo at
# the mercy of someone else's capacity, and the whole reserve exists so the
# thing answers when somebody who matters opens the link.
#
# ling-3.0-flash is served by Novita and DeepInfra, so one being busy is a
# routing decision rather than an outage. Its endpoint also serves 262k context
# against the VL variant's actual 131k - the models list advertises 262k for
# both, and the endpoints list disagrees; the endpoints list is the one serving
# the request.
#
# Verified for the part a cheaper model usually fails - tool calling and the
# citation contract, not prose: 20 of 21 measured runs closed with a parseable
# citations block and every citation resolving. The one miss produced no block
# at all, which resolve_citations degrades safely (markers stripped, no
# sources shown) rather than mapping a number to the wrong paper.
#
# render.yaml pins this explicitly rather than relying on the default, so
# production never depends on a value that can rot here unnoticed. Changing it
# is one environment variable and no code.
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "inclusionai/ling-3.0-flash")

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
# Preserve the existing Research setting during cutover. Wick has no fallback.
RESEARCH_MCP_SERVER_URL: str | None = os.getenv("RESEARCH_MCP_SERVER_URL") or MCP_SERVER_URL
WICK_MCP_SERVER_URL: str | None = os.getenv("WICK_MCP_SERVER_URL") or None


def mcp_endpoint(mode: str = "research") -> str | None:
    if mode == "research":
        return RESEARCH_MCP_SERVER_URL
    if mode == "wick":
        return WICK_MCP_SERVER_URL
    raise ValueError("Unknown MCP mode.")

# Measured against the deployed app: OAuth token 5.7s cold, initialize 3.3s,
# tools/list 1.5s. The token and the tool schemas are cached and the session is
# reused for a whole turn, which is what keeps that off the per-question path.
MCP_TIMEOUT_SECONDS: int = int(os.getenv("MCP_TIMEOUT_SECONDS", "30"))

# Two limits, because they fail differently. The call ceiling stops a model
# looping on itself; the deadline stops a slow provider from running past
# gunicorn's own timeout and returning nothing at all. Whichever trips first
# ends the turn, and telemetry records which one did.
#
# The default is 4 because of THIS deployment's provider allowance, not because
# four is the right number for the product. Calibration measured 2.46 provider
# requests per agent question with a ceiling of 7, against an OpenRouter free
# tier of 50 requests a day: the tail is what exhausts the allowance, and
# capping the tail is what buys back questions for everyone else.
#
# It stays an environment variable for exactly that reason. A self-hosted copy,
# or one on a paid provider, should raise it and re-run
# `scripts/calibrate_quotas.py` against its own numbers. Nothing about four is
# a product decision.
AGENT_MAX_TOOL_CALLS: int = int(os.getenv("AGENT_MAX_TOOL_CALLS", "4"))

# --- The two timeouts, and why they are two --------------------------------
#
#   AGENT_DEADLINE_SECONDS     the inner, application budget for research
#   GUNICORN_TIMEOUT_SECONDS   the outer, infrastructure limit on the request
#
# They are not alternatives and neither should be tuned alone. The agent's
# deadline stops it searching forever; the web server's timeout is the backstop
# for a request that has gone wrong in some way the agent cannot see. The outer
# one must stay comfortably above the inner one, because the deadline bounds the
# SEARCH phase only — after it expires a turn still has to run a final synthesis
# call, assemble citations, and stream the answer out. Measured turns finished
# between 90 and 122 seconds against a 75-second deadline, which is exactly that
# gap in practice.
#
# Set too close together, a perfectly good long answer is killed mid-stream and
# the person sees nothing at all, which is worse than a slow answer.
#
# Both come from the environment so they can move with the hosting plan, the
# model, or the size of the corpus without a code change. render.yaml passes
# GUNICORN_TIMEOUT_SECONDS to the start command and sets it here too, so the
# check below is comparing the number the server actually used.
AGENT_DEADLINE_SECONDS: int = int(os.getenv("AGENT_DEADLINE_SECONDS", "75"))
GUNICORN_TIMEOUT_SECONDS: int = int(os.getenv("GUNICORN_TIMEOUT_SECONDS", "180"))

# Room for MCP session overhead, the final synthesis call, citation resolution
# and delivering the response after the search budget is spent.
AGENT_TIMEOUT_HEADROOM_SECONDS: int = 60

# Raised from the 1024 default after a live answer was cut off mid-sentence:
# "...it is difficult to systematically characterize *when* and *why* agents
# fail" and then nothing. A truncated research answer is worse than a short one,
# because the reader cannot tell which it is.
AGENT_MAX_TOKENS: int = int(os.getenv("AGENT_MAX_TOKENS", "2400"))


def mcp_is_configured(mode: str = "research") -> bool:
    """True when every value the agent needs to reach the MCP server is present."""
    return bool(DATABRICKS_HOST and DATABRICKS_CLIENT_ID
                and DATABRICKS_CLIENT_SECRET and mcp_endpoint(mode))


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
#     mcp_server/shared_resource/middleware/request_context.py the agent writes as this user
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
# What these limits are FOR, because the previous answer was wrong.
#
# They were sized by dividing a 50-request-per-day free-model allowance by the
# 2.46 provider calls an agent question makes. That allowance meters only
# `:free` model variants. The configured model is a paid one, so the counter
# reads 0/50 on every credential while real money is spent - the arithmetic was
# sound and the input was fiction. Everything below was therefore derived from a
# meter the application never touches.
#
# Priced properly, a question costs hundredths of a cent: roughly fourteen
# thousand per dollar on the configured model, and a thousand-question crawl
# comes to about fifty cents. Money was never the binding constraint, so a
# ceiling computed from a budget lands in the thousands and protects nothing.
#
# These numbers are ABUSE protection. The risk is a crawler finding the demo and
# draining it overnight so that it fails when somebody who matters opens the
# link - authentication_and_demo_design.md §6d. They are chosen so that:
#
#   * a signed-in person can explore properly in one sitting rather than
#     rationing questions;
#   * several such people can do that on the same day;
#   * an anonymous visitor can try the headline feature before committing to an
#     account, while the anonymous tier as a whole cannot drain the day;
#   * a bot that finds the endpoint hits a wall long before anything expensive
#     or unavailable happens.
#
# GLOBAL_SPEND_USD_PER_DAY is the backstop underneath all of them, in the one
# unit that a cheaper model or a longer answer cannot quietly change.
#
# Authentication raises the allowance. It never removes it - an authenticated
# user with a runaway script costs exactly as much as an anonymous one.
#
# Re-derive with scripts/calibrate_quotas.py, which reads the live catalogue and
# every credential rather than trusting a number in a document.

# Search makes no completion call - it embeds via Hugging Face and queries
# pgvector - so it does not draw on the allowance the rest of this protects.
# Generous on purpose: being stingy here costs nothing and buys nothing.
ANON_SEARCH_PER_DAY: int = int(os.getenv("ANON_SEARCH_PER_DAY", "30"))
ANON_RAG_PER_DAY: int = int(os.getenv("ANON_RAG_PER_DAY", "5"))
# Two, not one. One question cannot show what the agent does: the first is spent
# learning how to ask. Two is still far too few to be worth automating.
ANON_AGENT_PER_DAY: int = int(os.getenv("ANON_AGENT_PER_DAY", "2"))

USER_SEARCH_PER_DAY: int = int(os.getenv("USER_SEARCH_PER_DAY", "300"))
# Enough to explore in one sitting. At five, a signed-in user spent their day
# before finding out whether the tool was any good - a limit sized to ration a
# free allowance that did not apply.
#
# Kept at or below the global ceilings below. A per-user allowance ABOVE the
# global one is not a looser limit, it is an incoherent pair: the individual
# number promises something the shared one will refuse, and the person is told
# they have allowance left at the moment they are turned away. These two were
# left at their placeholders when render.yaml was calibrated, which is exactly
# that state, and tests now forbid it.
USER_RAG_PER_DAY: int = int(os.getenv("USER_RAG_PER_DAY", "30"))
USER_AGENT_PER_DAY: int = int(os.getenv("USER_AGENT_PER_DAY", "25"))

# The ceilings that actually protect the demo, because no amount of
# cookie-clearing moves them.
#
# Sized so that roughly eight people can each have a full session on the same
# day before the feature rests. At the measured 2.48 provider requests per
# question that is about 500 upstream calls and, on the configured model,
# small change - the point is the shape of the day, not the bill.
GLOBAL_RAG_PER_DAY: int = int(os.getenv("GLOBAL_RAG_PER_DAY", "150"))
GLOBAL_AGENT_PER_DAY: int = int(os.getenv("GLOBAL_AGENT_PER_DAY", "200"))

# A second ceiling, for anonymous agent use alone.
#
# The per-visitor limit cannot protect a shared allowance: it resets with a
# cleared cookie, so ten curious visitors at two questions each are twenty
# questions however low it is set. This is the only limit that bounds the tier.
#
# A fifth of the global ceiling: enough that a genuine stream of first-time
# visitors is served, and not so much that anonymous traffic can take the day
# from people who signed in. What is left is reserved for them.
GLOBAL_ANON_AGENT_PER_DAY: int = int(os.getenv("GLOBAL_ANON_AGENT_PER_DAY", "40"))

# The backstop, in money.
#
# Every limit above counts REQUESTS, which is a proxy for spend that holds only
# while the per-request cost holds. Change the model, raise the tool budget, or
# let answers grow longer, and the same request count costs a different amount -
# which is how the previous numbers came to be built on a counter that read zero
# while money was leaving the account.
#
# This is the one unit that cannot drift. At the configured model's measured
# cost, the ceilings above come to a couple of cents a day, so a dollar is about
# fifty times normal use: it should never fire in ordinary operation, and it
# bounds the case nobody predicted - an expensive model configured by mistake, a
# retry loop, a question that reasons for pages.
#
# It TRAILS BY ONE OPERATION. Cost is only known once the provider reports it,
# so telemetry is written after the fact and this stops the next request rather
# than the one that crossed the line. For a backstop that is the right trade;
# the alternative is estimating a cost before spending it, and the request
# ceilings above are already that estimate.
#
# 0 disables it, for a self-hoster who would rather bound spend at the provider.
GLOBAL_SPEND_USD_PER_DAY: float = float(os.getenv("GLOBAL_SPEND_USD_PER_DAY", "1.00"))

# How often a background thread runs one trivial query, in seconds. 0 disables.
#
# Lakebase's free tier suspends compute after about FIVE MINUTES idle, and a
# suspended compute costs ~9.8 s to wake — measured 6 October 2026, against
# 0.33 s warm. TCP keepalives are already set on the pool and do not help: the
# socket is not what goes away, the server is.
#
# 240, not 600. The first value was chosen by analogy with Render's fifteen
# minute spin-down, before the database's own idle timer was known. At ten
# minutes against a five minute timer the compute suspends before the thread
# knocks, so visitors pay the cold connect anyway AND the deployment pays for a
# wake cycle every ten minutes to achieve it.
#
# This keeps the database warm only while the service is AWAKE. It cannot
# protect the endpoint overnight, when Render has stopped and this thread has
# stopped with it — that is what the external schedule in
# `context/setup/pinger_setup.md` is for, and the two are not interchangeable.
DB_KEEPWARM_SECONDS: int = int(os.getenv("DB_KEEPWARM_SECONDS", "240"))

# Off switch for local work, where metering only gets in the way.
QUOTAS_ENABLED: bool = os.getenv("QUOTAS_ENABLED", "true").lower() == "true"

# --- What is in front of this process -------------------------------------
#
# Render terminates TLS and forwards plain HTTP, so the app sees an `http`
# request even though the visitor is on `https`. One line cares:
# routes/auth.py builds the Google callback with url_for(_external=True), and a
# callback built as `http://` is one Google refuses to register for any host but
# localhost. Sign-in then works locally and fails only once deployed.
#
# ProxyFix reads the X-Forwarded-* headers a proxy adds so the app knows the real
# scheme and host. It is applied UNCONDITIONALLY and this number decides how much
# it believes:
#
#   0   nothing in front - the headers are ignored, so nobody can claim otherwise
#   1   one proxy in front (Render), and its headers are trusted
#
# A number rather than `if IS_PRODUCTION`, because a branch means the request
# path exercised in development is not the one that runs in production - which is
# how the bug above survives every local test. At 0 the wrapper is a pass-through,
# so both environments run the same code and differ only in a value.
#
# It states a fact about the deployment. Raise it only when a proxy is genuinely
# added, never to make something work: a hop count larger than the real one lets
# a caller forge the headers of the hops that do not exist.
TRUSTED_PROXY_HOPS: int = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))


# --- Session cookie ---
# Lax rather than Strict on purpose: the Google callback is a top-level GET
# navigation from accounts.google.com, and Strict would withhold the cookie on
# arrival - losing the OAuth `state` and breaking sign-in. Lax still withholds
# the cookie on cross-site POST, which is where the CSRF risk lives.
SESSION_COOKIE_SECURE: bool = IS_PRODUCTION
SESSION_COOKIE_HTTPONLY: bool = True
SESSION_COOKIE_SAMESITE: str = "Lax"
SESSION_LIFETIME_DAYS: int = int(os.getenv("SESSION_LIFETIME_DAYS", "14"))

# How long a validated session is trusted before the database is asked again.
#
# This is a deliberate trade, and it is worth naming precisely: revoking a
# session takes effect immediately in the worker that handled the revoke, and
# within this many seconds everywhere else. Checking on every request instead
# would make revocation instant and add a Lakebase round trip - several hundred
# milliseconds across the public endpoint - to every page load, which is a real
# cost paid constantly to close a one-minute window.
SESSION_CHECK_SECONDS: float = float(os.getenv("SESSION_CHECK_SECONDS", "60"))

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
_timeout_margin = GUNICORN_TIMEOUT_SECONDS - AGENT_DEADLINE_SECONDS
if _timeout_margin < AGENT_TIMEOUT_HEADROOM_SECONDS:
    logger.warning(
        "GUNICORN_TIMEOUT_SECONDS (%ds) leaves only %ds above "
        "AGENT_DEADLINE_SECONDS (%ds); %ds is the minimum that reliably covers "
        "MCP overhead, final synthesis, citation assembly and delivery. A long "
        "answer may be killed mid-stream.",
        GUNICORN_TIMEOUT_SECONDS, _timeout_margin, AGENT_DEADLINE_SECONDS,
        AGENT_TIMEOUT_HEADROOM_SECONDS,
    )

if not IS_PRODUCTION and ALLOW_DEV_USER_BYPASS:
    logger.warning(
        "ALLOW_DEV_USER_BYPASS is on — every request resolves to %s without "
        "signing in. Set ALLOW_DEV_USER_BYPASS=false to exercise the real Google flow.",
        DEV_USER_EMAIL,
    )
