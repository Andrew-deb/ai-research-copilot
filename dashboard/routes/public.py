"""
dashboard/routes/public.py — the pages a visitor reads before deciding.

About, help and pricing. Deliberately separate from the workspace: someone who
has not signed in is evaluating the product, and handing them a research
navigator before they know what it is asks them to explore something they have
no stake in yet.

Also home to the shell split. Two chromes, chosen per request:

    public      marketing navigation - sign in, sign up, about, help, pricing
    workspace   the research navigator - dashboard, search, collections, ...

The split is by *route*, not by a session flag. "Try demo" is a link into the
workspace rather than a mode a visitor toggles, so the shell follows the page
they are actually on. A flag would let the two disagree — workspace chrome on a
marketing page, or the reverse — which is the kind of state bug that only shows
up after someone uses the back button.
"""

from flask import Blueprint, render_template

from config import (
    ANON_AGENT_PER_DAY,
    DONATE_LABEL,
    DONATE_URL,
    ANON_RAG_PER_DAY,
    ANON_SEARCH_PER_DAY,
    USER_AGENT_PER_DAY,
    USER_RAG_PER_DAY,
    USER_SEARCH_PER_DAY,
)
from middleware.auth import current_user_id

bp = Blueprint("public", __name__)

# Endpoints that render the workspace shell even for an anonymous visitor —
# these are what "Try demo" leads into.
_WORKSPACE_ENDPOINTS = ("home.dashboard", "search.", "collections.", "progress.", "goals.")


def shell_mode(endpoint: str | None, authenticated: bool) -> str:
    """'workspace' or 'public' for the given endpoint."""
    if authenticated:
        return "workspace"
    if not endpoint:
        return "public"
    return "workspace" if endpoint.startswith(_WORKSPACE_ENDPOINTS) else "public"


def register_shell_context(app) -> None:
    from flask import request

    @app.context_processor
    def inject_shell() -> dict:
        mode = shell_mode(request.endpoint, current_user_id() is not None)
        return {"shell_mode": mode, "is_workspace_shell": mode == "workspace"}


@bp.get("/about")
def about():
    return render_template("about.html")


@bp.get("/help")
def help_page():
    """Plain-language answers. Paired with /docs behind a tab switch."""
    return render_template("help.html")


@bp.get("/docs")
def docs():
    """
    The technical half.

    Split from /help rather than merged into one long page: someone asking "why
    is this paper missing its sections" and someone asking "what embedding model
    is this" want different registers, and a single page serves whichever one it
    is written for badly.
    """
    return render_template("docs.html")


@bp.get("/pricing")
def pricing():
    """
    One plan, and the numbers are read from config rather than written into the
    template — a pricing page that disagrees with what the server enforces is
    worse than no pricing page.
    """
    return render_template(
        "pricing.html",
        anonymous={"search": ANON_SEARCH_PER_DAY, "rag": ANON_RAG_PER_DAY, "agent": ANON_AGENT_PER_DAY},
        member={"search": USER_SEARCH_PER_DAY, "rag": USER_RAG_PER_DAY, "agent": USER_AGENT_PER_DAY},
        donate_url=DONATE_URL,
        donate_label=DONATE_LABEL,
    )
