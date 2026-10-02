"""Chat routes for the full page and compact cross-page assistant.

Both surfaces share /chat/ask, orchestration, quota and capability gates,
conversation versions, and owner-scoped run cancellation. Panel context is
resolved server-side; its origin is recorded only for a new thread.
"""

import json
import logging
import queue
import threading
import uuid

from flask import (Blueprint, Response, abort, jsonify, redirect,
                   render_template, request, stream_with_context, url_for)

import llm_client
import suggestions
from exceptions import ResearchCopilotError
from middleware.auth import current_tier, current_user_id, current_quota_scope, require_user_id
from middleware.capabilities import AGENT_QUERY, consume_quota, require_capability, tier_can
from routes.helpers import form_or_json, wants_json
from services import (agent_service, assistant_context, collection_service,
                      conversation_service, goal_service, quota_service, telemetry_service)
from repositories import agent_runs

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


def _context_for_view(owner, conversation_id=None):
    # Deliberate panel expansion can carry unsaved selections. A GET only
    # previews them; persistence still requires the CSRF-protected POST/turn.
    if request.args.get('context_references') is not None:
        raw = request.args['context_references']
        if len(raw) > 1000:
            abort(400)
        try:
            refs = assistant_context.normalize(json.loads(raw))
        except (ValueError, TypeError):
            abort(400)
    elif conversation_id:
        refs = assistant_context.load(owner, conversation_id)
    else:
        kind = request.args.get("context_kind", "")
        hint = assistant_context.resolve(owner, kind, request.args.get("context_id", ""))
        refs = [assistant_context.legacy_reference(hint['kind'], hint['id'])] if hint else []
    return assistant_context.bundle(owner, refs)


@bp.get("/chat/assistant/assets")
@require_capability(AGENT_QUERY)
def assistant_assets():
    return jsonify(assistant_context.search(current_user_id(), request.args.get('q', ''),
                   request.args.get('kind') or None, request.args.get('cursor'), request.args.get('category')))


@bp.post("/chat/<conversation_id>/context")
@require_capability(AGENT_QUERY)
def set_context(conversation_id):
    payload = request.get_json(silent=True) or {}
    assistant_context.save(require_user_id(), conversation_id, payload.get('references'))
    return jsonify({'items': assistant_context.resolve_references(current_user_id(), payload['references'])})


@bp.get("/chat")
def new_chat():
    """
    A fresh conversation. The default landing spot for the signed-in agent.

    `?q=` prefills the composer, which is how the landing page hands a question
    over. Prefilling rather than submitting: the agent is not connected yet, and
    even once it is, arriving to find your question already running takes the
    decision away from whoever typed it.
    """
    context = _context_for_view(current_user_id())
    return render_template(
        "chat.html",
        conversation=None,
        messages=[],
        starters=suggestions.AGENT_STARTERS,
        initial_prompt=carried_prompt(),
        chat_mode="wick" if request.args.get("mode") == "wick" else "research",
        context=context,
    )


@bp.get("/chat/assistant")
def assistant_panel():
    """The compact surface uses the full chat's renderer and turn endpoint."""
    conversation_id = (request.args.get("conversation_id") or "").strip()
    stored = (conversation_service.load(current_user_id(), conversation_id)
              if conversation_id else None)
    if conversation_id and not stored:
        abort(404)
    context = _context_for_view(current_user_id(), conversation_id or None)
    return render_template("assistant_panel.html", context=context,
        conversation={"conversation_id": conversation_id} if stored else None,
        messages=stored["messages"] if stored else [], initial_prompt="", starters=[])


@bp.get("/chat/assistant/contexts")
def assistant_contexts():
    """Choices are scoped at the data layer; each chosen item is checked again per turn."""
    owner = current_user_id()
    collections = collection_service.collections_for(owner)
    choices = [
        {"kind": "collection", "id": str(row["collection_id"]), "label": row["name"]}
        for row in collections["own"] + collections["examples"]
    ]
    if owner:
        choices.extend(
            {"kind": "goal", "id": str(row["goal_id"]), "label": row["title"]}
            for row in goal_service.list_goals(owner)
        )
    return jsonify({"choices": choices})


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
    context = _context_for_view(current_user_id(), conversation_id)

    return render_template(
        "chat.html",
        conversation={"conversation_id": conversation_id, "title": stored["title"]},
        messages=stored["messages"],
        starters=suggestions.AGENT_STARTERS,
        initial_prompt="",
        chat_mode=("wick" if request.args.get("mode") == "wick" or
                   (not request.args.get("mode") and stored.get("origin") == "assistant")
                   else "research"),
        context=context,
    )


@bp.get("/chat/history")
def history():
    """Filter in storage before pagination, including entries beyond the sidebar."""
    owner = require_user_id()
    kind = request.args.get("kind", "all")
    query = (request.args.get("q") or "").strip()[:120]
    limit = min(max(request.args.get("limit", 12, type=int), 1), 50)
    offset = min(max(request.args.get("offset", 0, type=int), 0), 1000)
    return jsonify({"entries": conversation_service.recent(
        owner, limit=limit, kind=kind, search=query, offset=offset)})


@bp.post("/chat/<conversation_id>/versions/<uuid:message_id>/select")
def select_version(conversation_id, message_id):
    if not conversation_service.select_version(current_user_id(), conversation_id,
                                                str(message_id)):
        abort(404)
    return jsonify({"conversation_id": conversation_id, "selected": str(message_id)})


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
    payload = form_or_json("question", "conversation_id", "action", "source_message_id",
                           "surface", "context_kind", "context_id", "chat_mode", "context_references")
    question = agent_service.validate_question(payload.get("question") or "")
    conversation_id = (payload.get("conversation_id") or "").strip() or None
    tier = current_tier()
    user_id = current_user_id()
    mode = payload.get("action") or "new"
    source_id = payload.get("source_message_id") or ""
    surface = payload.get("surface") or "agent"
    if surface not in ("agent", "assistant"):
        abort(400)
    chat_mode = agent_service.validate_mode(payload.get("chat_mode") or
        ("wick" if surface == "assistant" else "research"))
    if surface == "assistant" and chat_mode != "wick":
        abort(400)
    references = []
    if chat_mode == "wick":
        if payload.get('context_references') is not None:
            references = assistant_context.normalize(payload['context_references'])
        elif payload.get('context_kind'):
            references = [assistant_context.legacy_reference(payload['context_kind'], payload.get('context_id') or '')]
        else:
            references = assistant_context.load(user_id, conversation_id)
    context = assistant_context.bundle(user_id, references)
    if context:
        previous = assistant_context.load(user_id, conversation_id)
        if any(not item['available'] and {'kind': item['kind'], 'id': item['id']} not in previous
               for item in context['items']):
            abort(400, description="A selected context item is unavailable or not accessible.")
    if not isinstance(mode, str) or not isinstance(source_id, str):
        abort(400)
    mode = mode.strip()
    source_id = source_id.strip() or None
    if mode not in ("new", "edit", "regenerate"):
        abort(400)
    if mode != "new" and (not user_id or not conversation_id or not source_id):
        abort(400)
    prepared = conversation_service.prepare_turn(user_id, conversation_id, mode, source_id)
    if mode == "regenerate" and question != prepared["original_question"]:
        abort(400, description="Regeneration must use the original prompt.")

    if not (agent_service.is_connected() if chat_mode == "research" else
            agent_service.is_connected(chat_mode)):
        # Validates first, so a malformed question is still a 400 rather than
        # being masked by the unavailability behind it.
        options = {"tier": tier, "user_id": user_id}
        if chat_mode != "research":
            options["mode"] = chat_mode
        result = agent_service.ask(question, **options)
        return jsonify(result), 503

    consume_quota(quota_service.AGENT_QUERY)

    # Give a signed-in panel turn its durable identity before the worker starts.
    # A page navigation can sever SSE while the run continues; the browser must
    # already know which conversation to reopen when the worker stores its answer.
    if chat_mode == "wick" and user_id and not conversation_id and mode == "new":
        conversation_id = str(conversation_service.start(
            user_id, question, origin="assistant",
            origin_context=context["label"] if context else None)["conversation_id"])

    if chat_mode == "wick" and user_id and conversation_id:
        assistant_context.save(user_id, conversation_id, references)

    if _wants_stream():
        run_id = str(uuid.uuid4())
        owner = current_quota_scope()
        agent_runs.create(run_id, owner)
        return Response(
            stream_with_context(_stream_turn(question, tier, user_id, conversation_id,
                                             run_id=run_id, owner=owner,
                                             prepared=prepared, mode=mode, source_id=source_id,
                                             context=context, surface=surface,
                                             chat_mode=chat_mode)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Proxies that buffer a response defeat the entire point of
                # streaming it: the page would sit silent and then receive
                # everything at once, which is what it does today.
                "X-Accel-Buffering": "no",
            },
        )

    result = _run_turn(question, tier, user_id, conversation_id,
                       prepared=prepared, context=context, chat_mode=chat_mode)
    return jsonify(_with_conversation(result, user_id, conversation_id, question,
                                     prepared=prepared, mode=mode, source_id=source_id,
                                     context=context, surface=surface,
                                     chat_mode=chat_mode))


@bp.post("/chat/runs/<uuid:run_id>/stop")
def stop_run(run_id):
    """Request a stop; only the run worker can confirm it finished stopping."""
    state = agent_runs.request_stop(str(run_id), current_quota_scope())
    if state is None:
        abort(404)
    return jsonify({"run_id": str(run_id), "state": state})


@bp.get("/chat/runs/<uuid:run_id>")
def run_status(run_id):
    state = agent_runs.status(str(run_id), current_quota_scope())
    if state is None:
        abort(404)
    return jsonify({"run_id": str(run_id), "state": state})


def _wants_stream() -> bool:
    """
    Streaming is opt-in by Accept header, on the same endpoint.

    A second URL would mean two routes to keep in step on capability, quota and
    validation; content negotiation keeps one set of gates. The JSON path stays
    for callers that want a single answer - the tests among them.
    """
    return "text/event-stream" in (request.headers.get("Accept") or "")


def _run_turn(question: str, tier: str, user_id: str | None,
              conversation_id: str | None = None, on_event=None,
              should_stop=None, prepared=None, context=None, chat_mode="research") -> dict:
    """
    One measured turn. Shared by the JSON and streaming paths.

    The tally is created here and filled by the model calls inside, so it is
    complete even when `ask` returns a not_connected envelope after spending a
    turn or two. Assigned before the `with` block closes, because that is where
    the row is written.
    """
    tally = llm_client.Usage()
    # `metric` stays agent_query for both modes — it is the quota key, and
    # splitting it would hand every account two separate agent allowances. The
    # mode rides alongside so the usage page can tell Research from Wick, which
    # cost very different amounts.
    with telemetry_service.measure(quota_service.AGENT_QUERY, tier, user_id,
                                   mode=chat_mode) as op:
        try:
            history = (prepared["history"] if prepared is not None else
                       conversation_service.agent_context(user_id, conversation_id))
            options = {"tier": tier, "user_id": user_id,
                       "conversation_history": history, "on_event": on_event,
                       "usage": tally}
            if chat_mode != "research":
                options["mode"] = chat_mode
            if context:
                options["page_context"] = context
            if should_stop:
                options["should_stop"] = should_stop
            try:
                result = agent_service.ask(question, **options)
            except agent_service.RunStopped:
                result = agent_service.envelope(
                    question, status=agent_service.STATUS_STOPPED,
                    message="Stopped. Work already completed may have used your allowance.")
            op.llm_turns = result["usage"]["llm_turns"]
            op.tool_calls = result["usage"]["tool_calls"]
            op.embedding_calls = result["usage"]["embedding_calls"]
            if result.get("status") == agent_service.STATUS_STOPPED:
                op.ok = False
                op.error = "stopped by user"
        finally:
            # In a finally: a turn that raises still burned tokens, and an
            # expensive failure is the one measurement 3.6 can least afford to
            # be missing when it sets a ceiling.
            op.spent(tally)
    return result


def _with_conversation(result: dict, user_id: str | None,
                       conversation_id: str | None, question: str, *,
                       prepared=None, mode: str = "new", source_id=None,
                       context=None, surface="agent", chat_mode="research") -> dict:
    """
    Persist the turn and tell the page where it landed.

    The id rides alongside the envelope rather than inside it: which
    conversation a turn belongs to is a routing concern, and agent_service has
    no business knowing about storage.
    """
    stored = conversation_service.record_turn(
        user_id, conversation_id, question, result, mode=mode, source_id=source_id,
        expected_head=prepared["expected_head"] if prepared else None,
        origin="assistant" if chat_mode == "wick" else surface,
        origin_context=context["label"] if context else None)
    return dict(result, conversation_id=stored)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _stream_turn(question: str, tier: str, user_id: str | None,
                 conversation_id: str | None = None, *, run_id=None, owner=None,
                 prepared=None, mode="new", source_id=None, context=None, surface="agent",
                 chat_mode="research"):
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
                question, tier, user_id, conversation_id, on_event=events.put,
                should_stop=(lambda: agent_runs.status(run_id, owner) == "stop_requested")
                if run_id else None, prepared=prepared, context=context,
                chat_mode=chat_mode)
            terminal = agent_runs.finish(run_id, owner) if run_id else "completed"
            if terminal == "stopped" or result.get("status") == agent_service.STATUS_STOPPED:
                result = dict(result, status=agent_service.STATUS_STOPPED,
                              message="Stopped. Work already completed may have used your allowance.")
            outcome["result"] = (_with_conversation(
                result, user_id, conversation_id, question, prepared=prepared,
                mode=mode, source_id=source_id, context=context, surface=surface,
                chat_mode=chat_mode)
                if result.get("answer")
                                 else result)
        except ResearchCopilotError as exc:
            terminal = agent_runs.finish(run_id, owner, failed=True) if run_id else "failed"
            if terminal == "stopped":
                outcome["result"] = agent_service.envelope(
                    question, status=agent_service.STATUS_STOPPED,
                    message="Stopped. Work already completed may have used your allowance.")
            else:
                outcome["error"] = str(exc)
        except Exception as exc:                      # noqa: BLE001
            terminal = agent_runs.finish(run_id, owner, failed=True) if run_id else "failed"
            logger.exception("Agent turn failed")
            if terminal == "stopped":
                outcome["result"] = agent_service.envelope(
                    question, status=agent_service.STATUS_STOPPED,
                    message="Stopped. Work already completed may have used your allowance.")
            else:
                outcome["error"] = "The research assistant failed on that question."
        finally:
            events.put(None)                          # sentinel: work is over

    worker = threading.Thread(target=work, daemon=True)
    worker.start()

    # Sent immediately so the page can switch out of its idle state without
    # waiting for the first real event, which may be seconds away.
    if surface == "assistant" and conversation_id:
        yield _sse({"type": "conversation", "conversation_id": conversation_id})
    if run_id:
        yield _sse({"type": "run", "run_id": run_id})
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
