"""
dashboard/app.py — Flask application factory for the AI Research & Learning Copilot dashboard.

Databricks App #2. Wires the four layers together and nothing else:
  config  →  repositories/lakebase.py  →  services/  →  routes/ (blueprints)
with middleware/ providing identity resolution and error handling.

Run locally:   flask --app dashboard.app run --debug
Deployed:      gunicorn dashboard.app:app   (see app.yaml)
"""

import logging

from flask import Flask

from config import DEBUG, SECRET_KEY
from middleware.auth import register_auth
from middleware.error_handler import register_error_handlers
from routes import register_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dashboard")


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(SECRET_KEY=SECRET_KEY, DEBUG=DEBUG, JSON_SORT_KEYS=False)

    register_auth(app)
    register_routes(app)
    register_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    logger.info("Dashboard app initialised (debug=%s)", DEBUG)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=DEBUG)
