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

from flask import Blueprint, jsonify, render_template, request

import suggestions
from middleware.auth import current_tier, current_user_id
from middleware.capabilities import AGENT_QUERY, consume_quota, require_capability, tier_can
from routes.helpers import form_or_json
from services import agent_service, quota_service, telemetry_service

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


@bp.post("/chat/ask")
@require_capability(AGENT_QUERY)
def ask():
    """
    One agent turn.

    A real endpoint with real enforcement and no agent behind it yet. The order
    of the three gates is the whole design:

      1. capability   may this tier use the agent at all? 403, answered by
                      logging in. Always checked, connected or not.
      2. connected    is there anything to run? While there is not, the turn is
                      refused with 503 and **no allowance is spent** - charging
                      a daily question for "not connected" takes payment for
                      work that did not happen, and on a free tier this small
                      the next attempt would be refused until tomorrow.
      3. quota        only once there is work. Consumed BEFORE it runs, so a
                      visitor with nothing left never reaches an LLM call.

    Phase 3.4 deletes branch 2, and 3 becomes unconditional - which is the
    ordering `@require_quota` would have given it all along.
    """
    question = (form_or_json("question").get("question") or "")
    tier = current_tier()
    user_id = current_user_id()

    if not agent_service.is_connected():
        # Validates first, so a malformed question is still a 400 rather than
        # being masked by the unavailability behind it.
        result = agent_service.ask(question, tier=tier, user_id=user_id)
        return jsonify(result), 503

    consume_quota(quota_service.AGENT_QUERY)

    with telemetry_service.measure(quota_service.AGENT_QUERY, tier, user_id) as op:
        result = agent_service.ask(question, tier=tier, user_id=user_id)
        op.llm_turns = result["usage"]["llm_turns"]
        op.tool_calls = result["usage"]["tool_calls"]
        op.embedding_calls = result["usage"]["embedding_calls"]

    return jsonify(result)


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
