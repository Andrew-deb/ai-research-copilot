"""
dashboard/services/conversation_service.py — research chat history.

Deferred in Phase 3.3 on the grounds that the shape of a stored turn depends on
what an agent actually produces, and no agent existed. It does now, and the
envelope has settled, so this stores it whole.

One conversation ID is the durable chat session ID. Message parent links and a
selected leaf express versions inside that session; an extra sessions table
would duplicate the identity with no distinct lifecycle yet. The future
assistant panel opens the same conversation ID and keeps its original `origin`.

**Anonymous visitors have no history**, and that is a promise rather than an
oversight: the demo tier states that nothing is kept between visits, the
sidebar hides the history controls for them, and `conversations.user_id` is NOT
NULL so the database refuses to hold an unowned row. Three layers saying the
same thing, because a promise about data is only as good as its least careful
enforcement.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

from exceptions import CapabilityDeniedError, ValidationError
from repositories import lakebase, conversation_versions

logger = logging.getLogger(__name__)

# A conversation is named after the question that started it. Long enough to
# tell two apart in a narrow sidebar, short enough not to wrap to three lines.
TITLE_CHARS = 60

RECENT_LIMIT = 12

# Model context is deliberately smaller than stored history. A long-running
# research chat should not resend every old turn on every request; recent
# conversational continuity is useful, unbounded prompt growth is not.
AGENT_CONTEXT_MESSAGES = 6
AGENT_CONTEXT_CHARS = 12000

# Openers that say nothing about the subject. "Can you compare X and Y" and
# "Compare X and Y" are the same conversation, and only one of them reads well
# in a 240px sidebar.
_FILLER = re.compile(
    r"^(please\s+|can you\s+|could you\s+|would you\s+|i(?:'d| would) like to\s+"
    r"|i want to\s+|tell me\s+|help me\s+|show me\s+|give me\s+)+",
    re.IGNORECASE)

# The first markdown heading of an answer.
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Markdown that would otherwise show as punctuation in the sidebar.
_EMPHASIS = re.compile(r"[*_`]+")


def _clip(text: str) -> str:
    """
    Fit a label to the sidebar, cutting on a word where there is one.

    "Compare the main approaches to retrieval-augmen…" is worse than stopping a
    word earlier, but only if that does not throw most of the label away.
    """
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= TITLE_CHARS:
        return cleaned

    clipped = cleaned[:TITLE_CHARS]
    spaced = clipped.rsplit(" ", 1)[0]
    chosen = spaced if len(spaced) >= TITLE_CHARS * 0.6 else clipped
    return chosen.rstrip(" ,.;:-") + "…"


def _from_answer(answer: str | None) -> str | None:
    """
    The answer's own opening heading, if it wrote one.

    The model routinely starts with something like "# Comparing Main Approaches
    to Retrieval-Augmented Generation" — a title it has already written, for
    free, and better than anything derived from the question. Asking a model to
    summarise the question into a title would be a second API call on a tier
    that is rate-limited by the minute.
    """
    if not answer:
        return None
    # Only the first heading, and only near the top: a "## Summary" halfway
    # down names a section, not the conversation.
    match = _HEADING.search(answer[:600])
    if not match:
        return None
    heading = _EMPHASIS.sub("", match.group(1)).strip()
    return heading or None


def _from_question(question: str) -> str:
    """The question, tidied into something that reads as a label."""
    cleaned = " ".join((question or "").split())
    cleaned = _FILLER.sub("", cleaned).strip()
    cleaned = cleaned.rstrip("?！!.,; ")
    if not cleaned:
        return ""
    # Only the first character, so acronyms keep their case: "RAG" must not
    # become "Rag".
    return cleaned[0].upper() + cleaned[1:]


def title_from(question: str, answer: str | None = None) -> str:
    """
    A readable name for a conversation.

    Prefers the heading the answer already wrote, falls back to the question
    with its filler removed. Never an API call: titles are not worth a request
    on a tier that rate-limits by the minute.
    """
    return _clip(_from_answer(answer) or _from_question(question)) or "New conversation"


def _require_owner(user_id: str | None) -> str:
    if not user_id:
        raise CapabilityDeniedError(
            "Log in to keep your research conversations.",
            capability="chat:history",
            requires_auth=True,
        )
    return user_id


def start(user_id: str | None, question: str, answer: str | None = None,
          *, origin: str = "agent", origin_context: str | None = None) -> dict:
    """Open a conversation, named from the answer where it named itself."""
    return lakebase.create_conversation(
        _require_owner(user_id), title_from(question, answer), origin, origin_context)


def record_turn(user_id: str | None, conversation_id: str | None,
                question: str, result: dict, *, mode: str = "new",
                source_id: str | None = None,
                expected_head: str | None = None, origin: str = "agent",
                origin_context: str | None = None) -> str | None:
    """
    Persist one exchange, and return the conversation it belongs to.

    Never raises into the caller. A turn that answered correctly must not be
    reported as a failure because writing the history afterwards went wrong —
    the person has their answer either way, and losing the record is the
    smaller loss by a wide margin.

    Returns None when there is nothing to store: an anonymous visitor, or an
    answer that never arrived.
    """
    if not user_id or not result or not result.get("answer"):
        return None

    try:
        if conversation_id:
            existing = lakebase.get_conversation(user_id, conversation_id)
            if not existing:
                if mode != "new":
                    return None
                # Someone else's id, or one that no longer exists. Start a new
                # conversation rather than refusing: the answer is already
                # written and the person is not at fault.
                conversation_id = None

        if not conversation_id:
            conversation_id = str(
                start(user_id, question, result.get("answer"), origin=origin,
                      origin_context=origin_context)["conversation_id"])

        selected = conversation_versions.append(
            user_id, conversation_id, question, result,
            mode=mode, source_id=source_id, expected_head=expected_head)
        if selected is None:
            return None
        return conversation_id
    except Exception:
        logger.exception("Could not record conversation turn")
        return None


def recent(user_id: str | None, limit: int = RECENT_LIMIT, *, kind: str = "all",
           search: str = "", offset: int = 0) -> list[dict]:
    """
    The sidebar list. Empty, never an error, for anyone without history.

    Called on every page render, so a database hiccup here would take down
    pages that have nothing to do with chat.
    """
    if not user_id:
        return []
    if kind not in ("all", "agent", "assistant", "search"):
        raise ValidationError("Unknown history filter.")
    try:
        chats = [
            {"conversation_id": str(row["conversation_id"]),
             "title": row["title"],
             "pinned": bool(row.get("pinned")),
             "origin": row.get("origin") or "agent",
             "origin_context": row.get("origin_context"),
             "updated_at": row.get("updated_at")}
            for row in lakebase.list_conversations(
                user_id, limit=limit + offset,
                origin=kind if kind in ("agent", "assistant") else None,
                search=search)
        ] if kind != "search" else []
        entries = [dict(chat, kind="chat", url="/chat/" + chat["conversation_id"])
                   for chat in chats]
        if kind in ("all", "search"):
            entries += [{"kind": "search", "origin": "search", "title": row["query"],
                         "mode": row["mode"], "pinned": False,
                         "updated_at": row["updated_at"],
                         "url": "/search?" + urlencode({"q": row["query"], "mode": row["mode"]})}
                        for row in conversation_versions.searches(
                            user_id, search=search, limit=limit + offset)]
        entries.sort(key=lambda entry: (bool(entry["pinned"]), entry["updated_at"]),
                     reverse=True)
        return entries[offset:offset + limit]
    except Exception:
        logger.exception("Could not load recent conversations")
        return []


def record_search(user_id: str | None, query: str, mode: str) -> None:
    if not user_id or not query or mode not in ("keyword", "semantic"):
        return
    try:
        conversation_versions.record_search(user_id, query[:500], mode)
    except Exception:
        logger.exception("Could not record search history")


def load(user_id: str | None, conversation_id: str) -> dict | None:
    """
    A conversation and its messages, or None if it is not this user's.

    The messages come back in the envelope's own shape so the page replays them
    through exactly the same renderer a live turn uses. Rebuilding them into
    something else here is how a replayed answer drifts from a fresh one.
    """
    owner = _require_owner(user_id)
    conversation = lakebase.get_conversation(owner, conversation_id)
    if not conversation:
        return None

    all_rows = conversation_versions.messages(owner, conversation_id)
    selected = _path(all_rows or [], conversation.get("selected_message_id"))
    siblings = {}
    for row in all_rows or []:
        key = (str(row.get("parent_message_id")), row["role"])
        siblings.setdefault(key, []).append(str(row["message_id"]))
    messages = []
    for row in selected:
        versions = siblings[(str(row.get("parent_message_id")), row["role"])]
        messages.append({
            "message_id": str(row["message_id"]),
            "versions": versions,
            "role": row["role"],
            "content": row["content"],
            "citations": row.get("citations") or [],
            "sources": row.get("sources") or [],
            "tool_calls": row.get("tool_calls") or [],
            "usage": row.get("usage") or {},
        })

    return {
        "conversation_id": str(conversation["conversation_id"]),
        "title": conversation["title"],
        "messages": messages,
        "origin": conversation.get("origin") or "agent",
    }


def _path(rows: list[dict], leaf) -> list[dict]:
    """Walk the selected ancestry, never the abandoned tail of another branch."""
    by_id = {str(row["message_id"]): row for row in rows}
    path, seen = [], set()
    while leaf and str(leaf) in by_id and str(leaf) not in seen:
        seen.add(str(leaf))
        row = by_id[str(leaf)]
        path.append(row)
        leaf = row.get("parent_message_id")
    return list(reversed(path))


def prepare_turn(user_id: str | None, conversation_id: str | None,
                 mode: str = "new", source_id: str | None = None) -> dict:
    """Freeze the chosen context before a metered run begins."""
    if not user_id or not conversation_id:
        if mode != "new":
            raise ValidationError("Select a saved conversation to create a version.")
        return {"history": [], "expected_head": None}
    conversation = lakebase.get_conversation(user_id, conversation_id)
    if not conversation:
        if mode != "new":
            raise ValidationError("That conversation is unavailable.")
        return {"history": [], "expected_head": None}
    rows = conversation_versions.messages(user_id, conversation_id) or []
    path = _path(rows, conversation.get("selected_message_id"))
    original_question = None
    if mode != "new":
        if mode not in ("edit", "regenerate"):
            raise ValidationError("Unknown conversation action.")
        index = next((i for i, row in enumerate(path)
                      if str(row["message_id"]) == str(source_id) and row["role"] == "user"), None)
        if index is None:
            raise ValidationError("Select a prompt on the current version.")
        original_question = path[index]["content"]
        if mode == "regenerate":
            # A completed write is not a safe instruction to repeat by accident.
            from services.agent_service import WRITE_TOOLS
            answer = path[index + 1] if index + 1 < len(path) else None
            if answer and any(call.get("name") in WRITE_TOOLS
                              for call in answer.get("tool_calls") or []):
                raise ValidationError("This response performed a write and cannot be regenerated.")
        path = path[:index]
    return {"history": _limit_context(path),
            "original_question": original_question,
            "expected_head": str(conversation["selected_message_id"])
                             if conversation.get("selected_message_id") else None}


def _limit_context(rows: list[dict], max_messages=AGENT_CONTEXT_MESSAGES,
                   max_chars=AGENT_CONTEXT_CHARS) -> list[dict]:
    usable = [{"role": row["role"], "content": row.get("content") or ""}
              for row in rows if row.get("role") in ("user", "assistant") and row.get("content")]
    chosen, chars = [], 0
    for message in reversed(usable):
        remaining = max_chars - chars
        if remaining <= 0 or len(chosen) >= max_messages:
            break
        content = message["content"][-remaining:]
        chosen.append({"role": message["role"], "content": content})
        chars += len(content)
    return list(reversed(chosen))


def select_version(user_id: str | None, conversation_id: str, message_id: str) -> bool:
    return conversation_versions.select(_require_owner(user_id), conversation_id, message_id)


def agent_context(user_id: str | None, conversation_id: str | None,
                  max_messages: int = AGENT_CONTEXT_MESSAGES,
                  max_chars: int = AGENT_CONTEXT_CHARS) -> list[dict]:
    """
    Recent user/assistant text for the next model turn.

    This is not the replay envelope. Citations, sources, tool calls and usage
    stay in storage/UI; routine follow-ups such as "summarize that" need the
    words that were exchanged, not another copy of every metadata object.

    Ownership is checked before any message rows are read. A missing, stale or
    foreign conversation id simply supplies no context, matching record_turn's
    existing behaviour of starting a fresh conversation instead of leaking or
    refusing.
    """
    if not user_id or not conversation_id:
        return []

    try:
        conversation = lakebase.get_conversation(user_id, conversation_id)
        if not conversation:
            return []

        rows = conversation_versions.messages(user_id, conversation_id) or []
        return _limit_context(_path(rows, conversation.get("selected_message_id")),
                              max_messages, max_chars)
    except Exception:
        logger.exception("Could not load agent conversation context")
        return []


def rename(user_id: str | None, conversation_id: str, title: str) -> dict | None:
    """
    Retitle a conversation.

    A blank title is refused rather than stored: an unnamed row in the sidebar
    is unreachable by anything except its position, and the person who typed
    nothing almost certainly meant to cancel.
    """
    cleaned = " ".join((title or "").split())[:TITLE_CHARS]
    if not cleaned:
        raise ValidationError("A conversation needs a name.")
    return lakebase.rename_conversation(_require_owner(user_id), conversation_id, cleaned)


def set_pinned(user_id: str | None, conversation_id: str, pinned: bool) -> dict | None:
    """Pin a conversation to the top of the sidebar, or release it."""
    return lakebase.set_conversation_pinned(
        _require_owner(user_id), conversation_id, bool(pinned))


def delete(user_id: str | None, conversation_id: str) -> bool:
    """Remove a conversation. Scoped to its owner in the statement itself."""
    return lakebase.delete_conversation(_require_owner(user_id), conversation_id)
