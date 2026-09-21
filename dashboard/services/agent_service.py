"""
dashboard/services/agent_service.py — the boundary between the dashboard and the
research agent (Phase 3.3).

**There is no agent behind this yet.** Phase 3.4 connects the MCP server and 3.5
deploys it. What exists now is the contract: the shape one turn returns, and the
rule about which tools a tier may cause to run. Both are written first on
purpose — the envelope is what the UI renders and what conversation storage will
have to persist, and deciding it after an agent exists means changing a live
path and a schema at the same time.

The trust boundary this sits on, from the approved design:

    browser  --session cookie-->  Render Flask  --Databricks OAuth M2M-->  MCP
             (application user)                 (service principal)

The agent runs server-side. The browser never holds a Databricks token, never
calls the MCP server and never learns its URL. That is why tool permission is
decided here, in the dashboard, against the caller's tier.
"""

from __future__ import annotations

from exceptions import CapabilityDeniedError, ValidationError
from middleware.capabilities import (
    LIBRARY_WRITE,
    NOTES_WRITE,
    PROGRESS_WRITE,
    tier_can,
)

# Longest question accepted. The agent is the expensive capability, and an
# unbounded prompt is an unbounded bill.
MAX_QUESTION = 2000

# Envelope statuses.
STATUS_OK = "ok"
STATUS_NOT_CONNECTED = "not_connected"


# ---------------------------------------------------------------------------
# Which tools a tier may cause to run
# ---------------------------------------------------------------------------

# Every tool the MCP server exposes, mapped to the capability it requires.
# `None` means read-only: anyone who may use the agent at all may cause it.
#
# The six writes are the point of this table. An anonymous visitor holds
# AGENT_QUERY but not LIBRARY_WRITE or NOTES_WRITE, so without a check here the
# agent would become a way around the capability layer: "save this to a
# collection" would succeed through the assistant while the button that does the
# same thing is disabled two inches away. Authorization has to be decided by the
# caller's tier, never by the model's choice of tool.
TOOL_CAPABILITIES: dict[str, str | None] = {
    # read
    "search_papers": None,
    "get_paper_details": None,
    "get_similar_papers": None,
    "compare_papers": None,
    "explain_topic": None,
    "list_collections": None,
    "get_collection_details": None,
    # write
    "create_collection": LIBRARY_WRITE,
    "add_paper_to_collection": LIBRARY_WRITE,
    "remove_paper_from_collection": LIBRARY_WRITE,
    "generate_reading_plan": LIBRARY_WRITE,
    "mark_paper_status": PROGRESS_WRITE,
    "save_note": NOTES_WRITE,
}

READ_ONLY_TOOLS = tuple(name for name, cap in TOOL_CAPABILITIES.items() if cap is None)
WRITE_TOOLS = tuple(name for name, cap in TOOL_CAPABILITIES.items() if cap is not None)


def tools_for_tier(tier: str) -> tuple[str, ...]:
    """
    The tools this tier may use, in table order.

    Offered to the model as its menu, so a tier that cannot save a note is never
    told the tool exists. That is a usability measure, not a security one -
    `check_tool` is the enforcement, because a model can always name a tool it
    was not offered.
    """
    return tuple(name for name, cap in TOOL_CAPABILITIES.items()
                 if cap is None or tier_can(tier, cap))


def check_tool(tier: str, tool_name: str) -> None:
    """
    Raise unless `tier` may cause `tool_name` to run.

    Unknown tools are refused rather than allowed: a name this table does not
    recognise is either a typo or something added to the MCP server without
    anyone deciding who may use it, and neither should default to permitted.
    """
    if tool_name not in TOOL_CAPABILITIES:
        raise CapabilityDeniedError(
            f"Unknown tool '{tool_name}'.",
            capability=None,
            # Signing in would not help, so the UI must not offer it as the fix.
            requires_auth=False,
        )

    capability = TOOL_CAPABILITIES[tool_name]
    if capability is not None and not tier_can(tier, capability):
        raise CapabilityDeniedError(
            "Log in to let the assistant save papers, notes and reading progress.",
            capability=capability,
            requires_auth=True,
        )


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------

def envelope(question: str, *, status: str = STATUS_OK, answer: str | None = None,
             citations: list[dict] | None = None, tool_calls: list[dict] | None = None,
             message: str | None = None, llm_turns: int = 0,
             embedding_calls: int = 0) -> dict:
    """
    One agent turn, in the shape every caller agrees on.

        status          ok | not_connected
        question        what was asked, trimmed
        answer          the prose, or None
        citations       [{number, paper_id, title, publication_year, venue, similarity}]
        tool_calls      [{name, arguments, ok, error}]
        usage           {llm_turns, tool_calls, embedding_calls}
        message         why there is no answer, when there is none

    `citations` deliberately reuses the shape `search_service.rag_answer` already
    returns for `sources`, so the page has one citation renderer rather than two
    that drift apart. `usage` is the same vocabulary as the `ai_operations`
    columns, so telemetry is a copy rather than a translation.
    """
    calls = tool_calls or []
    return {
        "status": status,
        "question": question,
        "answer": answer,
        "citations": citations or [],
        "tool_calls": calls,
        "usage": {
            "llm_turns": llm_turns,
            "tool_calls": len(calls),
            "embedding_calls": embedding_calls,
        },
        "message": message,
    }


def is_connected() -> bool:
    """
    Whether an agent is actually wired up behind this boundary.

    False until Phase 3.4. The route reads it to decide whether to spend the
    visitor's allowance: charging someone a daily question for a reply that says
    "not connected" would be taking payment for work that did not happen, and
    with the free tier as small as it is, the second attempt would be refused
    for the rest of the day.
    """
    return False


def ask(question: str, *, tier: str, user_id: str | None = None) -> dict:
    """
    Answer one research question.

    Validates and returns the not-connected envelope today. Phase 3.4 replaces
    the body, not the signature or the return shape.
    """
    cleaned = (question or "").strip()
    if not cleaned:
        raise ValidationError("Question cannot be empty.")
    if len(cleaned) > MAX_QUESTION:
        raise ValidationError(
            f"Question is too long — {MAX_QUESTION} characters maximum."
        )

    return envelope(
        cleaned,
        status=STATUS_NOT_CONNECTED,
        message=("The research assistant connects in the next release. "
                 "Semantic search answers with citations today."),
    )
