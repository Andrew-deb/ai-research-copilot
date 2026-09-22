"""
dashboard/routes/chat.py — the research chat shell.

The agent is the centre of the product rather than a widget bolted onto a
dashboard, which is why it got its own route before it had an implementation.

`/chat/ask` serves one turn two ways, chosen by the Accept header: plain JSON
for a caller that wants a single answer, and Server-Sent Events for the page,
which relays the agent's real steps while it works. A turn takes the better part
of a minute, and a minute of silence reads as a hang.

**No conversation schema.** Recent chats render an empty state rather than
reading a table, because inventing a message schema to satisfy a sidebar would
almost certainly get it wrong: the shape of a stored turn depends on what tool
calls and citations an agent actually produces, and none exist yet. The empty
state is honest and costs nothing to replace.
"""

import json
import logging
import queue
import threading

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

import suggestions
from exceptions import ResearchCopilotError
from middleware.auth import current_tier, current_user_id
from middleware.capabilities import AGENT_QUERY, consume_quota, require_capability, tier_can
from routes.helpers import form_or_json
from services import agent_service, quota_service, telemetry_service

logger = logging.getLogger(__name__)

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

    Every gate is settled before a delivery method is chosen, and that ordering
    is the whole design:

      1. capability   may this tier use the agent at all? 403, answered by
                      logging in. Always checked, connected or not.
      2. validation   a malformed question is 400 here, while a status code can
                      still be chosen. Inside a stream it could only be an
                      event, telling the browser a bad request had succeeded.
      3. connected    is there anything to run? If not, 503 and **no allowance
                      is spent** - charging a daily question for "not available"
                      takes payment for work that did not happen, and on a free
                      tier this small the next attempt would be refused until
                      tomorrow.
      4. quota        only once there is work, and BEFORE it runs, so a visitor
                      with nothing left never reaches an LLM call.

    Only then does the Accept header decide between a stream and a single JSON
    body. Both run the same turn through `_run_turn`.
    """
    # Validated here, not inside the turn: once a stream is open the status code
    # is already spent, and a 400 delivered as a stream event is a bad request
    # the browser was told to treat as success.
    question = agent_service.validate_question(
        form_or_json("question").get("question") or "")
    tier = current_tier()
    user_id = current_user_id()

    if not agent_service.is_connected():
        # Validates first, so a malformed question is still a 400 rather than
        # being masked by the unavailability behind it.
        result = agent_service.ask(question, tier=tier, user_id=user_id)
        return jsonify(result), 503

    consume_quota(quota_service.AGENT_QUERY)

    if _wants_stream():
        return Response(
            stream_with_context(_stream_turn(question, tier, user_id)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Proxies that buffer a response defeat the entire point of
                # streaming it: the page would sit silent and then receive
                # everything at once, which is what it does today.
                "X-Accel-Buffering": "no",
            },
        )

    return jsonify(_run_turn(question, tier, user_id))


def _wants_stream() -> bool:
    """
    Streaming is opt-in by Accept header, on the same endpoint.

    A second URL would mean two routes to keep in step on capability, quota and
    validation; content negotiation keeps one set of gates. The JSON path stays
    for callers that want a single answer - the tests among them.
    """
    return "text/event-stream" in (request.headers.get("Accept") or "")


def _run_turn(question: str, tier: str, user_id: str | None, on_event=None) -> dict:
    """One measured turn. Shared by the JSON and streaming paths."""
    with telemetry_service.measure(quota_service.AGENT_QUERY, tier, user_id) as op:
        result = agent_service.ask(question, tier=tier, user_id=user_id,
                                   on_event=on_event)
        op.llm_turns = result["usage"]["llm_turns"]
        op.tool_calls = result["usage"]["tool_calls"]
        op.embedding_calls = result["usage"]["embedding_calls"]
    return result


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _stream_turn(question: str, tier: str, user_id: str | None):
    """
    Run the turn on a worker thread and relay its progress as it happens.

    A thread is needed because the turn is a single blocking call — the MCP
    session and the tool loop live inside it — so a generator cannot yield from
    the middle of it. The worker pushes events onto a queue and this generator
    drains the queue, which is the only way the page learns anything before the
    whole 70 seconds is over.

    The worker touches no request state: agent_service reads config and calls
    out, and every value it needs is passed in. Reaching for `g` or `request`
    from here would fail, and silently, on the thread.
    """
    events: queue.Queue = queue.Queue()
    outcome: dict = {}

    def work():
        try:
            outcome["result"] = _run_turn(question, tier, user_id, on_event=events.put)
        except ResearchCopilotError as exc:
            outcome["error"] = str(exc)
        except Exception as exc:                      # noqa: BLE001
            logger.exception("Agent turn failed")
            outcome["error"] = "The research assistant failed on that question."
        finally:
            events.put(None)                          # sentinel: work is over

    worker = threading.Thread(target=work, daemon=True)
    worker.start()

    # Sent immediately so the page can switch out of its idle state without
    # waiting for the first real event, which may be seconds away.
    yield _sse({"type": "status", "phase": "thinking"})

    while True:
        event = events.get()
        if event is None:
            break
        yield _sse(event)

    if "error" in outcome:
        yield _sse({"type": "error", "message": outcome["error"]})
    else:
        yield _sse({"type": "done", "result": outcome["result"]})


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
