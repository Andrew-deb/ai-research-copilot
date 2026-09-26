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

from flask import (Blueprint, Response, abort, jsonify, redirect,
                   render_template, request, stream_with_context, url_for)

import llm_client
import suggestions
from exceptions import ResearchCopilotError
from middleware.auth import current_tier, current_user_id
from middleware.capabilities import AGENT_QUERY, consume_quota, require_capability, tier_can
from routes.helpers import form_or_json, wants_json
from services import (agent_service, conversation_service, quota_service,
                      telemetry_service)

logger = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)

# A question carried in from elsewhere should not be longer than anything the
# composer would accept, and a URL is the easiest place for someone to paste
# something enormous.
MAX_CARRIED_PROMPT = 500

# Keep a streaming response visibly alive while an upstream provider is quiet.
# This is an SSE comment rather than a UI event, so browsers/proxies see bytes
# but the chat trace does not gain fake progress steps.
SSE_HEARTBEAT_SECONDS = 15


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
    One stored conversation, replayed.

    The messages are handed to the page in the envelope's own shape and drawn by
    the same renderer a live turn uses, so a reopened answer looks like the one
    that was given — citations panel, step trace and all. Rebuilding them into
    some other shape here is how a replay drifts from the original.
    """
    stored = conversation_service.load(current_user_id(), conversation_id)
    if not stored:
        abort(404)

    return render_template(
        "chat.html",
        conversation={"conversation_id": conversation_id, "title": stored["title"]},
        messages=stored["messages"],
        starters=suggestions.AGENT_STARTERS,
        initial_prompt="",
    )


@bp.post("/chat/<conversation_id>/rename")
def rename_conversation(conversation_id: str):
    """Retitle a conversation. The sidebar renames in place, so this answers JSON."""
    title = (form_or_json("title").get("title") or "")
    renamed = conversation_service.rename(current_user_id(), conversation_id, title)
    if not renamed:
        abort(404)
    return jsonify({"conversation_id": conversation_id, "title": renamed["title"]})


@bp.post("/chat/<conversation_id>/pin")
def pin_conversation(conversation_id: str):
    """
    Pin or unpin, with the desired state sent explicitly.

    A toggle computed on the server would disagree with the page the moment two
    tabs are open: both would send "flip it" and the second would undo the first.
    """
    payload = form_or_json("pinned")
    wanted = payload.get("pinned")
    if isinstance(wanted, str):
        wanted = wanted.lower() not in ("false", "0", "")

    updated = conversation_service.set_pinned(
        current_user_id(), conversation_id, bool(wanted))
    if not updated:
        abort(404)
    return jsonify({"conversation_id": conversation_id, "pinned": updated["pinned"]})


@bp.post("/chat/<conversation_id>/delete")
def delete_conversation(conversation_id: str):
    """
    Remove a conversation.

    POST rather than GET: a link that deletes is a link a browser, a crawler or
    a prefetcher can follow without anyone meaning to.
    """
    removed = conversation_service.delete(current_user_id(), conversation_id)
    if not removed:
        abort(404)
    if wants_json():
        return jsonify({"deleted": True, "conversation_id": conversation_id})
    return redirect(url_for("chat.new_chat"))


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
    payload = form_or_json("question", "conversation_id")
    question = agent_service.validate_question(payload.get("question") or "")
    conversation_id = (payload.get("conversation_id") or "").strip() or None
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
            stream_with_context(_stream_turn(question, tier, user_id, conversation_id)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Proxies that buffer a response defeat the entire point of
                # streaming it: the page would sit silent and then receive
                # everything at once, which is what it does today.
                "X-Accel-Buffering": "no",
            },
        )

    result = _run_turn(question, tier, user_id, conversation_id)
    return jsonify(_with_conversation(result, user_id, conversation_id, question))


def _wants_stream() -> bool:
    """
    Streaming is opt-in by Accept header, on the same endpoint.

    A second URL would mean two routes to keep in step on capability, quota and
    validation; content negotiation keeps one set of gates. The JSON path stays
    for callers that want a single answer - the tests among them.
    """
    return "text/event-stream" in (request.headers.get("Accept") or "")


def _run_turn(question: str, tier: str, user_id: str | None,
              conversation_id: str | None = None, on_event=None) -> dict:
    """
    One measured turn. Shared by the JSON and streaming paths.

    The tally is created here and filled by the model calls inside, so it is
    complete even when `ask` returns a not_connected envelope after spending a
    turn or two. Assigned before the `with` block closes, because that is where
    the row is written.
    """
    tally = llm_client.Usage()
    with telemetry_service.measure(quota_service.AGENT_QUERY, tier, user_id) as op:
        try:
            history = conversation_service.agent_context(
                user_id, conversation_id)
            result = agent_service.ask(
                question,
                tier=tier,
                user_id=user_id,
                conversation_history=history,
                on_event=on_event,
                usage=tally,
            )
            op.llm_turns = result["usage"]["llm_turns"]
            op.tool_calls = result["usage"]["tool_calls"]
            op.embedding_calls = result["usage"]["embedding_calls"]
        finally:
            # In a finally: a turn that raises still burned tokens, and an
            # expensive failure is the one measurement 3.6 can least afford to
            # be missing when it sets a ceiling.
            op.spent(tally)
    return result


def _with_conversation(result: dict, user_id: str | None,
                       conversation_id: str | None, question: str) -> dict:
    """
    Persist the turn and tell the page where it landed.

    The id rides alongside the envelope rather than inside it: which
    conversation a turn belongs to is a routing concern, and agent_service has
    no business knowing about storage.
    """
    stored = conversation_service.record_turn(user_id, conversation_id, question, result)
    return dict(result, conversation_id=stored)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _stream_turn(question: str, tier: str, user_id: str | None,
                 conversation_id: str | None = None):
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
            result = _run_turn(
                question, tier, user_id, conversation_id, on_event=events.put)
            outcome["result"] = _with_conversation(
                result, user_id, conversation_id, question)
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
        try:
            event = events.get(timeout=SSE_HEARTBEAT_SECONDS)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        if event is None:
            break
        yield _sse(event)

    if "error" in outcome:
        yield _sse({"type": "error", "message": outcome["error"]})
    else:
        yield _sse({"type": "done", "result": outcome["result"]})


def recent_conversations(limit: int = conversation_service.RECENT_LIMIT) -> list[dict]:
    """Sidebar history for whoever is signed in. Empty for everyone else."""
    return conversation_service.recent(current_user_id(), limit=limit)


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
