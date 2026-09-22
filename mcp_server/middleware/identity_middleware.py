"""
mcp_server/middleware/identity_middleware.py — who a tool call is acting for.

The dashboard authenticates to this server as a **service principal**. Those
credentials say which *application* is calling; they say nothing about which
*person* the call is on behalf of, and the two must never be confused. When a
tool needs a user, the trusted backend supplies one explicitly in a header:

    X-RC-User-Id: <uuid>

Why this exists at all. `request_context.get_current_user_id()` falls back to
`demo@research-copilot.dev` whenever no identity has been set. For reads that is
harmless. For a write it is not: without this middleware, a signed-in
researcher's collections and notes would be written onto the demo account, with
no error raised anywhere and nothing in the logs to explain it. The fallback
makes the failure silent, which is the worst property a failure can have.

Set per request and reset afterwards. A worker thread is reused across requests,
and a contextvar left set is one user's identity leaking into the next user's
call.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware

from middleware.request_context import clear_current_user, set_current_user_id

logger = logging.getLogger("mcp_identity")

USER_ID_HEADER = "X-RC-User-Id"


class IdentityMiddleware(BaseHTTPMiddleware):
    """Bind the acting user from the request header for the life of the request."""

    async def dispatch(self, request, call_next):
        user_id = request.headers.get(USER_ID_HEADER)

        # Set even when absent — explicitly clearing is what stops the previous
        # request's user surviving on a reused worker.
        if user_id:
            set_current_user_id(user_id)
        else:
            clear_current_user()

        try:
            return await call_next(request)
        finally:
            clear_current_user()
