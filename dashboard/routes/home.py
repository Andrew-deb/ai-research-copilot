"""
dashboard/routes/home.py — the landing page and the workspace overview.

`/` shows two different things, and the split is the point of the Phase 3.2 shell:

    anonymous      a chat-first landing page. A first-time visitor should grasp
                   what this is within seconds, and be able to try it, rather
                   than arriving at an empty dashboard of someone else's data.

    authenticated  their workspace overview, which is what they came back for.

`/dashboard` always renders the overview, so "Try Demo" has somewhere to go and
the sidebar has a stable target regardless of who is signed in.
"""

from flask import Blueprint, render_template

from middleware.auth import current_user_id
from services import home_service

bp = Blueprint("home", __name__)

# Shown on the landing page under the composer. Deliberately concrete: "ask me
# anything" tells a visitor nothing about what this corpus can answer, while a
# real question shows both the subject matter and the depth available.
#
# These are agent questions, not search queries, and the difference is the point
# of the landing page. Every one of them needs several papers read and set
# against each other - compare, trace, sequence - which is precisely what a
# single retrieval pass cannot do. Suggesting searches here would advertise the
# one capability the demo workspace already hands to anonymous visitors, so the
# page would be selling something nobody needs to be sold.
SUGGESTED_PROMPTS = [
    "Compare the main approaches to retrieval-augmented generation and where they disagree",
    "What limitations do authors repeatedly report with LLM agents and tool use?",
    "Trace how the evaluation of human feedback has changed over time",
    "Build me a reading path into indexing dense vectors at scale",
]


@bp.get("/")
def index():
    """Chat-first for a new visitor; the workspace for someone signed in."""
    if not current_user_id():
        return render_template("landing.html", prompts=SUGGESTED_PROMPTS)
    overview = home_service.get_overview(current_user_id())
    return render_template("index.html", **overview)


@bp.get("/dashboard")
def dashboard():
    """
    The workspace overview, for anyone.

    An anonymous visitor reaching this through "Try Demo" sees the real page with
    their own (empty) numbers and the curated collections — the product's shape,
    honestly, rather than someone else's data dressed up as theirs.
    """
    overview = home_service.get_overview(current_user_id())
    return render_template("index.html", **overview)
