"""
dashboard/exceptions.py — Domain exception classes for the dashboard.

Mirrors mcp_server/exceptions.py. The Flask error handler in
dashboard/middleware/error_handler.py maps these to HTTP responses.
"""

from mcp_server.exceptions import (  # re-export shared exceptions
    CollectionNotFoundError,
    EmbeddingError,
    ExternalAPIError,
    GoalNotFoundError,
    NoteNotFoundError,
    PaperNotFoundError,
    ResearchCopilotError,
    ValidationError,
)

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
