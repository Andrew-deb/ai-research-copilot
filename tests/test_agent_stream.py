"""
tests/test_agent_stream.py — progress reporting for a turn (Phase 3.4).

A research turn takes the better part of a minute and used to send nothing at
all until it finished, which reads as a hang. /chat/ask now answers Server-Sent
Events when asked to, relaying the agent's real steps.

What matters here, and what these pin:

  * the events describe what actually happened — a step shown is a tool that
    ran, not a plausible-looking phase invented to fill the silence;
  * the JSON path still works unchanged, because the tests and any other caller
    that wants one answer depend on it;
  * a refusal is still a refusal. Capability, quota and validation are decided
    before a single byte of stream is committed to, since an SSE response has
    already sent 200 by the time it knows anything.
"""

import json
import time

import pytest

from services import agent_service

SSE = {"Accept": "text/event-stream", "X-Requested-With": "XMLHttpRequest"}
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _events(response):
    """Parse an SSE body into the list of payloads it carried."""
    out = []
    for frame in response.get_data(as_text=True).split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


@pytest.fixture
def wired(monkeypatch):
    """A connected agent whose turn emits a known sequence of events."""
    from services import mcp_client

    monkeypatch.setattr(agent_service, "is_connected", lambda: True)

    def fake_ask(question, *, tier, user_id=None, conversation_history=None,
                 on_event=None, usage=None):
        if on_event:
            on_event({"type": "status", "phase": "thinking"})
            on_event({"type": "tool_start", "name": "search_papers",
                      "arguments": {"query": "rag"}})
            on_event({"type": "tool_end", "name": "search_papers",
                      "ok": True, "found": 2})
            on_event({"type": "status", "phase": "writing"})
        return agent_service.envelope(
            question, answer="Answered [1].",
            citations=[{"number": 1, "paper_id": "p1", "title": "A Paper",
                        "publication_year": 2023, "venue": "arXiv",
                        "citation_count": 10, "similarity": 0.8}],
            sources=[{"number": 0, "paper_id": "p1", "title": "A Paper"}],
            tool_calls=[{"name": "search_papers", "arguments": {}, "ok": True,
                         "error": None}],
            llm_turns=2)

    monkeypatch.setattr(agent_service, "ask", fake_ask)
    return monkeypatch


# ---------------------------------------------------------------------------
# The stream
# ---------------------------------------------------------------------------

def test_the_stream_reports_the_steps_as_they_happen(anon_client, db, wired):
    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=SSE)

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["Content-Type"]

    kinds = [e["type"] for e in _events(resp)]
    assert kinds[0] == "status"           # sent before any work, so the page
    assert "tool_start" in kinds          # can leave its idle state at once
    assert "tool_end" in kinds
    assert kinds[-1] == "done"


def test_a_step_names_the_tool_and_what_it_was_asked(anon_client, db, wired):
    """
    The steps have to be real. A generic "Researching…" would have been easier
    and would have told the reader nothing they could check.
    """
    started = [e for e in _events(anon_client.post(
        "/chat/ask", json={"question": "why?"}, headers=SSE))
        if e["type"] == "tool_start"]

    assert started[0]["name"] == "search_papers"
    assert started[0]["arguments"]["query"] == "rag"


def test_the_final_event_carries_the_whole_envelope(anon_client, db, wired):
    done = [e for e in _events(anon_client.post(
        "/chat/ask", json={"question": "why?"}, headers=SSE))
        if e["type"] == "done"][0]

    result = done["result"]
    assert result["answer"] == "Answered [1]."
    assert result["citations"][0]["title"] == "A Paper"
    assert result["usage"]["llm_turns"] == 2


def test_stream_sends_keepalive_while_provider_is_quiet(
        anon_client, db, monkeypatch):
    """A quiet upstream call must not leave the SSE connection byte-silent."""
    from routes import chat as chat_route

    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(chat_route, "SSE_HEARTBEAT_SECONDS", 0.01)

    def slow_ask(question, *, tier, user_id=None, on_event=None, usage=None):
        time.sleep(0.04)
        return agent_service.envelope(question, answer="Eventually answered.")

    monkeypatch.setattr(agent_service, "ask", slow_ask)

    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=SSE)
    body = resp.get_data(as_text=True)

    assert ": keep-alive\n\n" in body
    assert '"type": "done"' in body


def test_buffering_is_disabled_on_the_response(anon_client, db, wired):
    """A proxy that buffers the body defeats the point: the page would sit
    silent and then receive everything at once, which is today's behaviour."""
    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=SSE)
    assert resp.headers.get("X-Accel-Buffering") == "no"
    assert "no-cache" in resp.headers.get("Cache-Control", "")


# ---------------------------------------------------------------------------
# The JSON path is untouched
# ---------------------------------------------------------------------------

def test_without_the_header_the_answer_is_plain_json(anon_client, db, wired):
    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json()["answer"] == "Answered [1]."


def test_both_paths_return_the_same_envelope(anon_client, db, wired, monkeypatch):
    """One turn, two deliveries. If they diverge, one of them is a second
    implementation nobody is maintaining."""
    # Two turns in one test, and the anonymous agent allowance is deliberately
    # tiny — without this the second call is refused and the comparison is
    # between an envelope and a 429.
    from services import quota_service
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 9, "rag_query": 9,
                                       "agent_query": 9},
                         "authenticated": {"semantic_search": 9, "rag_query": 9,
                                           "agent_query": 9}})
    streamed = [e for e in _events(anon_client.post(
        "/chat/ask", json={"question": "why?"}, headers=SSE))
        if e["type"] == "done"][0]["result"]
    plain = anon_client.post("/chat/ask", json={"question": "why?"},
                             headers=XHR).get_json()
    assert streamed == plain


# ---------------------------------------------------------------------------
# Refusals happen before the stream starts
# ---------------------------------------------------------------------------

def test_a_capability_refusal_is_still_a_403(anon_client, monkeypatch, wired):
    """
    An SSE response has already sent 200 by the time it knows anything, so every
    gate has to be decided first. A refusal delivered as a stream event would be
    a refusal the browser was told to treat as success.
    """
    from middleware import capabilities
    monkeypatch.setitem(capabilities.CAPABILITIES, "anonymous", frozenset())

    resp = anon_client.post("/chat/ask", json={"question": "hi"}, headers=SSE)
    assert resp.status_code == 403
    assert resp.is_json


def test_an_exhausted_allowance_is_still_a_429(anon_client, db, wired, monkeypatch):
    from services import quota_service
    monkeypatch.setattr(quota_service._policy, "_LIMITS",
                        {"anonymous": {"semantic_search": 9, "rag_query": 9,
                                       "agent_query": 1},
                         "authenticated": {"semantic_search": 9, "rag_query": 9,
                                           "agent_query": 9}})

    assert anon_client.post("/chat/ask", json={"question": "a"},
                            headers=SSE).status_code == 200
    resp = anon_client.post("/chat/ask", json={"question": "b"}, headers=SSE)
    assert resp.status_code == 429
    assert resp.is_json


def test_an_empty_question_is_still_a_400(anon_client, db, wired):
    resp = anon_client.post("/chat/ask", json={"question": "  "}, headers=SSE)
    assert resp.status_code == 400


def test_a_disconnected_agent_still_answers_503_json(anon_client, db, monkeypatch):
    monkeypatch.setattr(agent_service, "is_connected", lambda: False)
    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=SSE)
    assert resp.status_code == 503
    assert resp.is_json


# ---------------------------------------------------------------------------
# Failure mid-stream
# ---------------------------------------------------------------------------

def test_a_failure_mid_turn_is_reported_as_an_event(anon_client, db, monkeypatch):
    """
    Once the stream is open the status code is spent, so the only way left to
    say something went wrong is to say it in the stream.
    """
    from exceptions import ExternalAPIError

    monkeypatch.setattr(agent_service, "is_connected", lambda: True)

    def explode(question, *, tier, user_id=None, on_event=None, usage=None):
        if on_event:
            on_event({"type": "status", "phase": "thinking"})
        raise ExternalAPIError("The research service is not running right now.")

    monkeypatch.setattr(agent_service, "ask", explode)

    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=SSE)
    events = _events(resp)

    assert resp.status_code == 200          # already committed
    assert events[-1]["type"] == "error"
    assert "not running" in events[-1]["message"]


def test_the_turn_is_metered_even_when_it_fails(anon_client, db, monkeypatch):
    """The work was done and the capacity spent; a failure does not refund it,
    and calibration needs the row."""
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)

    def explode(question, *, tier, user_id=None, on_event=None, usage=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(agent_service, "ask", explode)
    anon_client.post("/chat/ask", json={"question": "why?"}, headers=SSE)

    rows = [r for r in db.ai_operations if r["metric"] == "agent_query"]
    assert rows and rows[-1]["ok"] is False


# ---------------------------------------------------------------------------
# Progress reporting must never break the turn
# ---------------------------------------------------------------------------

def test_a_failing_listener_does_not_fail_the_turn():
    """
    The turn behaves identically whether or not anyone is watching. A dropped
    progress event costs a line on screen; an exception would cost the answer.
    """
    def hostile(_event):
        raise RuntimeError("listener exploded")

    agent_service._emit(hostile, type="status", phase="thinking")   # must not raise


def test_no_listener_is_the_normal_case():
    agent_service._emit(None, type="status", phase="thinking")
