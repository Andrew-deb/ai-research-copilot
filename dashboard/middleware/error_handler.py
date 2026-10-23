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

from exceptions import (
    CollectionNotFoundError,
    ExternalAPIError,
    EmbeddingServiceError,
    GoalNotFoundError,
    NoteNotFoundError,
    UploadNotFoundError,
    PaperNotFoundError,
    ResearchCopilotError,
    CapabilityDeniedError,
    QuotaExceededError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Domain exception → (HTTP status, short label)
_STATUS_MAP: list[tuple[type[Exception], int, str]] = [
    (ValidationError, 400, "Invalid input"),
    # 403 not 401: the visitor is not unauthenticated in a way a WWW-Authenticate
    # header would fix — they are anonymous by design and need to sign in.
    (CapabilityDeniedError, 403, "Sign in required"),
    # 429 rather than 403: the answer is "later", not "never", and the difference
    # is what stops a signed-in user being told to sign in.
    (QuotaExceededError, 429, "Daily limit reached"),
    (PaperNotFoundError, 404, "Paper not found"),
    (CollectionNotFoundError, 404, "Collection not found"),
    (GoalNotFoundError, 404, "Learning goal not found"),
    (NoteNotFoundError, 404, "Note not found"),
    (UploadNotFoundError, 404, "File unavailable"),
    (EmbeddingServiceError, 503, "Semantic search unavailable"),
    (ExternalAPIError, 502, "Service unavailable"),
    (ResearchCopilotError, 500, "Something went wrong"),
]

# What a visitor reads when nothing more specific is known.
#
# Three properties, and the third is the one usually missed: it names what
# happened in words, it says what to do, and it does not pretend the visitor
# did something wrong. A person who typed a research question and got
# "Internal server error." has been told the computer's problem in the
# computer's vocabulary and given nothing to act on.
_FALLBACK_MESSAGE = (
    "Something went wrong at our end. Nothing you did caused it, and your work "
    "is safe. Please try again in a moment."
)


def _classify(exc: Exception) -> tuple[int, str]:
    for exc_type, status, label in _STATUS_MAP:
        if isinstance(exc, exc_type):
            return status, label
    return 500, "Something went wrong"


def speaks_to_the_visitor(exc: Exception) -> bool:
    """
    Whether this exception's own message was written for the person reading it.

    Not a question about status codes, which was the first answer here and the
    wrong one. Suppressing everything at 500 and above also suppressed
    `ExternalAPIError`, and that class carries the best sentence available —
    "The research assistant is unavailable right now, please try again in a few
    minutes" — while its diagnostics sit in `detail`. Replacing that with
    "Something went wrong at our end" threw away the useful half: a visitor who
    could have waited five minutes and retried was told only that something,
    somewhere, broke.

    So the question is about the contract each class keeps:

    - Below 500 the message is the refusal itself. "Keep your name under 80
      characters" is the only thing that makes it actionable.
    - `ExternalAPIError` separates the two audiences by construction, and every
      raise site is held to it by tests/test_error_messages.py.
    - `ResearchCopilotError` raised directly is the catch-all. Its text is
      whatever the raising code happened to say, which is how a connection pool
      and a module path end up in front of somebody researching proteins.
    - Anything else is not ours and made no promise at all.
    """
    if isinstance(exc, (ExternalAPIError, EmbeddingServiceError)):
        return True
    if not isinstance(exc, ResearchCopilotError):
        return False
    return _classify(exc)[0] < 500


def visitor_message(exc: Exception) -> str:
    """
    The sentence to show for an exception, by the same rule the handlers use.

    For code that cannot let the handlers do their job: a background worker
    thread serving a streamed agent turn, or a route that catches broadly in
    order to flash and redirect. Both used `str(exc)` and so bypassed the rule
    entirely — a quota refusal and a connection-pool failure came out looking
    identical, one of them carrying internal detail into a chat bubble.
    """
    if speaks_to_the_visitor(exc):
        return str(exc) or _classify(exc)[1]
    return _FALLBACK_MESSAGE


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
        raised = str(exc) or label

        # See speaks_to_the_visitor(): some of these messages were written for
        # the person reading them and some for whoever has to fix it, and the
        # status code alone does not tell them apart.
        message = visitor_message(exc)
        if status >= 500:
            logger.exception("Domain error (%s): %s", status, raised)
        else:
            logger.info("Domain error (%s): %s", status, raised)

        if _wants_json():
            body = {"error": label, "detail": message}
            # Enough context for the page to offer the right next step without
            # parsing the message text.
            if isinstance(exc, CapabilityDeniedError):
                body["requires_auth"] = exc.requires_auth
                body["sign_in_url"] = url_for("auth.login")
            elif isinstance(exc, QuotaExceededError):
                body["metric"] = exc.metric
                body["limit"] = exc.limit
                # A global ceiling must NOT invite signing in: it would not help,
                # and promising otherwise is worse than saying "come back later".
                body["requires_auth"] = exc.scope == "anon"
                if exc.scope == "anon":
                    body["sign_in_url"] = url_for("auth.login")
            return jsonify(body), status

        # A page navigation (GET) that failed → show the error page with the real
        # status. A form action (POST/DELETE) → flash and bounce back to the page.
        if request.method == "GET":
            return render_template("error.html", code=status, message=message), status

        flash(message, "error")
        referrer = request.referrer
        if referrer and referrer != request.url:
            return redirect(referrer)
        return redirect(url_for("home.index"))

    # Written for the person reading them, not for the specification. "Bad
    # request" and "Access denied" are accurate and accusatory; the versions
    # below say the same thing without implying fault, and each offers a next
    # step where one exists.
    _HTTP_MESSAGES = {
        400: "That request could not be understood. Try again, "
             "or go back and start over.",
        401: "You need to sign in to see this.",
        403: "You do not have access to this.",
        404: "That page does not exist. It may have moved, "
             "or the link may be out of date.",
        408: "That took too long and was stopped. Please try again.",
        413: "That is too large to accept.",
        429: "That is more requests than we can take right now. "
             "Please wait a moment and try again.",
        500: _FALLBACK_MESSAGE,
        502: "A service this page depends on is not responding. "
             "Please try again in a few minutes.",
        503: "The service is temporarily unavailable. "
             "Please try again in a few minutes.",
        504: "A service this page depends on took too long to answer. "
             "Please try again in a few minutes.",
    }

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        code = exc.code or 500
        if code >= 500:
            logger.exception("HTTP %s at %s", code, request.path)
        # `exc.description` is Werkzeug's own wording and leaks implementation
        # detail — "The browser (or proxy) sent a request that this server
        # could not understand." It is logged, never displayed.
        message = _HTTP_MESSAGES.get(code)
        if message is None:
            logger.info("Unmapped HTTP %s at %s: %s", code, request.path, exc.description)
            message = _FALLBACK_MESSAGE if code >= 500 else "That request could not be completed."

        if _wants_json():
            return jsonify({"error": exc.name, "detail": message}), code
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        """
        Anything that is not a domain error and not an HTTP error.

        Without this, an unexpected exception — Lakebase refusing a connection,
        a driver raising mid-query, any genuine bug — bypassed `error.html`
        entirely and reached the visitor as Werkzeug's own 500 page: a bare
        white document reading "Internal Server Error", with none of the
        application's chrome and no way back.

        The exception is logged with its traceback, because this is the handler
        that fires when something is actually wrong and the stack is the only
        thing that will explain it. The visitor gets a sentence.
        """
        logger.exception("Unhandled %s at %s %s",
                         type(exc).__name__, request.method, request.path)

        if _wants_json():
            return jsonify({"error": "Something went wrong",
                            "detail": _FALLBACK_MESSAGE}), 500
        return render_template("error.html", code=500, message=_FALLBACK_MESSAGE), 500
