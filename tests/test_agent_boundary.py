"""
tests/test_agent_boundary.py — the dashboard/agent contract (Phase 3.3).

There is no agent yet. What is pinned here is the boundary it will arrive
behind, and the three things that are easy to get wrong once it does:

  1. the envelope shape, which the UI renders and conversation storage will
     have to persist;
  2. who may cause a *write* tool to run — the agent must not become a way
     around the capability layer;
  3. that telemetry can never fail the request it is measuring.
"""

import pytest

from exceptions import CapabilityDeniedError, ValidationError
from services import agent_service, telemetry_service

XHR = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------

def test_the_envelope_has_every_key_a_caller_depends_on():
    env = agent_service.envelope("why?")
    # `citations` and `sources` are separate claims: what the prose points at,
    # and what the agent read. See envelope()'s docstring.
    assert set(env) == {"status", "question", "answer", "citations", "sources",
                        "tool_calls", "usage", "message"}
    assert set(env["usage"]) == {"llm_turns", "tool_calls", "embedding_calls"}


def test_usage_counts_tool_calls_rather_than_trusting_a_caller():
    """
    Two sources for the same number is how they end up disagreeing, and this one
    is copied straight into ai_operations.
    """
    env = agent_service.envelope(
        "why?",
        tool_calls=[{"name": "search_papers", "arguments": {}, "ok": True, "error": None},
                    {"name": "compare_papers", "arguments": {}, "ok": True, "error": None}],
    )
    assert env["usage"]["tool_calls"] == 2


def test_the_usage_vocabulary_matches_the_telemetry_columns():
    """
    `usage` is copied into ai_operations rather than translated. A rename on one
    side that is not made on the other would silently stop being recorded.
    """
    import io, re, pathlib
    sql = pathlib.Path(__file__).resolve().parents[1] / "sql" / "11_ai_operations.sql"
    ddl = io.open(sql, encoding="utf-8").read()
    for column in agent_service.envelope("x")["usage"]:
        assert re.search(rf"^\s+{column}\s", ddl, re.M), column


def test_an_empty_question_is_a_validation_error():
    with pytest.raises(ValidationError):
        agent_service.ask("   ", tier="anonymous")


def test_an_enormous_question_is_refused():
    """The agent is the expensive capability; an unbounded prompt is a bill."""
    with pytest.raises(ValidationError):
        agent_service.ask("x" * (agent_service.MAX_QUESTION + 1), tier="anonymous")


# ---------------------------------------------------------------------------
# Which tools a tier may cause to run
# ---------------------------------------------------------------------------

def test_every_mcp_tool_has_a_decision_recorded():
    """
    Read the tool names off the MCP server itself. A tool added there without a
    row in TOOL_CAPABILITIES is a tool nobody decided the permissions for, and
    this is the test that notices.
    """
    import io, re, pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "mcp_server" / "research_mcp_server.py"
    text = io.open(src, encoding="utf-8").read()
    exposed = set(re.findall(r"@mcp\.tool\(\)\s*(?:\n[^\n]*)*?\ndef (\w+)", text))
    assert exposed, "found no MCP tools — the parse is wrong, not the server"
    assert exposed == set(agent_service.TOOL_CAPABILITIES)


def test_anonymous_may_use_every_read_tool():
    """A demo that cannot search or compare is a screenshot."""
    allowed = agent_service.tools_for_tier("anonymous")
    for tool in agent_service.READ_ONLY_TOOLS:
        assert tool in allowed, tool


def test_anonymous_may_use_no_write_tool():
    """
    The point of the table. An anonymous visitor holds AGENT_QUERY but no
    persistent capability, so "save this to a collection" must fail through the
    assistant exactly as the button does.
    """
    allowed = agent_service.tools_for_tier("anonymous")
    for tool in agent_service.WRITE_TOOLS:
        assert tool not in allowed, tool
        with pytest.raises(CapabilityDeniedError):
            agent_service.check_tool("anonymous", tool)


def test_signing_in_unlocks_the_write_tools():
    """The control: it is the tier that was refused, not the tool."""
    allowed = agent_service.tools_for_tier("authenticated")
    for tool in agent_service.WRITE_TOOLS:
        assert tool in allowed, tool
        agent_service.check_tool("authenticated", tool)


def test_a_refused_write_tool_points_at_logging_in():
    with pytest.raises(CapabilityDeniedError) as excinfo:
        agent_service.check_tool("anonymous", "save_note")
    assert excinfo.value.requires_auth is True


def test_an_unknown_tool_is_refused_rather_than_allowed():
    """
    Fail closed. A name the table does not recognise is a typo or something
    added to the MCP server without a permissions decision; neither defaults to
    permitted. And signing in would not fix it, so it must not say so.
    """
    with pytest.raises(CapabilityDeniedError) as excinfo:
        agent_service.check_tool("authenticated", "drop_everything")
    assert excinfo.value.requires_auth is False


def test_there_are_writes_to_protect():
    """Guards the guard: if this ever reads zero, the tests above prove nothing."""
    assert len(agent_service.WRITE_TOOLS) >= 6


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

def test_the_endpoint_exists_and_answers_json(anon_client):
    resp = anon_client.post("/chat/ask", json={"question": "what is RAG?"},
                            headers=XHR)
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == agent_service.STATUS_NOT_CONNECTED
    assert body["question"] == "what is RAG?"
    assert body["answer"] is None
    assert body["message"]


def test_the_endpoint_refuses_a_tier_without_the_capability(anon_client, monkeypatch):
    """Capability is checked whether or not an agent is connected."""
    from middleware import capabilities
    monkeypatch.setitem(capabilities.CAPABILITIES, "anonymous", frozenset())

    resp = anon_client.post("/chat/ask", json={"question": "hi"}, headers=XHR)
    assert resp.status_code == 403
    assert resp.get_json()["requires_auth"] is True


def test_an_unconnected_agent_spends_no_allowance(anon_client, db):
    """
    Charging a daily question for "not connected" takes payment for work that
    did not happen — and on a free tier this small, the next attempt would be
    refused until tomorrow.
    """
    anon_client.post("/chat/ask", json={"question": "hi"}, headers=XHR)
    anon_client.post("/chat/ask", json={"question": "hi again"}, headers=XHR)
    spent = [n for (_s, _sid, metric), n in db.usage.items() if metric == "agent_query"]
    assert spent == []   # neither the visitor's counter nor the global one


def test_a_malformed_question_is_still_a_400(anon_client):
    """
    Validation runs before the unavailability, so a broken request is not masked
    by the outage sitting behind it.
    """
    resp = anon_client.post("/chat/ask", json={"question": "  "}, headers=XHR)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Once an agent *is* connected — proving the wiring without one
# ---------------------------------------------------------------------------

@pytest.fixture
def connected(monkeypatch):
    """Phase 3.4, simulated: a connected agent that answers trivially."""
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(agent_service, "ask", lambda question, **kw: agent_service.envelope(
        question.strip(), answer="Because [1].", llm_turns=2, embedding_calls=1,
        citations=[{"number": 1, "paper_id": "p1", "title": "A Paper",
                    "publication_year": 2024, "venue": None, "similarity": 0.8}],
        tool_calls=[{"name": "search_papers", "arguments": {}, "ok": True, "error": None}],
    ))


def test_a_connected_agent_consumes_allowance(anon_client, db, connected):
    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    # Scoped to the visitor: one request also increments the separate global
    # ceiling counter, which is a different row for a different purpose.
    spent = [n for (scope, _sid, metric), n in db.usage.items()
             if scope == "anon" and metric == "agent_query"]
    assert spent == [1]


def test_a_connected_agent_counts_against_the_global_ceiling_too(
        anon_client, db, connected):
    """Per-visitor limits shape usage; the global ceiling protects capacity.
    Clearing cookies resets the first and cannot touch the second."""
    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    scopes = {scope for (scope, _sid, metric) in db.usage if metric == "agent_query"}
    assert scopes == {"anon", "global"}


def test_a_connected_agent_is_refused_once_the_allowance_is_gone(
        anon_client, db, connected, monkeypatch):
    from services import quota_service
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 9, "rag_query": 9, "agent_query": 1},
                         "authenticated": {"semantic_search": 9, "rag_query": 9, "agent_query": 9}})

    assert anon_client.post("/chat/ask", json={"question": "a"}, headers=XHR).status_code == 200
    resp = anon_client.post("/chat/ask", json={"question": "b"}, headers=XHR)
    assert resp.status_code == 429


def test_a_connected_agent_is_measured(anon_client, db, connected):
    """What 3.6 will read. The counts come from the envelope, not from a guess."""
    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    rows = [r for r in db.ai_operations if r["metric"] == "agent_query"]
    assert len(rows) == 1
    assert rows[0]["llm_turns"] == 2
    assert rows[0]["tool_calls"] == 1
    assert rows[0]["embedding_calls"] == 1
    assert rows[0]["ok"] is True
    assert rows[0]["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# Telemetry must never break what it measures
# ---------------------------------------------------------------------------

def test_a_telemetry_failure_does_not_fail_the_request(anon_client, db, monkeypatch):
    """
    A dropped measurement costs a row in a calibration sample. An exception
    raised from telemetry would cost the visitor their answer, after their
    allowance had already been spent on it.
    """
    from repositories import lakebase

    def boom(**_):
        raise RuntimeError("ai_operations does not exist")

    monkeypatch.setattr(lakebase, "record_ai_operation", boom)
    resp = anon_client.get("/search/semantic?q=attention")
    assert resp.status_code == 200


def test_a_failed_operation_is_still_recorded(db):
    """
    Failures are part of the cost: a request that dies after twenty seconds
    consumed twenty seconds of capacity and belongs in the distribution.
    """
    with pytest.raises(RuntimeError):
        with telemetry_service.measure("agent_query", "anonymous"):
            raise RuntimeError("provider exploded")

    row = db.ai_operations[-1]
    assert row["ok"] is False
    assert "provider exploded" in row["error"]


def test_the_recorded_error_carries_no_question_text(db):
    """The row is a cost record, not a crash report."""
    with pytest.raises(ValidationError):
        with telemetry_service.measure("agent_query", "anonymous"):
            raise ValidationError("Question cannot be empty.")
    assert db.ai_operations[-1]["error"].startswith("ValidationError:")


# ---------------------------------------------------------------------------
# The cheap baselines 3.6 compares agent runs against
# ---------------------------------------------------------------------------

def test_semantic_search_is_measured(anon_client, db):
    anon_client.get("/search/semantic?q=attention")
    rows = [r for r in db.ai_operations if r["metric"] == "semantic_search"]
    assert len(rows) == 1
    assert rows[0]["embedding_calls"] == 1
    assert rows[0]["llm_turns"] == 0


def test_a_rag_answer_is_measured(anon_client, db):
    anon_client.post("/search/ask", json={"question": "why?"}, headers=XHR)
    rows = [r for r in db.ai_operations if r["metric"] == "rag_query"]
    assert len(rows) == 1
    assert rows[0]["llm_turns"] == 1


def test_the_measured_tier_is_recorded(client, db):
    """Calibration splits by tier — anonymous and authenticated cost differently."""
    client.get("/search/semantic?q=attention")
    assert db.ai_operations[-1]["tier"] == "authenticated"


# ---------------------------------------------------------------------------
# The composer's styles travel with it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/chat"])
def test_every_page_with_the_composer_loads_its_stylesheet(anon_client, path):
    """
    Regression guard, and the second time this class of bug has shipped: a
    shared partial carries its markup but not its CSS, so the page that
    included it rendered the thread as unstyled text. Whatever runs the
    composer has to load chat.css.
    """
    body = anon_client.get(path).get_data(as_text=True)
    assert 'id="chat-composer"' in body
    assert "css/chat.css" in body


def test_a_refusal_carries_something_the_page_can_render(anon_client):
    """
    The explanation lives in `message`, and the fetch helper used to read only
    detail/error - so the page showed "Request failed (503)" while the real
    reason sat unread in the body.
    """
    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    body = resp.get_json()
    assert resp.status_code == 503
    assert body["message"]
    assert body["status"]          # what tells chat.js to render the envelope


def test_the_fetch_helper_reads_message_too():
    """Pins the contract between the envelope and main.js's error path."""
    import io, pathlib
    js = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static" / "js" / "main.js"
    source = io.open(js, encoding="utf-8").read()
    assert "body.message" in source
    assert "err.body = body" in source


# ---------------------------------------------------------------------------
# The layout switch when a conversation starts
# ---------------------------------------------------------------------------

def _chat_css() -> str:
    import io, pathlib
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "dashboard" / "static" / "css" / "chat.css")
    return io.open(path, encoding="utf-8").read()


def test_the_layout_switch_hides_nothing_imaginary(anon_client):
    """
    Every class the conversation state hides must actually be rendered by one of
    the two composer pages. A hidden selector that matches nothing is a rule
    that silently stopped working — which is how the introduction came to sit
    above a growing thread in the first place.
    """
    import re

    block = re.search(r"\.has-conversation .*?\{ display: none; \}", _chat_css(), re.S)
    assert block, "the conversation layout switch is gone"
    classes = re.findall(r"\.has-conversation \.([a-z-]+)", block.group(0))
    assert classes

    pages = (anon_client.get("/").get_data(as_text=True)
             + anon_client.get("/chat").get_data(as_text=True))
    for name in classes:
        assert name in pages, f"{name} is hidden but never rendered"


def test_both_pages_give_the_thread_somewhere_to_take_height(anon_client):
    """
    chat.js inserts the thread as a SIBLING of the composer's stage, not beside
    the form. Inserting it next to the form put it inside .chat-stage, where
    nothing gave it height and each reply pushed the page apart instead.
    """
    chat = anon_client.get("/chat").get_data(as_text=True)
    landing = anon_client.get("/").get_data(as_text=True)
    assert 'class="chat-stage"' in chat        # closest() finds this
    assert 'class="chat-page' in chat          # the flex column it sits in
    assert 'class="landing-main"' in landing   # no stage here; the form is direct
    assert 'class="chat-stage"' not in landing


def test_the_landing_page_adopts_the_chat_geometry(anon_client):
    """
    The hero is a centred stack that grows with the document — the behaviour
    being corrected. Once a conversation exists it has to become the same
    fixed-height column /chat uses, or the composer keeps sinking.
    """
    css = _chat_css()
    assert ".landing-main.has-conversation" in css
    assert "min-height" in css.split(".landing-main.has-conversation")[1][:300]
