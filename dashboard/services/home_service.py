"""
dashboard/services/home_service.py — Dashboard home overview.

Assembles the numbers and short lists the landing page renders: stat cards
(goals, papers, reading status, notes), a reading-status breakdown, and the
most recent activity. One service call per page load.
"""

import logging

from dashboard.repositories import lakebase

logger = logging.getLogger(__name__)

_READING_STATUSES = ["not_started", "reading", "completed", "skipped"]


def get_overview(user_id: str) -> dict:
    """Everything index.html needs, in a single dict."""
    stats = lakebase.get_dashboard_stats(user_id)
    progress_stats = lakebase.get_progress_stats(user_id)

    return {
        "stats": {
            "active_goals": stats.get("active_goals", 0),
            "papers_in_collections": stats.get("papers_in_collections", 0),
            "notes_written": stats.get("notes_written", 0),
            "papers_completed": progress_stats.get("completed", 0),
        },
        "reading_breakdown": [
            {"status": status, "count": progress_stats.get(status, 0)}
            for status in _READING_STATUSES
        ],
        "recent_papers": lakebase.get_recent_papers(limit=5),
        "recent_goals": lakebase.get_learning_goals(user_id)[:5],
        "recent_progress": lakebase.get_user_progress(user_id)[:5],
    }
