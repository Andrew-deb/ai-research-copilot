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

import suggestions
from middleware.auth import current_user_id
from routes.chat import carried_prompt
from services import home_service

bp = Blueprint("home", __name__)

@bp.get("/")
def index():
    """
    Chat-first for a new visitor; the workspace for someone signed in.

    "Chat-first" is meant literally: the landing page runs the same composer as
    /chat, so a visitor asks their question here rather than being handed to
    another route to ask it. The prompts live in `suggestions` alongside every
    other empty-input suggestion in the app.
    """
    if not current_user_id():
        return render_template(
            "landing.html",
            starters=suggestions.agent_landing_starters(),
            initial_prompt=carried_prompt(),
        )
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
