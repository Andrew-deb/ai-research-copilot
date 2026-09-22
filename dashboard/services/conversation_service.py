"""
dashboard/services/conversation_service.py — research chat history.

Deferred in Phase 3.3 on the grounds that the shape of a stored turn depends on
what an agent actually produces, and no agent existed. It does now, and the
envelope has settled, so this stores it whole.

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

from exceptions import CapabilityDeniedError, ValidationError
from repositories import lakebase

logger = logging.getLogger(__name__)

# A conversation is named after the question that started it. Long enough to
# tell two apart in a narrow sidebar, short enough not to wrap to three lines.
TITLE_CHARS = 60

RECENT_LIMIT = 12

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


def start(user_id: str | None, question: str, answer: str | None = None) -> dict:
    """Open a conversation, named from the answer where it named itself."""
    return lakebase.create_conversation(
        _require_owner(user_id), title_from(question, answer))


def record_turn(user_id: str | None, conversation_id: str | None,
                question: str, result: dict) -> str | None:
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
                # Someone else's id, or one that no longer exists. Start a new
                # conversation rather than refusing: the answer is already
                # written and the person is not at fault.
                conversation_id = None

        if not conversation_id:
            conversation_id = str(
                start(user_id, question, result.get("answer"))["conversation_id"])

        lakebase.append_message(conversation_id, "user", question)
        lakebase.append_message(
            conversation_id, "assistant", result.get("answer"),
            citations=result.get("citations"),
            sources=result.get("sources"),
            tool_calls=result.get("tool_calls"),
            usage=result.get("usage"),
        )
        return conversation_id
    except Exception:
        logger.exception("Could not record conversation turn")
        return None


def recent(user_id: str | None, limit: int = RECENT_LIMIT) -> list[dict]:
    """
    The sidebar list. Empty, never an error, for anyone without history.

    Called on every page render, so a database hiccup here would take down
    pages that have nothing to do with chat.
    """
    if not user_id:
        return []
    try:
        return [
            {"conversation_id": str(row["conversation_id"]),
             "title": row["title"],
             "pinned": bool(row.get("pinned"))}
            for row in lakebase.list_conversations(user_id, limit=limit)
        ]
    except Exception:
        logger.exception("Could not load recent conversations")
        return []


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

    messages = []
    for row in lakebase.get_conversation_messages(conversation_id):
        messages.append({
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
    }


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
