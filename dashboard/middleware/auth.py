"""
dashboard/middleware/auth.py — End-user identity resolution.

Databricks Apps sit behind an OAuth2 proxy that injects the authenticated
user's identity as request headers on every proxied request:

    X-Forwarded-Email               → the user's email (primary key we use)
    X-Forwarded-Preferred-Username  → display name / UPN
    X-Forwarded-User                → the Databricks user id
    X-Forwarded-Access-Token        → the user's OAuth token (only if the app is
                                      configured for "on behalf of user" auth)

We resolve the email to a `users` row once per request and stash it on Flask's
`g`, so route and service code never parses headers or threads `user_id` through
call signatures.

Two modes, controlled by `REQUIRE_FORWARDED_AUTH` (config):
  * False (local dev)  — no header ⇒ fall back to the seeded demo user, the same
    identity mcp_server/middleware/request_context.py defaults to.
  * True  (Databricks) — no header ⇒ 401. The proxy should always supply it;
    a missing header means the request bypassed the proxy and must be refused.
"""

import logging
import threading
import time

from flask import Flask, abort, g, request

from config import (
    DEMO_USER_EMAIL,
    DEMO_USER_NAME,
    REQUIRE_FORWARDED_AUTH,
)
from repositories import lakebase

logger = logging.getLogger(__name__)

EMAIL_HEADER = "X-Forwarded-Email"
USERNAME_HEADER = "X-Forwarded-Preferred-Username"
USER_ID_HEADER = "X-Forwarded-User"

# Paths that must answer without resolving a user (no proxy header, no DB hit).
_EXEMPT_PATHS = {"/healthz", "/favicon.ico"}


def _forwarded_identity() -> tuple[str | None, str | None]:
    """(email, display_name) from proxy headers — either may be None/blank."""
    email = (request.headers.get(EMAIL_HEADER) or "").strip() or None
    name = (request.headers.get(USERNAME_HEADER) or "").strip() or None
    return email, name


# Small per-process cache: the user row for an email rarely changes, so we skip
# the users SELECT on every single request. Entries expire after _USER_CACHE_TTL.
_USER_CACHE: dict[str, tuple[float, dict]] = {}
_USER_CACHE_LOCK = threading.Lock()
_USER_CACHE_TTL = 300.0


def _resolve_user() -> dict:
    """Resolve the active request's user, honouring the auth mode."""
    email, display_name = _forwarded_identity()

    if not email:
        if REQUIRE_FORWARDED_AUTH:
            logger.warning("Rejected request to %s - no %s header", request.path, EMAIL_HEADER)
            abort(401, description="Missing Databricks identity header.")
        email, display_name = DEMO_USER_EMAIL, DEMO_USER_NAME

    now = time.monotonic()
    cached = _USER_CACHE.get(email)
    if cached and now - cached[0] < _USER_CACHE_TTL:
        return cached[1]

    user = lakebase.get_or_create_user(email=email, display_name=display_name)
    with _USER_CACHE_LOCK:
        _USER_CACHE[email] = (now, user)
    return user


def register_auth(app: Flask) -> None:
    """Attach the before_request identity hook and the template context processor."""

    @app.before_request
    def load_user() -> None:
        if request.endpoint == "static" or request.path in _EXEMPT_PATHS:
            return
        user = _resolve_user()
        g.user = user
        g.user_id = str(user["user_id"])
        g.user_email = user["email"]

    @app.context_processor
    def inject_user() -> dict:
        return {"current_user": getattr(g, "user", None)}


def current_user() -> dict:
    """The resolved users row for the active request."""
    return g.user


def current_user_id() -> str:
    """The resolved user_id (UUID string) for the active request."""
    return g.user_id


def current_user_email() -> str:
    """The resolved email for the active request."""
    return g.user_email
