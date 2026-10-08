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
    DB_KEEPWARM_SECONDS,
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
    #
    # Registered AFTER register_auth below, and the order is load-bearing. Flask
    # runs before_request hooks in registration order, so with CSRF ahead of
    # identity a rejected POST aborted before g.user was ever resolved — and the
    # 400 page rendered the signed-out shell, "Log in" and "You're viewing the
    # demo workspace", to someone who had been signed in the whole time. The
    # error told them the wrong thing about themselves while the actual fault
    # was a missing token in the form.
    #
    # Resolving identity first costs one session read on a request that may be
    # refused anyway. Knowing who someone is before deciding whether to refuse
    # them is worth that, and every error page past this point can address them
    # correctly.

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

    CSRFProtect(app)

    register_capabilities(app)
    register_chat_context(app)
    register_shell_context(app)
    register_routes(app)
    register_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        """
        Liveness. Deliberately touches nothing.

        This is Render's health check path AND the external pinger's target, and
        both reasons point the same way: a health check that queried the
        database would restart the service during a database blip it cannot fix
        by restarting, and would make the keep-awake ping fail exactly when the
        service most needed to stay up.
        """
        return {"status": "ok", "embedding_model_loaded": embedding.is_loaded()}

    @app.get("/readyz")
    def readyz():
        """
        Readiness. Does one cheap query, and says so honestly.

        Separate from /healthz on purpose, and NOT wired to Render's
        healthCheckPath — see above. This is for a human diagnosing "is the
        database reachable from the app?", and for an external monitor that
        wants to know the difference between awake and usable.

        Measured from a developer machine on 6 October 2026: a cold Lakebase
        connect took 9.8 s, a warm round trip 0.33 s. The gap is the free
        tier's compute suspending, which TCP keepalives cannot prevent.
        """
        ok, detail = lakebase.ping()
        return ({"status": "ok" if ok else "degraded", "database": detail},
                200 if ok else 503)

    if EMBEDDING_PRELOAD:
        threading.Thread(target=embedding.warmup, name="embedding-warmup", daemon=True).start()

    # Keeps the database warm while the service is awake.
    #
    # The external pinger keeps RENDER awake; nothing was keeping LAKEBASE
    # awake, and its free tier suspends compute after inactivity. Pinging
    # /healthz more often would not have helped — it never touches the database,
    # for the reasons above.
    #
    # In-process rather than a second external schedule: it runs only while the
    # service is up, which is exactly when a warm pool is worth having, and it
    # needs nothing configured outside the deployment.
    if DB_KEEPWARM_SECONDS > 0:
        threading.Thread(target=lakebase.keep_warm, args=(DB_KEEPWARM_SECONDS,),
                         name="db-keepwarm", daemon=True).start()

    atexit.register(lakebase.close_pool)

    logger.info("Dashboard app initialised (debug=%s, preload=%s)", DEBUG, EMBEDDING_PRELOAD)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=DEBUG)
