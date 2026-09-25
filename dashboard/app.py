"""
dashboard/app.py — Flask application factory for the AI Research & Learning Copilot dashboard.

Databricks App #2. Wires the four layers together and nothing else:
  config  →  repositories/lakebase.py  →  services/  →  routes/ (blueprints)
with middleware/ providing identity resolution and error handling.

Run locally:   python -m app        (or: flask --app app run --debug)
Deployed:      gunicorn app:app     (see app.yaml)

Imports are flat (`from config import ...`) because a Databricks App deploy
flattens dashboard/'s *contents* to /app/python/source_code/ — there is no
`dashboard` package at runtime.
"""

import atexit
import logging
import os
import sys
import threading

# This directory is the import root in both layouts (repo `dashboard/` and the
# flattened Databricks source root), so flat imports below always resolve.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import datetime

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

import embedding
from config import (
    DEBUG,
    EMBEDDING_PRELOAD,
    SECRET_KEY,
    SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SESSION_LIFETIME_DAYS,
    TRUSTED_PROXY_HOPS,
)
from middleware.auth import register_auth
from middleware.capabilities import register_capabilities
from middleware.error_handler import register_error_handlers
from repositories import lakebase
from routes import register_routes
from routes.auth import init_oauth
from routes.chat import register_chat_context
from routes.public import register_shell_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dashboard")


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=SECRET_KEY,
        DEBUG=DEBUG,
        JSON_SORT_KEYS=False,
        # Identity now lives in a cookie rather than a proxy header, which makes
        # these flags load-bearing rather than cosmetic.
        SESSION_COOKIE_HTTPONLY=SESSION_COOKIE_HTTPONLY,
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        SESSION_COOKIE_SAMESITE=SESSION_COOKIE_SAMESITE,
        PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=SESSION_LIFETIME_DAYS),
    )

    # Cookie authentication is forgeable across origins in a way header
    # authentication was not: before this change an attacker's page could not make
    # an authenticated request, because it could not set X-Forwarded-Email. Now the
    # browser attaches the session cookie itself. SameSite=Lax blocks the common
    # cross-site form POST, but it is one mitigation rather than a control, so
    # every state-changing request carries a token as well.
    CSRFProtect(app)

    # Applied in every environment, with TRUSTED_PROXY_HOPS deciding how much of
    # the forwarded headers to believe - 0 locally, where it is a pass-through,
    # and 1 on Render. Every count is named explicitly: ProxyFix defaults x_for
    # and x_proto to 1, so leaving one out would silently trust a header nobody
    # had thought about.
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUSTED_PROXY_HOPS,
        x_proto=TRUSTED_PROXY_HOPS,
        x_host=TRUSTED_PROXY_HOPS,
        x_port=0,
        x_prefix=0,
    )

    init_oauth(app)
    register_auth(app)
    register_capabilities(app)
    register_chat_context(app)
    register_shell_context(app)
    register_routes(app)
    register_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "embedding_model_loaded": embedding.is_loaded()}

    if EMBEDDING_PRELOAD:
        threading.Thread(target=embedding.warmup, name="embedding-warmup", daemon=True).start()

    atexit.register(lakebase.close_pool)

    logger.info("Dashboard app initialised (debug=%s, preload=%s)", DEBUG, EMBEDDING_PRELOAD)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=DEBUG)
