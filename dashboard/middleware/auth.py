"""
dashboard/middleware/auth.py — End-user identity resolution.

The dashboard runs on Render and authenticates users itself: Google OAuth, then a
Flask signed session cookie. It replaces the Databricks Apps model, where an OAuth
proxy injected `X-Forwarded-Email` on every request.

That header path is **deleted rather than kept as a fallback.** A dormant second way
to become a user is how authentication bypasses survive code review, and Render has
no proxy to set the header — so anything arriving with one would be forged.

Three identity outcomes, resolved once per request onto `g`:

    authenticated   a session cookie carrying user_id  ->  the users row
    dev bypass      ALLOW_DEV_USER_BYPASS, local only   ->  a fixed dev identity
    anonymous       ALLOW_ANONYMOUS_DEMO                ->  no users row at all

An anonymous visitor deliberately gets **no** database row. Minting one per visitor
would fill `users` with abandoned sessions and make "how many users are there?"
unanswerable. They are identified only by a random `anon_id` in their session, which
exists so quotas have something to count against.

`g.tier` is set here but not *enforced* here — the capability layer that stops an
anonymous visitor writing arrives in 3.2. Until it does, `ALLOW_ANONYMOUS_DEMO`
defaults to false (see config), so there is no window where anonymous means
unrestricted.

Routes and services never parse headers, read the session, or thread `user_id`
through call signatures. That property predates this rewrite and survives it: only
the *source* of identity changed.
"""

import logging
import secrets
import threading
import time

from flask import Flask, abort, g, redirect, request, session, url_for

from config import (
    ALLOW_ANONYMOUS_DEMO,
    ALLOW_DEV_USER_BYPASS,
    DEV_USER_EMAIL,
    DEV_USER_NAME,
)
from repositories import lakebase

logger = logging.getLogger(__name__)

SESSION_USER_KEY = "user_id"
SESSION_ANON_KEY = "anon_id"

TIER_AUTHENTICATED = "authenticated"
TIER_ANONYMOUS = "anonymous"

# Paths that must answer without resolving a user: no session, no database hit.
# /healthz in particular is Render's health check - making it depend on Lakebase
# would restart the service during a database blip it cannot fix by restarting.
_EXEMPT_PATHS = {"/healthz", "/favicon.ico"}

# Endpoints reachable while signed out. Everything else redirects to /login when
# anonymous access is disabled.
_PUBLIC_ENDPOINTS = {"auth.login", "auth.google", "auth.google_callback", "auth.logout"}

# Per-process cache: the users row for an id rarely changes, so this skips a SELECT
# on every request. Entries expire, so a row edited elsewhere is picked up.
_USER_CACHE: dict[str, tuple[float, dict]] = {}
_USER_CACHE_LOCK = threading.Lock()
_USER_CACHE_TTL = 300.0


def _cached_user(cache_key: str, loader) -> dict | None:
    now = time.monotonic()
    cached = _USER_CACHE.get(cache_key)
    if cached and now - cached[0] < _USER_CACHE_TTL:
        return cached[1]

    user = loader()
    if user:
        with _USER_CACHE_LOCK:
            _USER_CACHE[cache_key] = (now, user)
    return user


def forget_user(user_id: str) -> None:
    """Drop a cached row — called after sign-in so a fresh login sees fresh data."""
    with _USER_CACHE_LOCK:
        _USER_CACHE.pop(str(user_id), None)


def _dev_user() -> dict:
    """The local development identity. Config refuses to boot with this in production."""
    return _cached_user(
        f"dev:{DEV_USER_EMAIL}",
        lambda: lakebase.get_or_create_user(email=DEV_USER_EMAIL, display_name=DEV_USER_NAME),
    )


def _session_user() -> dict | None:
    """The signed-in user, or None if the cookie names a row that no longer exists."""
    user_id = session.get(SESSION_USER_KEY)
    if not user_id:
        return None

    user = _cached_user(str(user_id), lambda: lakebase.get_user_by_id(str(user_id)))
    if not user:
        # The account was deleted while the cookie lived on. Clearing the session
        # turns a confusing 500 on every page into a clean signed-out state.
        logger.info("Session referenced a missing user %s — clearing it.", user_id)
        session.clear()
    return user


def _anon_id() -> str:
    """Stable-per-session random id, so anonymous quota has something to count."""
    anon_id = session.get(SESSION_ANON_KEY)
    if not anon_id:
        anon_id = secrets.token_urlsafe(16)
        session[SESSION_ANON_KEY] = anon_id
    return anon_id


def _resolve_identity() -> tuple[dict | None, str]:
    """(user row or None, tier). Order matters: a real session always wins."""
    user = _session_user()
    if user:
        return user, TIER_AUTHENTICATED

    if ALLOW_DEV_USER_BYPASS:
        return _dev_user(), TIER_AUTHENTICATED

    if ALLOW_ANONYMOUS_DEMO:
        return None, TIER_ANONYMOUS

    return None, TIER_ANONYMOUS


def register_auth(app: Flask) -> None:
    """Attach the before_request identity hook and the template context processor."""

    @app.before_request
    def load_user():
        if request.endpoint == "static" or request.path in _EXEMPT_PATHS:
            return None

        user, tier = _resolve_identity()
        g.user = user
        g.tier = tier
        g.user_id = str(user["user_id"]) if user else None
        g.user_email = user["email"] if user else None
        g.anon_id = None if user else _anon_id()

        if user is None and not ALLOW_ANONYMOUS_DEMO:
            if request.endpoint in _PUBLIC_ENDPOINTS:
                return None
            # `next` is preserved so signing in returns you where you were, and is
            # validated on the way back out - see routes/auth.py::safe_next.
            return redirect(url_for("auth.login", next=request.full_path))

        return None

    @app.context_processor
    def inject_user() -> dict:
        return {
            "current_user": getattr(g, "user", None),
            "current_tier": getattr(g, "tier", TIER_ANONYMOUS),
            "is_authenticated": getattr(g, "user", None) is not None,
        }


# =============================================================================
# Accessors used by routes
# =============================================================================

def current_user() -> dict | None:
    """The resolved users row, or None for an anonymous visitor."""
    return getattr(g, "user", None)


def current_user_id() -> str | None:
    """The resolved user_id, or None when nobody is signed in."""
    return getattr(g, "user_id", None)


def current_user_email() -> str | None:
    return getattr(g, "user_email", None)


def current_tier() -> str:
    return getattr(g, "tier", TIER_ANONYMOUS)


def current_quota_scope() -> tuple[str, str]:
    """
    ("user", user_id) or ("anon", anon_id) — what a quota counts against.

    Provided here because identity is the only place that knows which of the two
    applies; 3.2's quota layer consumes it without re-deriving the rule.
    """
    user_id = current_user_id()
    if user_id:
        return "user", user_id
    return "anon", getattr(g, "anon_id", "unknown")


def require_user_id() -> str:
    """
    The signed-in user_id, or 401.

    Used by code that cannot meaningfully run without an account. The capability
    decorators in 3.2 give a friendlier answer; this is the backstop that makes a
    missed decorator fail closed rather than crash on a None user_id deeper down.
    """
    user_id = current_user_id()
    if not user_id:
        abort(401, description="Sign in to do that.")
    return user_id
