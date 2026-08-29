"""
dashboard/middleware/error_handler.py — Domain exception → HTTP response mapping.

Services raise typed exceptions from dashboard/exceptions.py (re-exported from
mcp_server.exceptions). Routes never wrap calls in try/except — these handlers
convert every domain error into either a JSON body (for fetch/XHR callers) or a
flashed message with a redirect back (for form posts and page loads).
"""

import logging

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

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
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
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

        # A page navigation (GET) that failed → show the error page with the real
        # status. A form action (POST/DELETE) → flash and bounce back to the page.
        if request.method == "GET":
            return render_template("error.html", code=status, message=message), status

        flash(message, "error")
        referrer = request.referrer
        if referrer and referrer != request.url:
            return redirect(referrer)
        return redirect(url_for("home.index"))

    _HTTP_MESSAGES = {
        400: "Bad request.",
        401: "You are not authorised to view this page.",
        403: "Access denied.",
        404: "Page not found.",
        500: "Internal server error.",
    }

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        code = exc.code or 500
        if code >= 500:
            logger.exception("HTTP %s at %s", code, request.path)
        message = _HTTP_MESSAGES.get(code, exc.description or "Request failed.")
        if _wants_json():
            return jsonify({"error": exc.name, "detail": message}), code
        return render_template("error.html", code=code, message=message), code
