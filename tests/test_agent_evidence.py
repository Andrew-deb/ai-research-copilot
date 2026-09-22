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
    """Navigation and provenance are different things and never share a
    column."""
    js = _chat_js()
    assert "chat-rail" in js
    assert "sidebar" not in js


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
