"""
tests/test_agent_loop.py — the agent loop and its MCP connection (Phase 3.4).

No network. The LLM and the MCP transport are both faked, because what is worth
pinning here is the loop's own behaviour: that it terminates, that it cannot be
talked into calling a tool it may not call, that it reads the answer from
whichever field the provider used, and that identity travels separately from the
service principal's credentials.

The two provider quirks asserted below were measured against the live model
before the loop was written, not discovered afterwards:

  * the final answer arrives in `reasoning` when `content` is empty;
  * dropping `tools` on a follow-up turn makes the model emit raw <tool_call>
    XML as prose instead of answering.
"""

import pytest

from services import agent_service

XHR = {"X-Requested-With": "XMLHttpRequest"}

TOOL_SCHEMAS = [
    {"name": "search_papers", "description": "Search papers.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "save_note", "description": "Save a note.",
     "input_schema": {"type": "object", "properties": {"note_text": {"type": "string"}}}},
]

PAPER_ROWS = [
    {"paper_id": "p1", "title": "RAG for Knowledge-Intensive NLP",
     "publication_year": 2020, "venue": "NeurIPS", "similarity": 0.81},
    {"paper_id": "p2", "title": "Dense Passage Retrieval", "publication_year": 2020},
]


def _tool_call(name, arguments="{}", call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


@pytest.fixture
def wired(monkeypatch):
    """
    A connected agent with a fake MCP transport and a scripted model.

    `turns` is the sequence of assistant messages the model will return.
    `calls` records what actually reached the transport — which is how the
    permission tests prove a refusal *stopped* a call rather than merely
    reporting one after the fact.
    """
    from services import mcp_client

    state = {"turns": [], "calls": [], "tool_result": PAPER_ROWS,
             "schemas_sent": [], "user_id": "unset"}

    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(mcp_client, "list_tools", lambda force=False: TOOL_SCHEMAS)

    class FakeTurn:
        def __init__(self, user_id=None):
            state["user_id"] = user_id

        def run(self, plan):
            def call_tool(name, arguments):
                state["calls"].append((name, arguments))
                return state["tool_result"]
            return plan(call_tool)

    monkeypatch.setattr(mcp_client, "Turn", FakeTurn)

    def fake_chat(messages, tools, **kwargs):
        state["schemas_sent"].append([t["function"]["name"] for t in tools])
        state["last_messages"] = list(messages)
        return state["turns"].pop(0) if state["turns"] else {"content": "Done."}

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", fake_chat)
    return state


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

def test_a_connected_agent_answers_with_citations(wired):
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers", '{"query": "rag"}')]},
        {"content": "Grounding generation in retrieved text [1][2].\n\n```citations\n1 = p1\n2 = p2\n```"},
    ]
    result = agent_service.ask("what is RAG?", tier="anonymous")

    assert result["status"] == agent_service.STATUS_OK
    assert result["answer"] == "Grounding generation in retrieved text [1][2]."
    assert [c["number"] for c in result["citations"]] == [1, 2]
    assert result["citations"][0]["title"] == "RAG for Knowledge-Intensive NLP"
    assert result["usage"]["tool_calls"] == 1
    assert result["usage"]["llm_turns"] == 2


def test_citations_are_deduplicated_across_tool_calls(wired):
    """The same paper found twice is one citation, not two."""
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers", '{"query": "a"}', "c1")]},
        {"content": "", "tool_calls": [_tool_call("search_papers", '{"query": "b"}', "c2")]},
        {"content": "Answer [1][2].\n\n```citations\n1 = p1\n2 = p2\n```"},
    ]
    result = agent_service.ask("q", tier="anonymous")
    # Two searches returned the same two papers; each appears once.
    assert [c["paper_id"] for c in result["citations"]] == ["p1", "p2"]


def test_the_answer_is_read_from_reasoning_when_content_is_empty(wired):
    """
    Measured on the configured model, on the turn following a tool result:
    content=0 chars, reasoning=612. Reading only `content` renders a blank
    answer with no error raised anywhere, which looks like broken retrieval.
    """
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "", "reasoning": "Here are the results."},
    ]
    result = agent_service.ask("q", tier="anonymous")
    assert result["answer"] == "Here are the results."


def test_conversation_history_is_sent_before_the_new_question(wired):
    wired["turns"] = [{"content": "Short version."}]

    result = agent_service.ask(
        "Summarize your previous response",
        tier="authenticated",
        conversation_history=[
            {"role": "user", "content": "Explain RAG"},
            {"role": "assistant", "content": "RAG retrieves evidence before generation."},
        ],
    )

    assert result["answer"] == "Short version."
    assert wired["calls"] == []
    assert [m["role"] for m in wired["last_messages"]] == [
        "system", "user", "assistant", "user"
    ]
    assert wired["last_messages"][-2]["content"].startswith("RAG retrieves")
    assert wired["last_messages"][-1]["content"] == "Summarize your previous response"


def test_prompt_allows_language_work_without_tools():
    prompt = agent_service.build_system_prompt("authenticated")
    followups = prompt.split("## Conversation follow-ups")[1].split("## Citing")[0]

    assert "summarize" in followups
    assert "without calling a tool" in followups
    assert "new evidence" in followups
    assert "Do not re-run research" in followups


def test_tools_are_resent_on_every_turn(wired):
    """
    Dropping them once the model has its results looks like an optimisation and
    is not: measured, the model then emits raw <tool_call> XML as prose.
    """
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Answer."},
    ]
    agent_service.ask("q", tier="anonymous")
    assert all(sent for sent in wired["schemas_sent"]), wired["schemas_sent"]


def test_a_tool_result_is_appended_before_the_next_turn(wired):
    """Without the tool message the model answers the original question blind."""
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Answer."},
    ]
    agent_service.ask("q", tier="anonymous")
    roles = [m["role"] for m in wired["last_messages"]]
    assert roles == ["system", "user", "assistant", "tool"]


# ---------------------------------------------------------------------------
# What the agent may not do
# ---------------------------------------------------------------------------

def test_a_write_tool_is_never_invoked_this_phase(wired):
    """
    The refusal must stop the CALL, not merely be reported afterwards. 3.4 is
    read-only until identity propagation is proven against the live server.
    """
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("save_note", '{"note_text": "x"}')]},
        {"content": "I cannot save notes yet."},
    ]
    result = agent_service.ask("save this", tier="authenticated")

    assert wired["calls"] == []                      # nothing reached the transport
    assert result["tool_calls"][0]["ok"] is False
    assert "cannot save" in result["tool_calls"][0]["error"].lower()


def test_a_refused_tool_does_not_kill_the_turn(wired):
    """
    The refusal goes back as a tool result so the model can explain itself. One
    disallowed call should not cost the person their entire answer.
    """
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("save_note")]},
        {"content": "I cannot save notes, but here is what I found."},
    ]
    result = agent_service.ask("q", tier="authenticated")
    assert result["status"] == agent_service.STATUS_OK
    assert result["answer"].startswith("I cannot save notes")


def test_an_unknown_tool_is_refused_without_reaching_the_transport(wired):
    """Fail closed: a name nobody decided on does not default to permitted."""
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("drop_everything")]},
        {"content": "No such tool."},
    ]
    result = agent_service.ask("q", tier="authenticated")
    assert wired["calls"] == []
    assert result["tool_calls"][0]["ok"] is False


def test_write_tools_are_not_offered_to_the_model(wired):
    """Enforcement is ensure_callable; this is the usability half of it."""
    wired["turns"] = [{"content": "Answer."}]
    agent_service.ask("q", tier="authenticated")
    assert wired["schemas_sent"][0] == ["search_papers"]


def test_the_prompt_lists_only_the_callable_tools():
    """
    system_prompt.md documents all 13. Handing that to a session where six are
    unavailable invites the model to try one and spend a turn apologising.
    """
    prompt = agent_service.build_system_prompt("authenticated")
    session_section = prompt.split("Tools available in THIS session")[1]
    assert "search_papers" in session_section
    assert "save_note" not in session_section


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------

def test_the_tool_ceiling_stops_a_looping_model(wired, monkeypatch):
    monkeypatch.setattr(agent_service.config, "AGENT_MAX_TOOL_CALLS", 2)
    # Always asks for another tool — without a ceiling this never returns.
    wired["turns"] = [{"content": "", "tool_calls": [_tool_call("search_papers")]}
                      for _ in range(20)]
    result = agent_service.ask("q", tier="anonymous")
    assert result["usage"]["tool_calls"] == 2
    assert result["status"] == agent_service.STATUS_OK


def test_the_deadline_stops_a_slow_turn(wired, monkeypatch):
    """
    A ceiling counted in calls does not protect a timeout measured in seconds.
    Zero means the deadline has already passed.
    """
    monkeypatch.setattr(agent_service.config, "AGENT_DEADLINE_SECONDS", 0)
    wired["turns"] = [{"content": "", "tool_calls": [_tool_call("search_papers")]}]
    result = agent_service.ask("q", tier="anonymous")
    assert result["usage"]["tool_calls"] == 0
    assert "longer than" in result["message"]


def test_a_partial_turn_still_returns_what_it_found(wired, monkeypatch):
    """A partial answer with citations beats an error page."""
    monkeypatch.setattr(agent_service.config, "AGENT_MAX_TOOL_CALLS", 1)
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Partial answer [1].\n\n```citations\n1 = p1\n```"},
    ]
    result = agent_service.ask("q", tier="anonymous")
    assert result["citations"]
    assert result["answer"] == "Partial answer [1]."


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_the_acting_user_is_handed_to_the_transport(wired):
    """
    Service-principal credentials say which application is calling and nothing
    about which person. The user travels separately, or not at all.
    """
    wired["turns"] = [{"content": "Answer."}]
    agent_service.ask("q", tier="authenticated", user_id="user-123")
    assert wired["user_id"] == "user-123"


def test_an_anonymous_turn_sends_no_user(wired):
    wired["turns"] = [{"content": "Answer."}]
    agent_service.ask("q", tier="anonymous", user_id=None)
    assert wired["user_id"] is None


def test_the_header_name_matches_on_both_sides():
    """Two constants, one wire. They drift silently if nothing checks."""
    import io
    import pathlib
    import re

    from services import mcp_client

    src = (pathlib.Path(__file__).resolve().parents[1] / "mcp_server"
           / "middleware" / "identity_middleware.py")
    server_side = re.search(r'USER_ID_HEADER = "([^"]+)"',
                            io.open(src, encoding="utf-8").read()).group(1)
    assert mcp_client.USER_ID_HEADER == server_side


def test_a_write_without_identity_raises_rather_than_using_the_demo_account():
    """
    The silent fallback is the bug being fixed: a signed-in user's notes landing
    on the demo profile, with nothing raised and nothing logged to explain it.
    """
    import importlib.util
    import pathlib

    # Loaded by path, not by import: `middleware` already resolves to the
    # dashboard's package of that name, and the MCP server's is a different one
    # with the same name.
    path = (pathlib.Path(__file__).resolve().parents[1] / "mcp_server"
            / "middleware" / "request_context.py")
    spec = importlib.util.spec_from_file_location("mcp_request_context", path)
    request_context = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(request_context)

    request_context.clear_current_user()
    try:
        with pytest.raises(PermissionError):
            request_context.require_current_user_id()

        request_context.set_current_user_id("user-abc")
        assert request_context.require_current_user_id() == "user-abc"
    finally:
        request_context.clear_current_user()


# ---------------------------------------------------------------------------
# Through the endpoint
# ---------------------------------------------------------------------------

def test_the_endpoint_answers_200_once_connected(anon_client, db, wired):
    wired["turns"] = [{"content": "Answered."}]
    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    assert resp.status_code == 200
    assert resp.get_json()["answer"] == "Answered."


def test_a_connected_turn_is_metered_and_measured(anon_client, db, wired):
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Answered."},
    ]
    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)

    spent = [n for (scope, _sid, metric), n in db.usage.items()
             if scope == "anon" and metric == "agent_query"]
    assert spent == [1]

    row = [r for r in db.ai_operations if r["metric"] == "agent_query"][-1]
    assert row["llm_turns"] == 2
    assert row["tool_calls"] == 1
    assert row["ok"] is True


def test_the_suite_cannot_reach_the_real_mcp_server():
    """
    Meta-test, added after the suite briefly started calling the deployed server
    for real and failing when it returned 503. Nothing here should open a
    socket, and a developer's .env must not be able to change that.
    """
    from services import mcp_client
    assert not mcp_client.is_configured()


# ---------------------------------------------------------------------------
# When the MCP server is down
# ---------------------------------------------------------------------------
#
# Written against a real outage: the Databricks App stopped mid-development and
# a plain HTTP 503 arrived as `ExceptionGroup: unhandled errors in a TaskGroup`
# wrapping an HTTPStatusError, escaping /chat/ask as a 500 error page. An outage
# in a dependency has to degrade the feature, not break the page.

class _FakeHTTPError(Exception):
    pass


def _task_group_failure(status):
    """What anyio actually raises: the real cause buried in an ExceptionGroup."""
    inner = _FakeHTTPError(f"Server error '{status}' for url 'https://example/mcp'")
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])


def _failing_run(exc):
    """
    A stand-in for asyncio.run that fails the way the transport does.

    It closes the coroutine it was handed; leaving it un-awaited is harmless to
    the assertion but emits a RuntimeWarning, and a suite that prints warnings
    it does not mean trains everyone to ignore the ones it does.
    """
    def run(coro=None, *args, **kwargs):
        if hasattr(coro, "close"):
            coro.close()
        raise exc
    return run


def test_a_transport_failure_becomes_a_domain_error(monkeypatch):
    from exceptions import ExternalAPIError
    from services import mcp_client

    monkeypatch.setattr(mcp_client, "is_configured", lambda: True)
    monkeypatch.setattr(mcp_client.asyncio, "run", _failing_run(_task_group_failure("503")))

    with pytest.raises(ExternalAPIError) as excinfo:
        mcp_client.Turn("u").run(lambda call_tool: None)
    assert "not running" in str(excinfo.value)


def test_the_message_distinguishes_an_outage_from_a_rejection(monkeypatch):
    """
    "Not running" and "refused our credentials" need different answers from
    whoever reads the log: one is wait, the other is fix the grant.
    """
    from exceptions import ExternalAPIError
    from services import mcp_client

    monkeypatch.setattr(mcp_client, "is_configured", lambda: True)
    monkeypatch.setattr(mcp_client.asyncio, "run", _failing_run(_task_group_failure("403")))

    with pytest.raises(ExternalAPIError) as excinfo:
        mcp_client.Turn("u").run(lambda call_tool: None)
    assert "refused" in str(excinfo.value)


def test_a_domain_error_is_not_reworded_as_a_transport_failure(monkeypatch):
    """A refusal we raised ourselves already says the right thing."""
    from exceptions import CapabilityDeniedError
    from services import mcp_client

    monkeypatch.setattr(mcp_client, "is_configured", lambda: True)

    monkeypatch.setattr(mcp_client.asyncio, "run",
                        _failing_run(CapabilityDeniedError("nope", requires_auth=False)))
    with pytest.raises(CapabilityDeniedError):
        mcp_client.Turn("u").run(lambda call_tool: None)


def test_an_outage_answers_gracefully_instead_of_500(anon_client, db, monkeypatch):
    """The end-to-end shape of the bug: a stopped server must not be a 500."""
    from exceptions import ExternalAPIError
    from services import mcp_client

    monkeypatch.setattr(agent_service, "is_connected", lambda: True)

    def down(force=False):
        raise ExternalAPIError("The research service is not running right now.")

    monkeypatch.setattr(mcp_client, "list_tools", down)

    resp = anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["status"] == agent_service.STATUS_NOT_CONNECTED
    assert "not running" in body["message"]
    assert body["answer"] is None


def test_an_outage_mid_turn_keeps_what_was_already_found(wired, monkeypatch):
    """A partial answer with citations beats an error page."""
    from exceptions import ExternalAPIError
    from services import mcp_client

    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
    ]

    class DyingTurn:
        def __init__(self, user_id=None):
            pass

        def run(self, plan):
            raise ExternalAPIError("The research service is not running right now.")

    monkeypatch.setattr(mcp_client, "Turn", DyingTurn)
    result = agent_service.ask("q", tier="anonymous")
    assert result["status"] == agent_service.STATUS_NOT_CONNECTED
    assert "not running" in result["message"]


# ---------------------------------------------------------------------------
# Stopping is not the same as giving up
# ---------------------------------------------------------------------------
#
# From a measured live run: five searches, 75 seconds, 23 papers found, and an
# empty answer — because the deadline fired at the limit with no room left to
# write anything down. Both stopping conditions now reserve time for one final
# tool-less turn.

def test_a_deadline_still_produces_an_answer_from_what_was_found(wired, monkeypatch):
    monkeypatch.setattr(agent_service, "SYNTHESIS_RESERVE_SECONDS", 0)
    monkeypatch.setattr(agent_service.config, "AGENT_DEADLINE_SECONDS", 0)
    wired["turns"] = [{"content": "Here is what I found."}]

    result = agent_service.ask("q", tier="anonymous")

    assert result["answer"] == "Here is what I found."
    # No apology over a perfectly good answer.
    assert result["message"] is None


def test_the_reserve_is_taken_out_of_the_deadline(wired, monkeypatch):
    """
    The point of the reserve: stop searching early enough to still answer. With
    a 10s budget and a 10s reserve, no tool call should ever be made.
    """
    monkeypatch.setattr(agent_service, "SYNTHESIS_RESERVE_SECONDS", 10)
    monkeypatch.setattr(agent_service.config, "AGENT_DEADLINE_SECONDS", 10)
    wired["turns"] = [{"content": "Answered without searching."}]

    result = agent_service.ask("q", tier="anonymous")

    assert wired["calls"] == []
    assert result["answer"] == "Answered without searching."


def test_post_evidence_planner_timeout_falls_back_to_synthesis(wired, monkeypatch):
    """Once papers are found, a slow planner should spend the reserve writing
    from those papers rather than failing the whole turn."""
    from exceptions import LLMTimeoutError

    calls = []

    def fake_chat(messages, tools, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": "", "tool_calls": [
                _tool_call("search_papers", '{"query": "rag"}')
            ]}
        if len(calls) == 2:
            raise LLMTimeoutError("planner timed out")
        return {
            "content": (
                "The papers report retrieval and grounding limits [1].\n\n"
                "```citations\n1 = p1\n```"
            )
        }

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", fake_chat)
    monkeypatch.setattr(agent_service, "POST_EVIDENCE_PLANNER_SECONDS", 15)

    result = agent_service.ask("q", tier="anonymous")

    assert result["status"] == agent_service.STATUS_OK
    assert result["answer"].startswith("The papers report")
    assert [c["paper_id"] for c in result["citations"]] == ["p1"]
    assert len(wired["calls"]) == 1
    assert len(calls) == 3
    assert calls[1]["timeout"] <= 15
    assert calls[2]["tool_choice"] == "none"


def test_first_planner_timeout_still_fails_without_evidence(wired, monkeypatch):
    """Fallback is evidence-driven: before any tool result exists there is
    nothing grounded to synthesize from."""
    from exceptions import LLMTimeoutError

    def timeout(*args, **kwargs):
        raise LLMTimeoutError("planner timed out")

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", timeout)

    result = agent_service.ask("q", tier="anonymous")

    assert result["status"] == agent_service.STATUS_NOT_CONNECTED
    assert result["sources"] == []
    assert "timed out" in result["message"]


def test_only_a_genuinely_empty_answer_gets_an_explanation(wired, monkeypatch):
    monkeypatch.setattr(agent_service.config, "AGENT_MAX_TOOL_CALLS", 1)
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": ""},                      # final synthesis turn says nothing
    ]
    result = agent_service.ask("q", tier="anonymous")
    assert result["answer"] in (None, "")
    assert "tool limit" in result["message"]


# ---------------------------------------------------------------------------
# Citation numbers have to mean something
# ---------------------------------------------------------------------------

def test_the_prompt_demands_a_citation_mapping():
    prompt = agent_service.build_system_prompt("anonymous")
    assert "```citations" in prompt
    assert "paper_id" in prompt
    # And not to keep hunting for a paper the corpus does not have.
    assert "same search again" in prompt


# ---------------------------------------------------------------------------
# Citations: the prose and the sidebar must agree
# ---------------------------------------------------------------------------
#
# Measured failure that produced this design: told to reuse numbers we assigned
# in discovery order, the live model wrote "[2]" for the second paper it chose
# to discuss, while our citation 2 was a different paper. The sidebar linked
# somewhere the sentence never meant. The model now declares the mapping and we
# rebuild from that, so both come from one statement instead of two independent
# acts of counting.

FOUND = [
    {"number": 0, "paper_id": "aaa", "title": "A Survey of RAG",
     "publication_year": 2023, "venue": "arXiv", "similarity": 0.9},
    {"number": 0, "paper_id": "bbb", "title": "Active Retrieval",
     "publication_year": 2023, "venue": None, "similarity": 0.7},
    {"number": 0, "paper_id": "ccc", "title": "Never Mentioned",
     "publication_year": 2021, "venue": None, "similarity": 0.4},
]


def test_the_mapping_decides_which_paper_each_number_means():
    answer = (
        "Surveys cover the field [1]. Active retrieval refines it [2].\n\n"
        "```citations\n1 = aaa\n2 = bbb\n```"
    )
    prose, citations = agent_service.resolve_citations(answer, FOUND)

    assert "```citations" not in prose
    assert [(c["number"], c["paper_id"]) for c in citations] == [(1, "aaa"), (2, "bbb")]
    assert citations[0]["title"] == "A Survey of RAG"


def test_the_model_may_number_however_it_likes():
    """
    Its own narrative order is fine — what matters is that the mapping says what
    it meant. Fighting the model over numbering is what failed before.
    """
    answer = "Active retrieval refines it [7].\n\n```citations\n7 = bbb\n```"
    prose, citations = agent_service.resolve_citations(answer, FOUND)
    assert [(c["number"], c["paper_id"]) for c in citations] == [(7, "bbb")]
    assert "[7]" in prose


def test_papers_found_but_never_cited_are_not_citations():
    """A paper the search turned up and the answer never mentions is a search
    result, not a citation. Listing it pads the sidebar with things the text
    does not support."""
    answer = "Surveys cover the field [1].\n\n```citations\n1 = aaa\n2 = ccc\n```"
    _, citations = agent_service.resolve_citations(answer, FOUND)
    assert [c["paper_id"] for c in citations] == ["aaa"]


def test_a_marker_with_no_mapping_is_removed_from_the_prose():
    """
    A marker with nothing behind it promises a source, and the reader cannot
    tell it is broken without checking.
    """
    answer = "Grounded [1]. Unsupported [9].\n\n```citations\n1 = aaa\n```"
    prose, citations = agent_service.resolve_citations(answer, FOUND)
    assert "[9]" not in prose
    assert "[1]" in prose
    assert len(citations) == 1


def test_a_mapping_to_an_unknown_paper_is_dropped():
    """The id has to be one the search actually returned, not one invented."""
    answer = "Claim [1].\n\n```citations\n1 = zzz\n```"
    prose, citations = agent_service.resolve_citations(answer, FOUND)
    assert citations == []
    assert "[1]" not in prose


def test_no_mapping_means_no_numbered_citations():
    """Showing none is honest; showing wrong ones is not."""
    answer = "Surveys cover the field [1]. Active retrieval refines it [2]."
    prose, citations = agent_service.resolve_citations(answer, FOUND)
    assert citations == []
    assert "[1]" not in prose and "[2]" not in prose
    assert prose.startswith("Surveys cover the field")


def test_removing_a_marker_does_not_leave_a_gap_before_the_full_stop():
    answer = "A claim [4]."
    prose, _ = agent_service.resolve_citations(answer, FOUND)
    assert prose == "A claim."


def test_an_unterminated_block_is_still_read():
    """Models truncate. A missing closing fence should not lose every source."""
    answer = "Claim [1].\n\n```citations\n1 = aaa\n"
    prose, citations = agent_service.resolve_citations(answer, FOUND)
    assert [c["paper_id"] for c in citations] == ["aaa"]
    assert "```" not in prose


def test_the_turn_returns_only_resolved_citations(wired):
    """End to end through ask(): the envelope carries what the prose supports."""
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Grounding works [1].\n\n```citations\n1 = p2\n```"},
    ]
    result = agent_service.ask("q", tier="anonymous")

    # p1 and p2 were both found; only p2 was cited.
    assert [(c["number"], c["paper_id"]) for c in result["citations"]] == [(1, "p2")]
    assert result["answer"] == "Grounding works [1]."


# ---------------------------------------------------------------------------
# Citation metadata: the prompt may only claim what the page renders
# ---------------------------------------------------------------------------
#
# The prompt used to tell the model that authors, DOIs, venues and citation
# counts were "displayed beside your answer". None of the four were. So the
# model kept writing "cited 747 times" inline — correctly, because otherwise the
# number vanished — and that was mistaken for it ignoring an instruction. The
# instruction was false.

def test_the_citation_count_survives_to_the_envelope(wired):
    wired["tool_result"] = [
        {"paper_id": "p9", "title": "A Survey", "publication_year": 2023,
         "venue": "arXiv", "citation_count": 747, "influence_score": 27.0},
    ]
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Surveyed [1].\n\n```citations\n1 = p9\n```"},
    ]
    citation = agent_service.ask("q", tier="anonymous")["citations"][0]

    assert citation["citation_count"] == 747
    assert citation["venue"] == "arXiv"


def test_a_zero_citation_count_is_kept_not_dropped(wired):
    """Zero is a real answer about a paper — an unread preprint — and saying so
    is more useful than saying nothing."""
    wired["tool_result"] = [
        {"paper_id": "p9", "title": "New Preprint", "publication_year": 2026,
         "citation_count": 0},
    ]
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Recent [1].\n\n```citations\n1 = p9\n```"},
    ]
    assert agent_service.ask("q", tier="anonymous")["citations"][0]["citation_count"] == 0


def test_the_influence_score_is_not_carried(wired):
    """
    Left out on purpose: "influential" cannot be labelled in two words without
    misleading, and it is null for papers that came from OpenAlex alone, so it
    would show on some citations and not others and be read as zero.
    """
    wired["tool_result"] = [
        {"paper_id": "p9", "title": "A Survey", "citation_count": 10,
         "influence_score": 27.0},
    ]
    wired["turns"] = [
        {"content": "", "tool_calls": [_tool_call("search_papers")]},
        {"content": "Surveyed [1].\n\n```citations\n1 = p9\n```"},
    ]
    assert "influence_score" not in agent_service.ask("q", tier="anonymous")["citations"][0]


def test_the_prompt_claims_only_what_the_page_renders():
    """
    The guard on the original bug. Every field the prompt tells the model not to
    repeat must actually be rendered by chat.js, or the model is being asked to
    delete information the reader will never get.
    """
    import io as _io
    import pathlib

    prompt = agent_service.build_system_prompt("anonymous")
    section = prompt.split("What the reader already sees")[1].split("##")[0]

    js = _io.open(pathlib.Path(__file__).resolve().parents[1] / "dashboard"
                  / "static" / "js" / "chat.js", encoding="utf-8").read()
    renderer = js.split("function citationMeta")[1].split("function addCitations")[0]

    for claimed, field in [("title", "c.title"), ("year", "publication_year"),
                           ("venue", "c.venue"), ("citation count", "citation_count")]:
        assert claimed in section, claimed
        assert field in renderer or field in js, field


def test_the_prompt_does_not_claim_authors_or_dois_are_shown():
    """They are not rendered, so telling the model to omit them would delete
    them from the reader's view entirely."""
    prompt = agent_service.build_system_prompt("anonymous")
    section = prompt.split("What the reader already sees")[1].split("##")[0]
    assert "not* displayed" in section or "not displayed" in section
    assert "name authors" in section


# ---------------------------------------------------------------------------
# A hung provider must not outlive the turn
# ---------------------------------------------------------------------------
#
# Measured: a free-tier call hung and the turn ran 775 seconds with zero
# completed LLM turns. The wall-clock deadline was only checked BETWEEN turns,
# so a call that never returned was never interrupted. Render's gunicorn would
# have killed the worker at 120s.

def test_each_call_is_bounded_by_what_is_left_of_the_budget(wired, monkeypatch):
    seen = []

    def capture(messages, tools, **kwargs):
        seen.append(kwargs.get("timeout"))
        return {"content": "Done."}

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", capture)
    monkeypatch.setattr(agent_service.config, "AGENT_DEADLINE_SECONDS", 40)

    agent_service.ask("q", tier="anonymous")

    assert seen, "no LLM call was made"
    assert all(t is not None for t in seen), "a call was made with no timeout"
    assert all(t <= 40 for t in seen), seen


def test_planner_call_preserves_the_synthesis_reserve(wired, monkeypatch):
    """A research/planning call must not be allowed to consume the final-answer reserve."""
    seen = []

    def capture(messages, tools, **kwargs):
        seen.append(kwargs.get("timeout"))
        return {"content": "Done."}

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", capture)
    monkeypatch.setattr(agent_service.config, "AGENT_DEADLINE_SECONDS", 40)
    monkeypatch.setattr(agent_service, "SYNTHESIS_RESERVE_SECONDS", 20)

    agent_service.ask("q", tier="anonymous")

    assert seen, "no planner call was made"
    assert seen[0] <= 20.1, seen


def test_a_call_is_never_given_an_impossible_deadline(wired, monkeypatch):
    """
    With the budget already spent, the final synthesis turn still needs long
    enough to answer — a zero-second timeout would fail it instantly and throw
    away everything the turn had gathered.
    """
    seen = []

    def capture(messages, tools, **kwargs):
        seen.append(kwargs.get("timeout"))
        return {"content": "Done."}

    monkeypatch.setattr(agent_service.llm_client, "chat_with_tools", capture)
    monkeypatch.setattr(agent_service.config, "AGENT_DEADLINE_SECONDS", 0)

    agent_service.ask("q", tier="anonymous")

    assert seen == [agent_service.MIN_CALL_SECONDS]


def test_openrouter_http_error_keeps_provider_detail(monkeypatch, caplog):
    """A non-2xx response must expose OpenRouter's diagnostic body, not just
    requests' generic '404 Not Found' text."""
    import llm_client
    from exceptions import ExternalAPIError

    class FakeResponse:
        status_code = 404
        text = '{"error":{"message":"No endpoints found for this model"}}'

        def json(self):
            return {"error": {"message": "No endpoints found for this model"}}

    async def fake_request(payload, timeout):
        return FakeResponse()

    monkeypatch.setattr(llm_client, "_request_openrouter", fake_request)
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEY", "test-key")

    with pytest.raises(ExternalAPIError) as excinfo:
        llm_client.chat_with_tools([{"role": "user", "content": "hi"}], [])

    assert "404" in str(excinfo.value)
    assert "No endpoints found for this model" in str(excinfo.value)
    assert "OpenRouter request rejected" in caplog.text


def test_the_llm_client_honours_a_caller_supplied_timeout(monkeypatch):
    """The caller's remaining budget must reach the cancellable HTTP layer."""
    import llm_client

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    async def fake_request(payload, timeout):
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(llm_client, "_request_openrouter", fake_request)
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEY", "test-key")

    llm_client.chat_with_tools([{"role": "user", "content": "hi"}], [], timeout=12.5)
    assert captured["timeout"] == 12.5


def test_tool_choice_is_forwarded_to_openrouter_payload(monkeypatch):
    """Final synthesis can keep schemas while explicitly forbidding tool calls."""
    import llm_client

    captured = {}

    def fake_post(payload, timeout=None, usage=None):
        captured.update(payload)
        return {"choices": [{"message": {"content": "done"}}]}

    monkeypatch.setattr(llm_client, "_post", fake_post)

    llm_client.chat_with_tools(
        [{"role": "user", "content": "answer now"}],
        [{"type": "function", "function": {"name": "search_papers"}}],
        tool_choice="none",
    )

    assert captured["tool_choice"] == "none"
    assert captured["tools"]


def test_openrouter_call_has_a_true_wall_clock_deadline(monkeypatch):
    """A provider that stays active forever must still be cancelled at the
    caller's total deadline, rather than keeping Alfred on Thinking indefinitely."""
    import asyncio
    import time

    import llm_client
    from exceptions import ExternalAPIError

    async def never_finishes(payload, timeout):
        await asyncio.sleep(60)

    monkeypatch.setattr(llm_client, "_request_openrouter", never_finishes)
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEY", "test-key")

    started = time.monotonic()
    with pytest.raises(ExternalAPIError) as excinfo:
        llm_client._post({"messages": []}, timeout=0.02)
    elapsed = time.monotonic() - started

    assert "wall-clock deadline" in str(excinfo.value)
    assert elapsed < 0.5
