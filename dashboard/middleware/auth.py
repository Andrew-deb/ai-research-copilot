"""
dashboard/middleware/auth.py — End-user identity resolution.

Databricks Apps sit behind a proxy that injects the authenticated user's
email as the `X-Forwarded-Email` header on every request. We resolve that
email to a `users` row once per request and stash it on Flask's `g`, so
route and service code never has to parse headers or thread `user_id`
through call signatures.

Local dev has no proxy, so we fall back to the seeded demo user — the same
identity `mcp_server/middleware/request_context.py` defaults to, so the
agent and the dashboard operate on one account.
"""

import logging

from flask import Flask, g, request

from dashboard.repositories import lakebase

logger = logging.getLogger(__name__)

FORWARDED_EMAIL_HEADER = "X-Forwarded-Email"
FORWARDED_USER_HEADER = "X-Forwarded-Preferred-Username"

DEFAULT_USER_EMAIL = "demo@research-copilot.dev"
DEFAULT_USER_NAME = "Demo Researcher"


def _resolve_user() -> dict:
    """Read identity headers (or demo defaults) and upsert the matching users row."""
    email = request.headers.get(FORWARDED_EMAIL_HEADER, "").strip() or DEFAULT_USER_EMAIL
    display_name = request.headers.get(FORWARDED_USER_HEADER, "").strip() or None

    if email == DEFAULT_USER_EMAIL and not display_name:
        display_name = DEFAULT_USER_NAME

    return lakebase.get_or_create_user(email=email, display_name=display_name)


def register_auth(app: Flask) -> None:
    """Attach a before_request hook that populates g.user / g.user_id."""

    _EXEMPT_PATHS = {"/healthz", "/favicon.ico"}

    @app.before_request
    def load_user() -> None:
        # Static assets and health checks never touch user data.
        if request.endpoint == "static" or request.path in _EXEMPT_PATHS:
            return
        user = _resolve_user()
        g.user = user
        g.user_id = str(user["user_id"])

    @app.context_processor
    def inject_user() -> dict:
        return {"current_user": getattr(g, "user", None)}


def current_user_id() -> str:
    """Return the resolved user_id for the active request."""
    return g.user_id
