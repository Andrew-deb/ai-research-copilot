"""
dashboard/exceptions.py — Domain exception classes for the dashboard.

Mirrors mcp_server/exceptions.py. The Flask error handler in
dashboard/middleware/error_handler.py maps these to HTTP responses.
"""

class ResearchCopilotError(Exception):
    """Base exception for all application domain errors."""


class PaperNotFoundError(ResearchCopilotError):
    """Raised when a paper ID or DOI does not exist in the database or any API."""


class CollectionNotFoundError(ResearchCopilotError):
    """Raised when a collection ID does not exist or does not belong to the user."""


class GoalNotFoundError(ResearchCopilotError):
    """Raised when a learning goal ID does not exist or does not belong to the user."""


class NoteNotFoundError(ResearchCopilotError):
    """Raised when a note ID does not exist or does not belong to the user."""


class ValidationError(ResearchCopilotError):
    """Raised when input fails domain validation rules (e.g., empty title, invalid status)."""


class ExternalAPIError(ResearchCopilotError):
    """
    Raised when a broker HTTP call fails after all retries.
    Wraps the underlying requests.HTTPError so callers don't need to import requests.
    """


class EmbeddingError(ResearchCopilotError):
    """Raised when the embedding model fails to produce a vector."""

__all__ = [
    "ResearchCopilotError",
    "PaperNotFoundError",
    "CollectionNotFoundError",
    "GoalNotFoundError",
    "NoteNotFoundError",
    "ValidationError",
    "ExternalAPIError",
    "EmbeddingError",
]


class CapabilityDeniedError(ResearchCopilotError):
    """
    Raised when the current tier may not use a feature at all.

    Distinct from QuotaExceededError on purpose: this one is answered by signing
    in, that one by waiting. Collapsing them would mean telling a signed-in user
    who ran out of allowance to sign in.
    """

    def __init__(self, message: str, capability: str | None = None,
                 requires_auth: bool = True):
        super().__init__(message)
        self.capability = capability
        # False for curated demo content, which nobody may edit - so the UI knows
        # not to offer signing in as the remedy.
        self.requires_auth = requires_auth


class QuotaExceededError(ResearchCopilotError):
    """
    Raised when a metered capability has no allowance left.

    `scope` distinguishes "you have used yours" from "the whole application is
    resting", which need different messages: the first invites signing in, the
    second must not, because signing in would not help.
    """

    def __init__(self, message: str, metric: str, scope: str,
                 used: int, limit: int):
        super().__init__(message)
        self.metric = metric
        self.scope = scope
        self.used = used
        self.limit = limit
