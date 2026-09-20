"""
dashboard/services/home_service.py — Dashboard home overview.

Assembles the numbers and short lists the landing page renders: stat cards
(goals, papers, reading status, notes), a reading-status breakdown, and the
most recent activity. One service call per page load, and three database round
trips rather than the eight this used to take.
"""

import logging

from repositories import lakebase

logger = logging.getLogger(__name__)

_READING_STATUSES = ["not_started", "reading", "completed", "skipped"]


def get_overview(user_id: str) -> dict:
    """Everything index.html needs, in a single dict."""
    # One query, not five. get_dashboard_stats used to be four statements, and the
    # get_progress_stats call that followed it was byte-identical to one of them.
    stats = lakebase.get_dashboard_stats(user_id)
    progress_stats = stats.get("reading_by_status") or {}

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
        # LIMIT in SQL rather than slicing in Python - otherwise every goal and
        # every progress row crosses the wire just to display five.
        "recent_goals": lakebase.get_learning_goals(user_id, limit=5),
        "recent_progress": lakebase.get_user_progress(user_id, limit=5),
    }
