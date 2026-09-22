"""
dashboard/services/agent_service.py — the research agent and its boundary.

The contract was written first (3.3) and the loop behind it second (3.4): the
envelope is what the UI renders and what conversation storage will have to
persist, so deciding it after an agent existed would have meant changing a live
path and a schema at the same time.

**Writes are switched off** — see WRITE_TOOLS_ENABLED. The agent reads; it does
not yet save, create or modify anything.

Two behaviours here exist because of measurements against the live provider
rather than from reasoning about how models ought to work:

  * the answer is read from `reasoning` when `content` is empty;
  * `tools` are sent on *every* turn, including the final one. Sending an empty
    tool list makes this model emit raw `<tool_call>` XML as prose, so the
    instruction to stop searching travels as a message instead.

The trust boundary this sits on, from the approved design:

    browser  --session cookie-->  Render Flask  --Databricks OAuth M2M-->  MCP
             (application user)                 (service principal)

The agent runs server-side. The browser never holds a Databricks token, never
calls the MCP server and never learns its URL. That is why tool permission is
decided here, in the dashboard, against the caller's tier.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import time

import config
import llm_client
from exceptions import CapabilityDeniedError, ExternalAPIError, ValidationError
from middleware.capabilities import (
    LIBRARY_WRITE,
    NOTES_WRITE,
    PROGRESS_WRITE,
    tier_can,
)
from services import mcp_client

logger = logging.getLogger(__name__)

# Longest question accepted. The agent is the expensive capability, and an
# unbounded prompt is an unbounded bill.
MAX_QUESTION = 2000

# Envelope statuses.
STATUS_OK = "ok"
STATUS_NOT_CONNECTED = "not_connected"

# Held back from the deadline so there is always room for one final, tool-less
# turn. Sized from a measured run: turns against the free model took 8-15s, so
# 20 leaves room for one without pushing the total past the web server's own
# timeout.
SYNTHESIS_RESERVE_SECONDS = 20


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

# Phase 3.4 ships read-only, deliberately.
#
# Identity propagation is built and tested here, but it has not yet been proven
# end to end against the deployed server. Until it has, a fault in it would
# write a signed-in user's collections and notes onto the demo account, silently
# — which is exactly the failure the MCP server's default-user fallback was
# hiding. Nothing writes until the header is verified.
#
# This is a *phase* gate and is kept separate from TOOL_CAPABILITIES, which
# answers the different question of whether a tier is permitted at all. The two
# are checked independently for the same reason capability and quota are.
WRITE_TOOLS_ENABLED = False


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
             citations: list[dict] | None = None, sources: list[dict] | None = None,
             tool_calls: list[dict] | None = None,
             message: str | None = None, llm_turns: int = 0,
             embedding_calls: int = 0) -> dict:
    """
    One agent turn, in the shape every caller agrees on.

        status          ok | not_connected
        question        what was asked, trimmed
        answer          the prose, or None
        citations       papers the answer actually cites, numbered as it wrote them
        sources         every paper the search turned up, cited or not
        tool_calls      [{name, arguments, ok, error}]
        usage           {llm_turns, tool_calls, embedding_calls}
        message         why there is no answer, when there is none

    `citations` and `sources` are different claims and are kept apart. A citation
    is a paper the prose points at; a source is one the agent read. Collapsing
    them would either pad the citation list with papers the answer never used, or
    leave the panel empty whenever the model omits its mapping - and after a real
    search, showing nothing is its own kind of lie.

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
        "sources": sources or [],
        "tool_calls": calls,
        "usage": {
            "llm_turns": llm_turns,
            "tool_calls": len(calls),
            "embedding_calls": embedding_calls,
        },
        "message": message,
    }


def callable_tools(tier: str) -> tuple[str, ...]:
    """
    What the agent may actually invoke: tier permission AND the phase gate.

    `tools_for_tier` answers "is this tier allowed"; this also answers "is it
    switched on yet". Both have to pass.
    """
    allowed = tools_for_tier(tier)
    if not WRITE_TOOLS_ENABLED:
        allowed = tuple(n for n in allowed if TOOL_CAPABILITIES[n] is None)
    return allowed


def ensure_callable(tier: str, tool_name: str) -> None:
    """
    Raise unless this tool may run, for this tier, in this phase.

    Enforced on every invocation rather than trusted to the catalog the model
    was given. A model can always name a tool it was not offered, and "we did
    not tell it about that one" is not access control.
    """
    check_tool(tier, tool_name)
    if not WRITE_TOOLS_ENABLED and TOOL_CAPABILITIES[tool_name] is not None:
        raise CapabilityDeniedError(
            "The assistant cannot save or change anything yet — that arrives in "
            "a later release. Ask it to find or explain things instead.",
            capability=TOOL_CAPABILITIES[tool_name],
            # Logging in does not turn this on, so the UI must not suggest it.
            requires_auth=False,
        )


def is_connected() -> bool:
    """
    Whether there is actually an agent behind this boundary.

    Both halves are required: MCP for the tools and OpenRouter for the model.
    The route reads this to decide whether to spend the visitor's allowance —
    charging a daily question for a reply that says "not connected" would be
    taking payment for work that did not happen.
    """
    return mcp_client.is_configured() and llm_client.is_available()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_PROMPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "agent" / "system_prompt.md"
_prompt_cache: str | None = None

_FALLBACK_PROMPT = (
    "You are the AI Research & Learning Copilot, an academic research assistant. "
    "Answer only from what the tools return. Cite papers inline as [1], [2] in "
    "the order you first use them. If the tools return nothing relevant, say so "
    "plainly rather than answering from memory."
)


def _base_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is None:
        try:
            _prompt_cache = _PROMPT_PATH.read_text(encoding="utf-8")
        except OSError:
            # The prompt lives outside the dashboard directory, which is the
            # deploy root on Render. Rather than fail the turn, fall back to a
            # short prompt that still enforces the grounding contract.
            logger.warning("system_prompt.md not readable at %s — using fallback",
                           _PROMPT_PATH)
            _prompt_cache = _FALLBACK_PROMPT
    return _prompt_cache


def build_system_prompt(tier: str) -> str:
    """
    The base prompt plus the tools this caller may actually use.

    The file on disk documents all 13 tools. Handing that to a session where six
    of them are unavailable invites the model to attempt one, get refused, and
    spend a turn apologising — so the real catalog is appended and declared
    authoritative over the one above it.
    """
    available = callable_tools(tier)
    lines = "\n".join(f"- {name}" for name in available)
    return (
        f"{_base_prompt()}\n\n"
        "---\n\n"
        "## Tools available in THIS session\n\n"
        "This list overrides any tool catalog above it. Only these may be "
        "called; any other name will be refused:\n\n"
        f"{lines}\n\n"
        "You cannot currently save, create, modify or delete anything. If asked "
        "to, say so briefly and offer what you can do instead.\n\n"
        "## Citing — required\n\n"
        "After each statement you take from a paper, write a number in square "
        "brackets, like [1]. Number them however you like, in whatever order "
        "suits your answer — but every number must be defined.\n\n"
        "End your reply with a block listing what each number refers to, using "
        "the `paper_id` from the search results. Nothing may follow it:\n\n"
        "```citations\n"
        "1 = <paper_id>\n"
        "2 = <paper_id>\n"
        "```\n\n"
        "This applies to tables too: put the marker in the cell, next to the "
        "claim it supports. A comparison with no markers is an answer the "
        "reader cannot check.\n\n"
        "Do not write a heading called Citations, References or Sources — the "
        "interface renders the list. Write only the fenced block above, and "
        "nothing after it.\n\n"
        "A number missing from that block will be deleted from your answer, and "
        "the reader will never see the source. If you cite nothing, write no "
        "markers and no block.\n\n"
        "## What the reader already sees\n\n"
        "Each citation is displayed beside your answer with its **title, year, "
        "venue and citation count**. Do not repeat those four in your prose — "
        "the reader can see them, and restating them spends their attention on "
        "nothing.\n\n"
        "Author names and DOIs are *not* displayed, so name authors where it "
        "makes a sentence read naturally, and give a DOI only if asked for one. "
        "Otherwise: name the paper, say what it does, cite it.\n\n"
        "## Searching\n\n"
        "The corpus is a narrow slice of the literature, not all of it. Do not "
        "run the same search again with reworded terms: if a specific paper did "
        "not come back the first time it is not there, and hunting for it costs "
        "the reader their answer. Two searches is usually plenty. Answer from "
        "what you have and say plainly what the corpus does not cover."
    )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

# A call may not outlive the turn's budget. Checking the deadline only between
# turns left a hung provider running unbounded — measured at 775 seconds with
# zero completed turns, well past the web server's own 120s timeout.
MIN_CALL_SECONDS = 10


def _remaining(deadline: float) -> float:
    """What is left of the turn's budget, with a floor so a call is never
    given an impossible deadline just because time is nearly up."""
    return max(MIN_CALL_SECONDS, deadline - time.monotonic())


_TOOL_XML = re.compile(r"<tool_call>.*?(?:</tool_call>|$)", re.DOTALL)

# The model closes its answer with this, mapping each marker it wrote to the
# paper it meant.
_CITATION_BLOCK = re.compile(r"```citations\s*\n(.*?)(?:```|$)", re.DOTALL)
_MAP_LINE = re.compile(r"^\s*\[?(\d+)\]?\s*=\s*(\S+?)[\s,]*$", re.MULTILINE)
_MARKER = re.compile(r"\s*\[(\d+)\]")

# A heading the model writes above its mapping block. Stripping the block left
# the heading behind, so answers ended with the word "Citations" and nothing
# under it — which reads as a section that failed to load.
_TRAILING_HEADING = re.compile(
    r"\n#{1,6}\s*(citations?|references?|sources?)\s*:?\s*$", re.IGNORECASE)


def _drop_trailing_heading(text: str) -> str:
    """Remove a heading left hanging once its content was taken away."""
    previous = None
    while previous != text:
        previous = text
        text = _TRAILING_HEADING.sub("", text).rstrip()
    return text


def _strip_markers(text: str, keep: set[int]) -> str:
    """
    Remove citation markers that do not resolve to a paper.

    A marker with nothing behind it is worse than no marker: it promises a
    source, and the reader cannot tell it is broken without checking.
    """
    def replace(match):
        return match.group(0) if int(match.group(1)) in keep else ""

    cleaned = _MARKER.sub(replace, text)
    # Tidy the spacing the removal leaves behind.
    return re.sub(r" +([.,;:])", r"\1", cleaned).strip()


def resolve_citations(answer: str, found: list[dict]) -> tuple[str, list[dict]]:
    """
    Reconcile the markers in the prose with the papers they refer to.

    Numbering by discovery order and *telling* the model to use those numbers
    does not work: measured against the live model, it wrote "[2]" for the
    second paper it chose to discuss, while our citation 2 was a different paper
    entirely. The sidebar linked somewhere the sentence never meant. Prompting
    harder does not fix that — the model is numbering by its own narrative order
    and has no reason to think otherwise.

    So the model is required to close with an explicit mapping instead:

        ```citations
        1 = <paper_id>
        2 = <paper_id>
        ```

    and the citation list is rebuilt from that. Prose and sidebar then agree
    because they come from the same statement, rather than from two independent
    acts of counting.

    A missing or unusable mapping means no numbered citations at all, and the
    markers are stripped from the prose. Showing none is honest; showing wrong
    ones is not.
    """
    if not answer:
        return answer, []

    match = _CITATION_BLOCK.search(answer)
    if not match:
        return _drop_trailing_heading(_strip_markers(answer, keep=set())), []

    prose = _drop_trailing_heading(
        (answer[:match.start()] + answer[match.end():]).strip())

    by_paper = {c["paper_id"]: c for c in found}
    resolved: dict[int, dict] = {}
    for number, paper_id in _MAP_LINE.findall(match.group(1)):
        paper = by_paper.get(paper_id.strip().strip('"\'`'))
        if paper:
            resolved[int(number)] = paper

    # Only what the prose actually refers to. A paper the search turned up and
    # the answer never mentions is not a citation, it is a search result.
    cited = {int(n) for n in _MARKER.findall(prose)} & set(resolved)
    if not cited:
        return _drop_trailing_heading(_strip_markers(prose, keep=set())), []

    citations = [dict(resolved[n], number=n) for n in sorted(cited)]
    return _drop_trailing_heading(_strip_markers(prose, keep=cited)), citations


def _clean(text: str) -> str:
    """
    Strip tool-call syntax that leaked into the prose.

    A belt to the braces of keeping `tools` on every turn: this model emits
    `<tool_call>` blocks as text under conditions that are not fully pinned
    down, and XML rendered into a research answer is worse than a short answer.
    If stripping leaves nothing, the turn produced nothing — which the caller
    already knows how to say.
    """
    if not text or "<tool_call>" not in text:
        return text
    return _TOOL_XML.sub("", text).strip()


def _tool_schemas(tier: str) -> list[dict]:
    """OpenRouter function schemas for the tools this tier may call."""
    available = set(callable_tools(tier))
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in mcp_client.list_tools()
        if t["name"] in available
    ]


# What a tool result is trimmed to before the model sees it.
#
# Measured, not guessed: a ten-paper search came back as 105,477 characters of
# JSON, of which the model was shown the first 6,000 — 5.7%, containing zero
# complete papers and not even valid JSON. A single paper's `payload` field (the
# raw upstream API response, which nothing downstream reads) was 8,451
# characters on its own. The agent was not failing to use the evidence; it was
# never given any.
#
# Keeping the fields a research answer can actually be built from, and dropping
# the bookkeeping, takes the same ten papers to roughly a seventh of the size.
EVIDENCE_FIELDS = ("paper_id", "title", "publication_year", "venue",
                   "citation_count", "tldr")

# Abstracts are the substance, and also the second-largest field. Enough to
# judge and quote a paper by, not enough that ten of them crowd out the rest.
ABSTRACT_CHARS = 900

# Raised from 6,000 now that a result is a seventh of the size: ten trimmed
# papers fit comfortably, which is the entire point of the trimming.
MAX_TOOL_RESULT_CHARS = 14000


def _result_label(result) -> str | None:
    """
    A short name for what a tool call actually produced.

    Without it four consecutive `get_paper_details` calls all read "Reading a
    paper", which tells the reader the agent did something four times and
    nothing about what. The title is the one piece that distinguishes them.
    """
    row = result
    if isinstance(result, list):
        if len(result) != 1:
            return None
        row = result[0]
    if isinstance(row, dict):
        title = row.get("title") or row.get("topic")
        if title:
            return str(title)[:70]
    return None


def _evidence(result):
    """
    A tool result in the shape the model needs, and no larger.

    Anything paper-shaped keeps the fields an answer is built from; everything
    else is passed through untouched, because this cannot know what another
    tool's output means. Dropping `payload`, `authors`, `synced_at` and the
    relevance/fulltext bookkeeping is what turns "one truncated paper" into
    "ten whole ones".
    """
    def one(row):
        if not isinstance(row, dict) or not row.get("title"):
            return row
        lean = {k: row[k] for k in EVIDENCE_FIELDS if row.get(k) is not None}
        abstract = row.get("abstract")
        if abstract:
            text = str(abstract)
            lean["abstract"] = (text[:ABSTRACT_CHARS] + "…"
                                if len(text) > ABSTRACT_CHARS else text)
        return lean

    if isinstance(result, list):
        return [one(row) for row in result]
    if isinstance(result, dict) and "papers" in result and isinstance(result["papers"], list):
        # compare_papers returns {count, papers: [...]}
        return {**{k: v for k, v in result.items() if k != "papers"},
                "papers": [one(row) for row in result["papers"]]}
    return one(result)


def _collect_citations(result, found: list[dict], seen: set[str]) -> None:
    """
    Pull anything paper-shaped out of a tool result.

    Shape-based rather than tool-name-based: several tools return papers, and a
    list keyed on tool names would silently stop finding them the day another
    one is added.

    This builds the *candidate* set — everything the search turned up. Which of
    them end up cited, and under which numbers, is decided later by
    resolve_citations from the mapping the model supplies. An earlier version
    assigned numbers here and told the model to use them; it did not, and could
    not be made to.
    """
    rows = result if isinstance(result, list) else [result]
    for row in rows:
        if not isinstance(row, dict):
            continue
        paper_id = row.get("paper_id") or row.get("id")
        title = row.get("title")
        if not paper_id or not title or str(paper_id) in seen:
            continue

        seen.add(str(paper_id))
        found.append({
            "number": 0,          # assigned by resolve_citations
            "paper_id": str(paper_id),
            "title": title,
            "publication_year": row.get("publication_year") or row.get("year"),
            "venue": row.get("venue"),
            # Displayed beside the answer, which is what lets the prompt tell the
            # model not to repeat it. It used to be discarded here while the
            # prompt claimed the interface showed it — so the model kept writing
            # "cited 747 times" inline, correctly, because otherwise the number
            # vanished. The instruction was wrong, not the model.
            "citation_count": row.get("citation_count"),
            "similarity": row.get("similarity"),
        })
        # Deliberately not carried: influence_score (Semantic Scholar's
        # influentialCitationCount). It cannot be labelled in two words without
        # misleading — "influential" to whom? — and it is null for papers that
        # came from OpenAlex alone, so it would appear on some citations and not
        # others, inviting the reader to take its absence for zero.


def validate_question(question: str) -> str:
    """
    The cleaned question, or raise.

    Separate from `ask` because the streaming path has to decide this BEFORE it
    commits to a stream: an SSE response has already sent 200 by the time the
    turn begins, so a malformed question discovered inside the generator can
    only be reported as a stream event — telling the browser a bad request
    succeeded. Every gate is settled while a status code can still be chosen.
    """
    cleaned = (question or "").strip()
    if not cleaned:
        raise ValidationError("Question cannot be empty.")
    if len(cleaned) > MAX_QUESTION:
        raise ValidationError(f"Question is too long — {MAX_QUESTION} characters maximum.")
    return cleaned


def _emit(on_event, **payload) -> None:
    """
    Report progress, if anyone is listening.

    Optional because the turn must behave identically whether or not it is being
    watched: the JSON path passes nothing, the streaming path passes a queue.
    A failure to report is never allowed to fail the turn it is reporting on.
    """
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception:
        logger.debug("progress event dropped", exc_info=True)


def ask(question: str, *, tier: str, user_id: str | None = None,
        on_event=None) -> dict:
    """
    Answer one research question, running tools as needed.

    Two independent stopping conditions, because they fail differently: a call
    ceiling stops a model looping on itself, and a wall-clock deadline stops a
    slow provider running past the web server's own timeout and returning
    nothing at all. Whichever trips first ends the turn with whatever has been
    gathered, rather than raising — a partial answer with citations is worth
    more than an error page.
    """
    cleaned = validate_question(question)

    _emit(on_event, type="status", phase="thinking")

    if not is_connected():
        return envelope(
            cleaned,
            status=STATUS_NOT_CONNECTED,
            message=("The research assistant is not available right now. "
                     "Semantic search answers with citations in the meantime."),
        )

    # Inside the guard, not before it: fetching the schemas is itself a call to
    # the MCP server, so it fails exactly when the server is down — and a
    # dependency being unavailable must degrade the feature, not 500 the page.
    try:
        schemas = _tool_schemas(tier)
    except ExternalAPIError as exc:
        logger.error("Could not load tool schemas: %s", exc)
        return envelope(cleaned, status=STATUS_NOT_CONNECTED, message=str(exc))

    messages = [
        {"role": "system", "content": build_system_prompt(tier)},
        {"role": "user", "content": cleaned},
    ]

    found: list[dict] = []
    seen_papers: set[str] = set()
    tool_calls: list[dict] = []
    state = {"llm_turns": 0, "answer": None, "stopped": None}
    deadline = time.monotonic() + config.AGENT_DEADLINE_SECONDS

    def plan(call_tool) -> None:
        while True:
            # Both stopping conditions lead to the same place: one final turn
            # with no tools offered, so the model writes an answer from what it
            # already found.
            #
            # The deadline reserves room for that turn rather than firing at the
            # limit. Measured: a run that searched five times and then ran out
            # of clock returned 75 seconds of work, 23 papers, and an empty
            # answer. Stopping the search is not the same as giving up.
            out_of_time = time.monotonic() > deadline - SYNTHESIS_RESERVE_SECONDS
            out_of_calls = len(tool_calls) >= config.AGENT_MAX_TOOL_CALLS

            if out_of_time or out_of_calls:
                state["stopped"] = "deadline" if out_of_time else "tool_ceiling"
                # Tools are still offered on this turn, and the instruction to
                # stop goes in a message instead. Sending an empty tool list is
                # what makes this model emit raw <tool_call> XML as prose —
                # measured before the loop was written, and then walked into
                # anyway by the first version of this branch.
                messages.append({
                    "role": "user",
                    "content": ("Stop searching now and answer from the results you "
                                "already have. Do not call any more tools. Cite each "
                                "paper by its `citation` number."),
                })
                _emit(on_event, type="status", phase="writing")
                message = llm_client.chat_with_tools(
                    messages, schemas, timeout=_remaining(deadline),
                    max_tokens=config.AGENT_MAX_TOKENS)
                state["llm_turns"] += 1
                state["answer"] = _clean(llm_client.message_text(message))
                return

            message = llm_client.chat_with_tools(
                messages, schemas, timeout=_remaining(deadline),
                max_tokens=config.AGENT_MAX_TOKENS)
            state["llm_turns"] += 1
            requested = message.get("tool_calls") or []

            if not requested:
                _emit(on_event, type="status", phase="writing")
                state["answer"] = _clean(llm_client.message_text(message))
                return

            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": requested,
            })

            for call in requested:
                function = call.get("function") or {}
                name = function.get("name") or ""
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    arguments = {}

                record = {"name": name, "arguments": arguments, "ok": True, "error": None}
                _emit(on_event, type="tool_start", name=name, arguments=arguments)
                before = len(found)
                try:
                    ensure_callable(tier, name)
                    result = call_tool(name, arguments)
                    # Citations are collected from the FULL result, which still
                    # has venue and citation_count; the model is handed the
                    # trimmed one. The panel stays rich, the context stays small.
                    _collect_citations(result, found, seen_papers)
                    content = json.dumps(_evidence(result), default=str)[:MAX_TOOL_RESULT_CHARS]
                    _emit(on_event, type="tool_end", name=name, ok=True,
                          found=len(found) - before, label=_result_label(result))
                except CapabilityDeniedError as exc:
                    _emit(on_event, type="tool_end", name=name, ok=False, error=str(exc))
                    # Handed back to the model as a tool result, not raised: it
                    # should tell the person it cannot do that and carry on,
                    # rather than the whole turn dying on one refused call.
                    record.update(ok=False, error=str(exc))
                    content = json.dumps({"error": str(exc), "refused": True})
                except ExternalAPIError as exc:
                    _emit(on_event, type="tool_end", name=name, ok=False, error=str(exc))
                    record.update(ok=False, error=str(exc))
                    content = json.dumps({"error": str(exc)})

                tool_calls.append(record)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": content,
                })
            _emit(on_event, type="status", phase="reading")

    try:
        mcp_client.Turn(user_id).run(plan)
    except ExternalAPIError as exc:
        logger.error("Agent turn failed: %s", exc)
        return envelope(cleaned, status=STATUS_NOT_CONNECTED,
                        message=str(exc), llm_turns=state["llm_turns"],
                        tool_calls=tool_calls, citations=[], sources=found)

    # Prose and sidebar are reconciled here, from the mapping the model closed
    # with — not from two independent acts of counting.
    answer, citations = resolve_citations(state["answer"], found)

    message = None
    if not answer:
        # Only when the final synthesis turn also came back empty — a stopped
        # search that still produced prose is a complete answer, not a failure,
        # and saying otherwise over a perfectly good reply is worse than saying
        # nothing.
        message = {
            "deadline": "That took longer than the assistant is allowed to spend. "
                        "Try a narrower question.",
            "tool_ceiling": "The assistant reached its tool limit for one question. "
                            "Try asking for one thing at a time.",
        }.get(state["stopped"], "The assistant could not produce an answer.")

    return envelope(
        cleaned,
        status=STATUS_OK,
        answer=answer,
        citations=citations,
        sources=found,
        tool_calls=tool_calls,
        message=message,
        llm_turns=state["llm_turns"],
    )
