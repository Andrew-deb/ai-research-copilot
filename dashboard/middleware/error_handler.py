"""
dashboard/middleware/error_handler.py — Domain exception → HTTP response mapping.

Services raise typed exceptions from dashboard/exceptions.py (re-exported from
mcp_server.exceptions). Routes never wrap calls in try/except — these handlers
convert every domain error into either a JSON body (for fetch/XHR callers) or a
flashed message with a redirect back (for form posts and page loads).
"""

import logging

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from dashboard.exceptions import (
    CollectionNotFoundError,
    ExternalAPIError,
    GoalNotFoundError,
    NoteNotFoundError,
    PaperNotFoundError,
    ResearchCopilotError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Domain exception → (HTTP status, short label)
_STATUS_MAP: list[tuple[type[Exception], int, str]] = [
    (ValidationError, 400, "Invalid input"),
    (PaperNotFoundError, 404, "Paper not found"),
    (CollectionNotFoundError, 404, "Collection not found"),
    (GoalNotFoundError, 404, "Learning goal not found"),
    (NoteNotFoundError, 404, "Note not found"),
    (ExternalAPIError, 502, "Upstream service error"),
    (ResearchCopilotError, 500, "Something went wrong"),
]


def _classify(exc: Exception) -> tuple[int, str]:
    for exc_type, status, label in _STATUS_MAP:
        if isinstance(exc, exc_type):
            return status, label
    return 500, "Something went wrong"


def _wants_json() -> bool:
    """True when the caller is a fetch()/XHR request rather than a browser navigation."""
    if request.path.startswith("/api/") or request.is_json:
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def register_error_handlers(app: Flask) -> None:

    @app.errorhandler(ResearchCopilotError)
    def handle_domain_error(exc: ResearchCopilotError):
        status, label = _classify(exc)
        message = str(exc) or label
        if status >= 500:
            logger.exception("Domain error (%s): %s", status, message)
        else:
            logger.info("Domain error (%s): %s", status, message)

        if _wants_json():
            return jsonify({"error": label, "detail": message}), status

        flash(message, "error")
        referrer = request.referrer
        if referrer and referrer != request.url:
            return redirect(referrer)
        return redirect(url_for("home.index"))

    @app.errorhandler(404)
    def handle_404(exc):
        if _wants_json():
            return jsonify({"error": "Not found", "detail": request.path}), 404
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def handle_500(exc):
        logger.exception("Unhandled 500")
        if _wants_json():
            return jsonify({"error": "Internal server error"}), 500
        return render_template("error.html", code=500, message="Internal server error."), 500
