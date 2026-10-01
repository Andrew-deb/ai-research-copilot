"""
mcp_server/middleware/request_context.py — User Context & Session Resolution.

Uses Python contextvars to track user identity (email and user_id) throughout
tool execution pipelines without threading issues or pollution of tool signatures.
"""

from contextvars import ContextVar
from typing import Optional
from repositories import lakebase

_current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)
_current_user_id: ContextVar[Optional[str]] = ContextVar("current_user_id", default=None)

DEFAULT_USER_EMAIL = "demo@research-copilot.dev"


def set_current_user(email: str, display_name: Optional[str] = None) -> str:
    """
    Resolve or provision user in Lakebase, setting contextvars for downstream services.
    Returns the resolved user_id UUID string.
    """
    user_record = lakebase.get_or_create_user(email=email, display_name=display_name)
    user_id = str(user_record["user_id"])
    
    _current_user_email.set(email)
    _current_user_id.set(user_id)
    return user_id


def set_current_user_id(user_id: str) -> None:
    """
    Bind an already-resolved user id, with no database round trip.

    Used by IdentityMiddleware: the dashboard has already authenticated the
    person and holds their id, so resolving them again here would be a lookup
    to confirm something the trusted caller just told us.
    """
    _current_user_id.set(user_id)
    _current_user_email.set(None)


def clear_current_user() -> None:
    """
    Unbind the acting user.

    Called at the end of every request. Worker threads are reused, and a
    contextvar left set is one person's identity leaking into the next
    person's tool call.
    """
    _current_user_id.set(None)
    _current_user_email.set(None)


def get_current_user_id() -> str:
    """
    Get current user_id from contextvar, defaulting to the demo user.

    Safe for reads. NOT safe for writes — see require_current_user_id: this
    returns a real, writable account when nobody is bound, which is how a
    signed-in user's data ends up on the demo profile.
    """
    uid = _current_user_id.get()
    if not uid:
        uid = set_current_user(DEFAULT_USER_EMAIL, "Demo Researcher")
    return uid


def require_current_user_id() -> str:
    """
    The acting user, or raise.

    What every write should use. The difference from get_current_user_id is the
    whole point: absent identity must stop a write, loudly, rather than silently
    redirecting it onto the demo account where nobody will ever look for it.
    """
    uid = _current_user_id.get()
    if not uid:
        raise PermissionError(
            "This action needs a specific user, and none was supplied. The "
            "caller must send the X-RC-User-Id header."
        )
    return uid


def get_bound_user_id() -> Optional[str]:
    """
    The acting user id, or None. No fallback, no database, no side effects.

    What telemetry should read. The difference from get_current_user_id is that
    this one cannot change the answer to the question it is asked - see the note
    on get_current_user_email for what that cost.
    """
    return _current_user_id.get()


def get_current_user_email() -> Optional[str]:
    """
    The acting user's email, or None when only an id is bound.

    THIS FUNCTION USED TO PROVISION. When no email was set it called
    set_current_user(DEFAULT_USER_EMAIL), which resolves the demo account and
    writes its id into _current_user_id - so merely *asking* who was acting
    replaced the answer with the demo user.

    That is not a hypothetical. IdentityMiddleware binds an id and sets the
    email to None, which is exactly the branch that provisioned; and
    trace_middleware read this one line before running the tool. Every traced
    call therefore executed as the demo user, and mcp_traces recorded
    demo@research-copilot.dev for all of them - the trace table was not showing
    the symptom, it was writing the cause.

    None rather than the demo address when an id is bound and no email is: the
    dashboard sends an id and no email, and naming the demo user there would be
    a trace that says a signed-in person's work was done by somebody else.
    """
    return _current_user_email.get()
