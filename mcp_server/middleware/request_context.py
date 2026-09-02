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


def get_current_user_id() -> str:
    """
    Get current user_id from contextvar, automatically defaulting to the demo user.
    """
    uid = _current_user_id.get()
    if not uid:
        uid = set_current_user(DEFAULT_USER_EMAIL, "Demo Researcher")
    return uid


def get_current_user_email() -> str:
    """Get current user email from contextvar."""
    email = _current_user_email.get()
    if not email:
        set_current_user(DEFAULT_USER_EMAIL, "Demo Researcher")
        return DEFAULT_USER_EMAIL
    return email
