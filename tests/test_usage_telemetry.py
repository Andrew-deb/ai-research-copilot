"""
tests/test_usage_telemetry.py — what an AI operation actually cost.

Phase 3.6 sets every quota in the product from measured cost per operation, and
it reads `ai_operations` to do it. Before this, that table recorded latency and
turn counts and left `input_tokens`, `output_tokens` and `estimated_cost_usd`
NULL on every row — 16 rows, none of them priced. The numbers were being
discarded at the point they arrived: OpenRouter returns a `usage` block on every
response and `llm_client` read `choices[0].message` and dropped the rest.

The loss is not recoverable. A query served today without its cost recorded is a
calibration sample that cannot be collected again, which is why this lands
before deployment rather than with 3.6.
"""

import pytest

import llm_client
from exceptions import ExternalAPIError
from services import telemetry_service

XHR = {"X-Requested-With": "XMLHttpRequest"}


def _body(prompt=100, completion=20, cost=0.001, model="z-ai/glm-4.6"):
    return {
        "model": model,
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion, "cost": cost},
        "choices": [{"message": {"content": "an answer"}}],
    }


# ---------------------------------------------------------------------------
# The tally
# ---------------------------------------------------------------------------

def test_a_turns_calls_are_summed_not_overwritten():
    """
    An agent turn makes several model calls. Only their sum is a cost, so the
    last call must not replace the ones that paid for the search before it.
    """
    tally = llm_client.Usage()
    tally.add(_body(prompt=1200, completion=350, cost=0.0021))
    tally.add(_body(prompt=800, completion=120, cost=0.0009))

    assert tally.calls == 2
    assert tally.input_tokens == 2000
    assert tally.output_tokens == 470
    assert tally.estimated_cost_usd == pytest.approx(0.0030)


def test_the_model_recorded_is_the_one_that_answered():
    """OpenRouter routes to a provider of its choosing and can fall back to a
    different model, so the request's model name is a hope, not a record."""
    tally = llm_client.Usage()
    tally.add(_body(model="z-ai/glm-4.6"))
    assert tally.model == "z-ai/glm-4.6"
    assert tally.provider == "openrouter"


def test_a_missing_cost_does_not_become_a_zero():
    """
    Some providers report no cost. Zero would say the call was free, and 3.6
    averages this column — a handful of false zeros under-prices the quota
    derived from them.
    """
    tally = llm_client.Usage()
    tally.add({"model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    assert tally.input_tokens == 10
    assert tally.estimated_cost_usd == 0.0

    op = telemetry_service.Operation("agent_query", "anonymous", None)
    op.spent(tally)
    assert op.input_tokens == 10
    assert op.estimated_cost_usd is None       # unpriced, not free


def test_an_unreadable_usage_block_never_costs_the_answer():
    """
    Telemetry that raises would take down the request it was measuring, having
    already spent the visitor's allowance on it. A dropped measurement is a gap
    in a sample; a raised exception is a lost answer.
    """
    tally = llm_client.Usage()
    tally.add(_body(prompt=50, completion=10, cost=0.0005))
    tally.add({"usage": {"prompt_tokens": "not a number"}})       # must not raise

    assert tally.input_tokens == 50               # the good call survives intact


def test_nothing_is_claimed_when_no_model_was_called():
    """
    NULL and 0 are different claims: "no model was called" against "a model was
    called and cost nothing". A RAG request that matched no papers returns
    before the LLM, and must not enter the sample as a free query.
    """
    op = telemetry_service.Operation("rag_query", "anonymous", None)
    op.spent(llm_client.Usage())

    assert op.input_tokens is None
    assert op.output_tokens is None
    assert op.estimated_cost_usd is None
    assert op.model is None


# ---------------------------------------------------------------------------
# Reading it off the wire
# ---------------------------------------------------------------------------

def test_tokens_are_counted_even_when_the_body_carries_an_error(monkeypatch):
    """
    OpenRouter answers 200 with an error object when a provider rate-limits. The
    input tokens were still sent and still billed, and an operation that failed
    expensively is the one calibration can least afford to miss.
    """
    class Resp:
        status_code = 200

        def raise_for_status(self): pass

        def json(self):
            return {"model": "m", "usage": {"prompt_tokens": 900,
                                            "completion_tokens": 0, "cost": 0.0004},
                    "error": {"message": "rate limited upstream"}}

    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **k: Resp())

    tally = llm_client.Usage()
    with pytest.raises(ExternalAPIError):
        llm_client._post({"messages": []}, usage=tally)

    assert tally.input_tokens == 900
    assert tally.estimated_cost_usd == pytest.approx(0.0004)


# ---------------------------------------------------------------------------
# End to end, through the routes that already meter
# ---------------------------------------------------------------------------

def _spending_ask(prompt, completion, cost, raises=None):
    """A stand-in agent that spends before it succeeds or fails."""
    def ask(question, *, tier, user_id=None, on_event=None, usage=None):
        if usage is not None:
            usage.add(_body(prompt=prompt, completion=completion, cost=cost))
        if raises:
            raise raises
        from services import agent_service
        return agent_service.envelope(question, answer="Done [1].", llm_turns=2)
    return ask


def test_an_agent_turn_records_what_it_spent(anon_client, db, monkeypatch):
    from services import agent_service
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(agent_service, "ask", _spending_ask(4100, 780, 0.0062))

    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)

    row = [r for r in db.ai_operations if r["metric"] == "agent_query"][-1]
    assert row["input_tokens"] == 4100
    assert row["output_tokens"] == 780
    assert row["estimated_cost_usd"] == pytest.approx(0.0062)
    assert row["provider"] == "openrouter"
    assert row["model"]
    assert row["ok"] is True


def test_a_turn_that_fails_still_records_what_it_burned(anon_client, db, monkeypatch):
    """
    The expensive failures are the ones a ceiling has to be set against. Without
    the `finally`, a turn that searched four times and then died would enter the
    record as having cost nothing.
    """
    from services import agent_service
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(agent_service, "ask",
                        _spending_ask(3000, 0, 0.004,
                                      raises=ExternalAPIError("provider gave up")))

    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)

    row = [r for r in db.ai_operations if r["metric"] == "agent_query"][-1]
    assert row["ok"] is False
    assert row["input_tokens"] == 3000
    assert row["estimated_cost_usd"] == pytest.approx(0.004)


def test_the_rag_baseline_is_priced_too(anon_client, db, monkeypatch):
    """3.6 compares an agent run against RAG. Both sides need a number."""
    def chat(system_prompt, user_prompt, usage=None, **kw):
        if usage is not None:
            usage.add(_body(prompt=1500, completion=200, cost=0.0011))
        return "Synthesised answer [1]."

    monkeypatch.setattr(llm_client, "chat", chat)
    # Retrieval has to return something, or rag_answer stops before the model -
    # which is the early return the NULL-not-zero rule above exists for.
    from repositories import lakebase
    monkeypatch.setattr(lakebase, "semantic_search_papers", lambda vec, top_k=10: [{
        "paper_id": "11111111-1111-1111-1111-111111111111",
        "title": "A paper", "publication_year": 2023, "venue": "NeurIPS",
        "similarity": 0.8, "chunk_text": "some text", "chunk_index": 0,
        "section_name": None,
    }])
    anon_client.post("/search/ask", json={"question": "why?"}, headers=XHR)

    row = [r for r in db.ai_operations if r["metric"] == "rag_query"][-1]
    assert row["input_tokens"] == 1500
    assert row["estimated_cost_usd"] == pytest.approx(0.0011)


def test_a_search_records_which_backend_embedded_it(anon_client, db):
    """
    No model call, so no tokens — but a query embedded on a hosted API and one
    embedded in-process are not the same operation, and the deployment uses a
    different backend from a laptop.
    """
    anon_client.get("/search/semantic?q=attention")

    row = [r for r in db.ai_operations if r["metric"] == "semantic_search"][-1]
    assert row["provider"] in ("huggingface", "local")
    assert row["model"] == "nomic-ai/modernbert-embed-base"
    assert row["input_tokens"] is None          # nothing was sent to an LLM


# ---------------------------------------------------------------------------
# The shape the table needs
# ---------------------------------------------------------------------------

def test_every_column_3_6_reads_is_written(anon_client, db, monkeypatch):
    """
    The checklist for the phase that was blocked on this: cost, tokens, turns,
    tool calls, latency and outcome, all on one row.
    """
    from services import agent_service
    monkeypatch.setattr(agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(agent_service, "ask", _spending_ask(2200, 640, 0.0035))

    anon_client.post("/chat/ask", json={"question": "why?"}, headers=XHR)
    row = [r for r in db.ai_operations if r["metric"] == "agent_query"][-1]

    for column in ("input_tokens", "output_tokens", "estimated_cost_usd",
                   "llm_turns", "tool_calls", "latency_ms", "ok",
                   "provider", "model", "tier"):
        assert row.get(column) is not None, column
