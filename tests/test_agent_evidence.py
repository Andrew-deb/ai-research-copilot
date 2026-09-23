"""
tests/test_agent_evidence.py — what actually reaches the model, and the panel.

Written from a measurement, not a suspicion. A ten-paper search came back from
the MCP server as 105,477 characters of JSON; the loop handed the model the
first 6,000 of it. That is 5.7%, it contained zero complete papers, and it was
not valid JSON. The agent then cited one paper out of twenty-eight — which
looked like a reasoning failure and was a plumbing failure.

The biggest single field was `payload`, the raw upstream API response, at 8,451
characters for one paper. Nothing downstream reads it.
"""

import json
import pathlib

import pytest

from services import agent_service

PAPER = {
    "paper_id": "p1",
    "title": "A Survey of RAG",
    "publication_year": 2023,
    "venue": "arXiv",
    "citation_count": 747,
    "tldr": "A survey.",
    "abstract": "x" * 4000,
    # Everything below is bookkeeping the model has no use for.
    "payload": {"raw": "y" * 8000},
    "authors": [{"display_name": "Someone"} for _ in range(30)],
    "relevance_score": 0.8,
    "relevance_status": "accepted",
    "relevance_topic": "rag",
    "relevance_threshold": 0.4,
    "relevance_scored_at": "2026-01-01",
    "fulltext_status": "ok",
    "fulltext_attempts": 1,
    "fulltext_checked_at": "2026-01-01",
    "synced_at": "2026-01-01",
    "source_api": "openalex",
    "open_access_url": "https://example.test/paper.pdf",
    "openalex_id": "W123",
    "semantic_scholar_id": "S123",
    "doi": "10.0000/x",
    "influence_score": 27.0,
}


# ---------------------------------------------------------------------------
# Trimming a tool result to evidence
# ---------------------------------------------------------------------------

def test_the_raw_payload_never_reaches_the_model():
    """8,451 characters per paper of upstream API response that nothing reads."""
    lean = agent_service._evidence([PAPER])[0]
    assert "payload" not in lean
    assert "authors" not in lean


def test_the_bookkeeping_fields_are_dropped():
    lean = agent_service._evidence([PAPER])[0]
    for noise in ("relevance_score", "relevance_status", "fulltext_status",
                  "synced_at", "source_api", "open_access_url"):
        assert noise not in lean, noise


def test_what_an_answer_is_built_from_is_kept():
    lean = agent_service._evidence([PAPER])[0]
    for useful in ("paper_id", "title", "publication_year", "venue",
                   "citation_count", "tldr", "abstract"):
        assert useful in lean, useful


def test_the_abstract_is_trimmed_not_dropped():
    """It is the substance and the second-largest field. Enough to judge and
    quote by, not enough that ten of them crowd out the rest."""
    lean = agent_service._evidence([PAPER])[0]
    assert len(lean["abstract"]) <= agent_service.ABSTRACT_CHARS + 1
    assert lean["abstract"].startswith("xxx")


def test_ten_papers_now_fit_where_none_did():
    """
    The whole point. Before: 105k chars cut to 6k, zero complete papers, invalid
    JSON. After: every paper arrives whole and parseable.
    """
    payload = json.dumps(agent_service._evidence([PAPER] * 10), default=str)

    assert len(payload) < agent_service.MAX_TOOL_RESULT_CHARS
    assert len(json.loads(payload)) == 10          # and it still parses


def test_a_result_that_is_not_papers_is_left_alone():
    """This cannot know what another tool's output means, so it does not guess."""
    topic = {"topic": "RAG", "summary": "A technique.", "url": "https://x"}
    assert agent_service._evidence(topic) == topic


def test_compare_papers_keeps_its_shape():
    result = {"count": 2, "papers": [PAPER, PAPER]}
    lean = agent_service._evidence(result)
    assert lean["count"] == 2
    assert len(lean["papers"]) == 2
    assert "payload" not in lean["papers"][0]


def test_citations_are_collected_from_the_full_result(wired):
    """
    Trimming is for the model. The panel is built from the untrimmed result, so
    venue and citation count survive even though the model never sees them.
    """
    wired["tool_result"] = [PAPER]
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Answer [1].\n\n```citations\n1 = p1\n```"},
    ]
    citation = agent_service.ask("q", tier="anonymous")["citations"][0]
    assert citation["venue"] == "arXiv"
    assert citation["citation_count"] == 747


# ---------------------------------------------------------------------------
# get_paper_details: a DOI is not a UUID
# ---------------------------------------------------------------------------

def test_the_uuid_lookup_is_guarded_before_the_doi_fallback():
    """
    Regression guard for a live failure. `papers.paper_id` is a uuid column, so
    looking a DOI up against it made Postgres raise `invalid input syntax for
    type uuid` — an exception, not an empty result, so it escaped before either
    fallback could run. The chain read as "try three things" and was really
    "try one thing and crash".
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "mcp_server"
              / "services" / "discovery_service.py").read_text(encoding="utf-8")
    body = source.split("def get_paper_details(")[1].split("\ndef ")[0]

    guard = body.index("uuid.UUID(identifier)")
    by_uuid = body.index("lakebase.get_paper(identifier)")
    by_doi = body.index("get_paper_by_doi")

    assert guard < by_uuid < by_doi
    assert "if is_uuid:" in body


# ---------------------------------------------------------------------------
# A tool answering in text still has to be usable
# ---------------------------------------------------------------------------

def test_a_json_text_result_is_parsed_into_objects(monkeypatch):
    """
    get_paper_details answers as text rather than structured content, so the
    caller was handed a JSON *string* — nothing downstream could read a field
    out of it, and a paper fetched that way never became a source.
    """
    from services import mcp_client

    class FakeResult:
        isError = False
        structuredContent = None
        content = [type("Block", (), {"text": json.dumps({"paper_id": "p1",
                                                          "title": "A Paper"})})()]

    import asyncio
    parsed = asyncio.run(mcp_client._call.__wrapped__(None, "t", {})
                         if hasattr(mcp_client._call, "__wrapped__")
                         else _call_with(FakeResult()))
    assert parsed == {"paper_id": "p1", "title": "A Paper"}


async def _call_with(result):
    """Drive mcp_client._call with a stubbed session."""
    from services import mcp_client

    class Session:
        async def call_tool(self, name, arguments):
            return result

    return await mcp_client._call(Session(), "get_paper_details", {})


def test_plain_text_stays_text():
    """Not everything a tool returns is JSON, and mangling prose would be worse
    than leaving it alone."""
    import asyncio

    class FakeResult:
        isError = False
        structuredContent = None
        content = [type("Block", (), {"text": "Just a sentence."})()]

    assert asyncio.run(_call_with(FakeResult())) == "Just a sentence."


# ---------------------------------------------------------------------------
# The panel keeps the two claims apart
# ---------------------------------------------------------------------------

def _chat_js() -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "js" / "chat.js").read_text(encoding="utf-8")


def test_the_panel_separates_citations_from_sources_consulted():
    js = _chat_js()
    assert '"Citations"' in js
    assert '"Sources consulted"' in js
    # Consulted excludes anything cited, so a paper never appears twice.
    assert "citedIds[s.paper_id]" in js


def test_only_citations_are_numbered():
    """A number implies the answer points at it, and consulted sources are
    precisely the ones it does not."""
    js = _chat_js()
    consulted = js.split('"Sources consulted"')[1][:200]
    assert "false" in consulted


def test_the_panel_renders_the_metadata_we_carry():
    js = _chat_js()
    # A fixed window rather than brace-matching: what matters is which fields
    # the function reads off a citation, not where its body ends.
    meta = js.split("function citationMeta")[1][:600]
    for field in ("publication_year", "venue", "citation_count"):
        assert field in meta, field
    # Deliberately absent: it cannot be labelled in two words without
    # misleading, and it is null for OpenAlex-only papers.
    assert "influence_score" not in js


def test_the_rail_is_separate_from_the_app_sidebar():
    """
    Navigation and provenance are different things and never share a column.

    Asserted on where sources are *put*, not on whether the word "sidebar"
    appears anywhere in the file — the first version of this forbade the word
    and then failed on a legitimate function that adds a conversation to the
    nav list, which is a different concern entirely.
    """
    js = _chat_js()
    sources = js.split("function addSources")[1].split("function citationMeta")[0]
    assert "railBody()" in sources
    assert "sidebar" not in sources
    assert ".sidebar" not in js.split("function ensureRail")[1][:1200]


# ---------------------------------------------------------------------------
# shared fixture
# ---------------------------------------------------------------------------

def _tool_call(name, arguments="{}", call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


@pytest.fixture
def wired(monkeypatch):
    from services import mcp_client

    state = {"turns": [], "calls": [], "tool_result": [PAPER],
             "schemas_sent": [], "sent_content": []}

    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(mcp_client, "list_tools", lambda force=False: [
        {"name": "search_papers", "description": "Search.",
         "input_schema": {"type": "object", "properties": {}}}])

    class FakeTurn:
        def __init__(self, user_id=None):
            pass

        def run(self, plan):
            def call_tool(name, arguments):
                state["calls"].append((name, arguments))
                return state["tool_result"]
            return plan(call_tool)

    monkeypatch.setattr(mcp_client, "Turn", FakeTurn)

    def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if m.get("role") == "tool":
                state["sent_content"].append(m["content"])
        return state["turns"].pop(0) if state["turns"] else {"content": "Done."}

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", fake_chat)
    return state


def test_the_model_receives_the_trimmed_result(wired):
    """End to end: what lands in the tool message is the lean shape."""
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Answer."},
    ]
    agent_service.ask("q", tier="anonymous")

    sent = wired["sent_content"][-1]
    assert "payload" not in sent
    assert len(sent) < agent_service.MAX_TOOL_RESULT_CHARS
    assert json.loads(sent)          # parseable, which the old 6k cut was not


def test_a_tool_failure_keeps_its_own_message(monkeypatch):
    """
    Observed live: OpenAlex rate-limited a search, the error travelled up inside
    an anyio ExceptionGroup rather than as itself, and got relabelled "Could not
    reach the research service" — wrong, and the opposite of a lead.
    """
    from exceptions import ExternalAPIError
    from services import mcp_client

    tool_error = ExternalAPIError("Tool 'search_papers' failed: 429 Too Many Requests")
    wrapped = BaseExceptionGroup("unhandled errors in a TaskGroup", [tool_error])

    mapped = mcp_client._as_external(wrapped)
    assert "429" in str(mapped)
    assert "Could not reach" not in str(mapped)


def test_a_real_transport_failure_still_reads_as_one():
    """The control: an HTTP-level failure is not a tool's fault."""
    from services import mcp_client

    class FakeHTTP(Exception):
        pass

    wrapped = BaseExceptionGroup(
        "unhandled errors in a TaskGroup",
        [FakeHTTP("Server error '503 Service Unavailable' for url ...")])

    assert "not running" in str(mcp_client._as_external(wrapped))


# ---------------------------------------------------------------------------
# A heading with nothing under it
# ---------------------------------------------------------------------------

def test_a_citations_heading_is_removed_with_its_block():
    """
    The model writes a heading above its mapping block. Stripping the block left
    the heading behind, so answers ended with the word "Citations" and nothing
    under it — which reads as a section that failed to load.
    """
    answer = ("Findings here [1].\n\n## Citations\n\n"
              "```citations\n1 = aaa\n```")
    prose, citations = agent_service.resolve_citations(answer, FOUND_FOR_HEADINGS)

    assert "Citations" not in prose
    assert prose.endswith("[1].")
    assert len(citations) == 1


def test_a_dangling_heading_goes_even_with_no_block():
    """The worse case: a heading, no block, and so no citations either."""
    answer = "A comparison table.\n\n### References"
    prose, citations = agent_service.resolve_citations(answer, FOUND_FOR_HEADINGS)
    assert prose == "A comparison table."
    assert citations == []


def test_a_heading_in_the_middle_is_left_alone():
    """Only a TRAILING one is orphaned; a section with content under it is
    part of the answer."""
    answer = "## Sources\n\nWe used several.\n\nAnd then some more."
    prose, _ = agent_service.resolve_citations(answer, FOUND_FOR_HEADINGS)
    assert prose.startswith("## Sources")


def test_the_prompt_forbids_writing_that_heading():
    prompt = agent_service.build_system_prompt("anonymous")
    assert "Do not write a heading called Citations" in prompt
    # And says the requirement covers tables, which is where it was ignored.
    assert "tables too" in prompt


FOUND_FOR_HEADINGS = [
    {"number": 0, "paper_id": "aaa", "title": "A Survey",
     "publication_year": 2023, "venue": "arXiv", "citation_count": 1},
]


# ---------------------------------------------------------------------------
# Steps that can be told apart
# ---------------------------------------------------------------------------

def test_a_step_label_names_what_was_read():
    """
    Four consecutive get_paper_details calls all read "Reading a paper", which
    says the agent did something four times and nothing about what.
    """
    assert agent_service._result_label(
        {"paper_id": "p1", "title": "A Survey of RAG"}) == "A Survey of RAG"
    assert agent_service._result_label([{"title": "One Paper"}]) == "One Paper"


def test_a_many_row_result_gets_no_label():
    """A search returns ten papers; naming one of them would misrepresent it.
    The count already says what happened."""
    assert agent_service._result_label([{"title": "A"}, {"title": "B"}]) is None
    assert agent_service._result_label("some text") is None


def test_the_label_travels_on_the_tool_end_event(wired):
    events = []
    wired["tool_result"] = [dict(PAPER, title="Only One Paper")]
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("get_paper_details")]},
        {"content": "Answer."},
    ]
    agent_service.ask("q", tier="anonymous", on_event=events.append)

    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends and ends[0]["label"] == "Only One Paper"


# ---------------------------------------------------------------------------
# The rail opens and closes
# ---------------------------------------------------------------------------

def test_the_rail_can_be_collapsed_on_any_screen():
    js = _chat_js()
    css = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
           / "css" / "chat.css").read_text(encoding="utf-8")

    assert "setCollapsed" in js
    assert "rc-rail-collapsed" in js            # remembered per viewer
    assert ".chat-rail.is-collapsed" in css
    # Not hidden behind a breakpoint any more.
    desktop = css.split("@media (max-width: 1100px)")[0]
    assert ".chat-rail.is-collapsed { flex-basis: 44px; }" in desktop


def test_storage_failure_cannot_break_the_panel():
    """localStorage throws in a private window and returns nothing after a
    clear; a remembered preference is never worth a broken page."""
    js = _chat_js()
    collapse = js.split("function setCollapsed")[1][:800]
    assert "try {" in collapse and "catch" in collapse


def test_the_scrollbars_are_styled_down():
    css = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
           / "css" / "chat.css").read_text(encoding="utf-8")
    assert "scrollbar-width: thin" in css
    assert "::-webkit-scrollbar" in css


def test_the_thread_scrolls_rather_than_the_document():
    """
    A long answer used to grow the page and push the composer below the fold,
    so you had to scroll back down to ask the next question.
    """
    css = _chat_css()
    wrapper = css.split(".chat-with-rail {")[1].split("}")[0]
    # The height comes from the parent now, not from viewport arithmetic.
    assert "flex: 1" in wrapper

    # min-height: 0 is what lets the flex child shrink so the thread can scroll.
    column = css.split(".chat-with-rail > .chat-page,")[1].split("}")[0]
    assert "min-height: 0" in column


# ---------------------------------------------------------------------------
# Layout regressions seen on screen
# ---------------------------------------------------------------------------

def _chat_css() -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "chat.css").read_text(encoding="utf-8")


def test_the_conversation_does_not_shift_left_before_the_rail_exists():
    """
    The answer column is capped at its own measure, so in a 1180px flex row it
    sat against the left edge and left a hole where the rail would appear —
    the conversation visibly moved sideways on the first turn with sources.
    """
    wrapper = _chat_css().split(".chat-with-rail {")[1].split("}")[0]
    assert "justify-content: center" in wrapper


def test_nothing_in_the_conversation_scrolls_sideways():
    """One long token — an error message, an identifier — used to drag a
    horizontal bar across the whole interface."""
    thread = _chat_css().split(".chat-thread {")[1].split("}")[0]
    assert "overflow-x: hidden" in thread


def test_a_step_flows_as_text_rather_than_as_columns():
    """As a flex row the label and its count each became a column, and a UUID
    wrapped onto four lines beside a sprawling error message."""
    step = _chat_css().split("\n.chat-step {")[1].split("}")[0]
    assert "display: block" in step
    assert "overflow-wrap: anywhere" in step


def test_a_raw_identifier_is_never_shown_as_a_step_label():
    """A 36-character UUID tells the reader nothing. The title arrives on
    tool_end, which is the part worth waiting for."""
    js = _chat_js()
    assert "IDENTIFIER.test(id)" in js

    import re as _re
    pattern = _re.search(r"var IDENTIFIER = /(.+?)/i;", js).group(1)
    identifier = _re.compile(pattern, _re.I)
    assert identifier.search("411fc2c1-2296-4818-9a33-2d05f0951227")
    assert identifier.search("10.1609/aaai.v38i17.29936")
    # A real query must still be shown.
    assert not identifier.search("LLM agents tool use limitations")


def test_the_trace_is_lighter_than_an_answer():
    """It is a note about how the answer was produced, not a second answer."""
    trace = _chat_css().split("\n.chat-trace {")[1].split("}")[0]
    assert "border-left" in trace
    assert "background:" not in trace


# ---------------------------------------------------------------------------
# Mobile: the composer holds its place, the launcher can be moved
# ---------------------------------------------------------------------------

def _mobile_css() -> str:
    css = _chat_css()
    return css.split("@media (max-width: 1100px)")[1].split("@media (min-width: 1101px)")[0]


def test_the_composer_is_pinned_while_the_conversation_scrolls():
    """
    Otherwise a long answer carries the composer off the bottom of the screen
    and you have to scroll back down to ask the next question.
    """
    mobile = _mobile_css()
    stage = mobile.split(".chat-page.has-conversation .chat-stage,")[1].split("}")[0]
    assert "position: sticky" in stage
    assert "bottom: 0" in stage
    # Opaque, or the thread shows through the box being typed into.
    assert "background: var(--bg)" in stage


def test_the_pinned_composer_avoids_viewport_height_maths():
    """
    Mobile viewport height moves as the browser chrome hides and shows, so a
    calc() against it is wrong for part of every scroll. Sticky needs no height.
    """
    mobile = _mobile_css()
    stage = mobile.split(".chat-page.has-conversation .chat-stage,")[1].split("}")[0]
    assert "100vh" not in stage and "calc(" not in stage


def test_the_landing_composer_is_pinned_too():
    """It has no .chat-stage wrapper — its composer is a direct child."""
    assert ".landing-main.has-conversation > .composer" in _mobile_css()


def test_the_sources_button_can_be_dragged_out_of_the_way():
    js = _chat_js()
    assert "makeDraggable" in js
    drag = js.split("function makeDraggable")[1][:1800]
    assert "pointerdown" in drag and "pointermove" in drag and "pointerup" in drag
    assert "setPointerCapture" in drag


def test_a_tap_still_opens_the_drawer():
    """Past a threshold a press is a drag; below it, it is still a tap — or the
    button would be impossible to press."""
    js = _chat_js()
    drag = js.split("function makeDraggable")[1][:1800]
    assert "DRAG_THRESHOLD" in drag
    assert "if (!moved) { onTap(); }" in drag


def test_the_dragged_button_is_kept_on_screen():
    """A button parked against an edge would be half off-screen after a
    rotate."""
    js = _chat_js()
    drag = js.split("function makeDraggable")[1][:1800]
    assert "function clamp(" in drag
    assert "window.innerWidth" in drag and "window.innerHeight" in drag
    assert 'window.addEventListener("resize"' in drag


def test_the_position_is_not_remembered_between_visits():
    """
    It starts where it belongs. Moving it is a response to what is on screen
    now — a position saved from yesterday would be in the way of something else
    today.
    """
    js = _chat_js()
    drag = js.split("function makeDraggable")[1][:1800]
    assert "localStorage" not in drag


def test_the_browser_does_not_treat_the_drag_as_a_scroll():
    launcher = _chat_css().split(".chat-rail-launcher {")[1].split("}")[0]
    assert "touch-action: none" in launcher


# ---------------------------------------------------------------------------
# The column measures itself instead of guessing the viewport
# ---------------------------------------------------------------------------

def test_the_column_measures_itself_instead_of_guessing_the_viewport():
    """
    `calc(100vh - 176px)` had to guess the topbar, both content paddings and the
    footer. It guessed low, and the composer floated well above the bottom of
    the page. `.content-fill` already ends where the footer begins, so `flex: 1`
    is the exact remaining height.
    """
    base = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "base.css").read_text(encoding="utf-8")
    assert ".content-fill" in base
    fill = base.split(".content-fill {")[1].split("}")[0]
    assert "display: flex" in fill and "flex-direction: column" in fill

    desktop = _chat_css().split("@media (max-width: 1100px)")[0]
    wrapper = desktop.split(".chat-with-rail {")[1].split("}")[0]
    assert "flex: 1" in wrapper
    assert "100vh" not in wrapper


def test_no_viewport_arithmetic_survives_on_desktop():
    """
    The whole point: nothing on the desktop path guesses at chrome sizes.

    Comments are stripped first — the file still *describes* the old
    `calc(100vh - 176px)` in the note explaining why it went, and a test that
    cannot tell a rule from the prose about it fails for the wrong reason.
    """
    import re as _re

    desktop = _chat_css().split("@media (max-width: 1100px)")[0]
    declarations = _re.sub(r"/\*.*?\*/", "", desktop, flags=_re.S)
    assert "100vh" not in declarations


def test_both_composer_pages_ask_for_the_fill():
    templates = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "templates"
    for name in ("chat.html", "landing.html"):
        assert "content-fill" in (templates / name).read_text(encoding="utf-8"), name
    shell = (templates / "base.html").read_text(encoding="utf-8")
    assert "{% block content_class %}" in shell


def test_the_space_under_the_composer_is_given_to_the_thread():
    """
    Forty pixels of content padding below an already-pinned composer is a margin
    nobody reads, and the footer supplies its own directly underneath.
    """
    base = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "base.css").read_text(encoding="utf-8")
    assert ".content-fill { padding-bottom: 12px; }" in base

    tight = _chat_css().split("@media (min-width: 1101px)")[1]
    assert ".content-fill .chat-stage" in tight
    assert ".content-fill .chat-note" in tight


def test_the_tightening_is_desktop_only():
    """Mobile is sticky and already sits where it should."""
    base = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "base.css").read_text(encoding="utf-8")
    block = base.split(".content-fill { padding-bottom: 12px; }")[0]
    assert block.rstrip().endswith("@media (min-width: 1101px) {")


def test_the_shell_has_a_definite_height_on_conversation_pages():
    """
    The bug behind two failed attempts at this. `.app-shell` carries
    `min-height: 100vh`, which means its height is whatever its content needs —
    so every `flex: 1` below it was distributing free space in a box that grew.
    `.chat-thread`'s `overflow-y: auto` had no height to scroll within, the page
    grew instead, and the composer ended up below the fold.

    A cap on the shell is what turns all of those into real constraints.
    """
    base = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "base.css").read_text(encoding="utf-8")

    assert "body.app-fixed .app-shell { height: 100vh; min-height: 0; }" in base
    # And the cap has to survive the trip down: a flex item will not shrink
    # below its content without this, which would undo it one level lower.
    assert "body.app-fixed .main-col { min-height: 0; }" in base
    assert "body.app-fixed .content-fill" in base


def test_the_cap_is_scoped_to_the_pages_that_want_it():
    """Every other page keeps a shell that grows with its content."""
    base = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "base.css").read_text(encoding="utf-8")
    assert ".app-shell { display: flex; min-height: 100vh; }" in base   # unchanged default

    templates = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "templates"
    assert "{% block body_class %}" in (templates / "base.html").read_text(encoding="utf-8")
    for name in ("chat.html", "landing.html"):
        assert "app-fixed" in (templates / name).read_text(encoding="utf-8"), name


def test_the_cap_does_not_reach_mobile():
    """
    Mobile scrolls the document and sticks the composer; capping the shell there
    would fight the sticky positioning.
    """
    base = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
            / "css" / "base.css").read_text(encoding="utf-8")

    # The media block, from its opening brace to its matching close.
    start = base.index("@media (min-width: 1101px) {")
    depth, i = 0, start + len("@media (min-width: 1101px)")
    while True:
        if base[i] == "{":
            depth += 1
        elif base[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = base[start:i]

    assert "body.app-fixed .app-shell" in block, "the cap escaped its breakpoint"
