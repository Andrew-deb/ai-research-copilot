"""
mcp_server/exceptions.py — Domain exception classes.

Services raise these typed exceptions. The MCP error handler and
Flask error handler (in dashboard) convert them to structured responses.
No module catches these in routes or tools — middleware handles them.
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
