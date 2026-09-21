"""
dashboard/routes/chat.py — the research chat shell.

**Shell only.** There is no agent behind this yet: no orchestration, no MCP calls,
no streaming, no persistence. Phase 3.3 wires the first two and 3.4 the rest.

It exists now because the agent is meant to be the centre of the product rather
than a widget bolted onto a dashboard, and that is a layout decision — one that
gets expensive to reverse once every page assumes a dashboard-shaped shell.

**No conversation schema.** Recent chats render an empty state rather than
reading a table, because inventing a message schema to satisfy a sidebar would
almost certainly get it wrong: the shape of a stored turn depends on what tool
calls and citations an agent actually produces, and none exist yet. The empty
state is honest and costs nothing to replace.
"""

from flask import Blueprint, render_template, request

import suggestions
from middleware.auth import current_tier
from middleware.capabilities import AGENT_QUERY, tier_can
from services import quota_service

bp = Blueprint("chat", __name__)

# A question carried in from elsewhere should not be longer than anything the
# composer would accept, and a URL is the easiest place for someone to paste
# something enormous.
MAX_CARRIED_PROMPT = 500


def carried_prompt() -> str:
    """
    A question handed over in `?q=`, trimmed and capped.

    Shared with the landing page, which runs the same composer and so has to
    treat the same parameter the same way. Two copies of a length cap is how one
    of them ends up not being a cap at all.
    """
    return (request.args.get("q") or "").strip()[:MAX_CARRIED_PROMPT]


@bp.get("/chat")
def new_chat():
    """
    A fresh conversation. The default landing spot for the signed-in agent.

    `?q=` prefills the composer, which is how the landing page hands a question
    over. Prefilling rather than submitting: the agent is not connected yet, and
    even once it is, arriving to find your question already running takes the
    decision away from whoever typed it.
    """
    return render_template(
        "chat.html",
        conversation=None,
        messages=[],
        starters=suggestions.AGENT_STARTERS,
        initial_prompt=carried_prompt(),
    )


@bp.get("/chat/<conversation_id>")
def conversation(conversation_id: str):
    """
    One conversation.

    Routed now so the sidebar, back button and shareable URLs all work the moment
    persistence lands, rather than requiring a second pass over the navigation.
    Until then every id renders the same empty shell.
    """
    return render_template(
        "chat.html",
        conversation={"conversation_id": conversation_id},
        messages=[],
        starters=suggestions.AGENT_STARTERS,
        initial_prompt="",
    )


def recent_conversations(limit: int = 8) -> list[dict]:
    """
    Sidebar history. Empty until the agent phase adds storage.

    A function rather than an empty list inline, so the sidebar has one place to
    start reading real data from and the template never changes.
    """
    return []


def register_chat_context(app) -> None:
    """Expose sidebar chat state to every template."""

    @app.context_processor
    def inject_chat() -> dict:
        tier = current_tier()
        can_ask = tier_can(tier, AGENT_QUERY)
        agent_quota = None
        if can_ask:
            agent_quota = quota_service.limit_for(tier, quota_service.AGENT_QUERY)
        return {
            "recent_chats": recent_conversations(),
            # Here rather than passed by each route: the composer is included by
            # the landing page as well now, and a route that forgot to pass this
            # would silently render a composer nobody is allowed to use.
            "can_ask": can_ask,
            "agent_daily_limit": agent_quota,
        }
