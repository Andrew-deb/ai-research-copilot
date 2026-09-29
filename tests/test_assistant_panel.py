"""The panel shares chat execution while page hints stay owner-scoped."""

import pytest

from exceptions import ValidationError
from services import assistant_context


def test_paper_context_is_looked_up_not_trusted(db):
    paper = db.seed_paper(title="Verified paper")
    result = assistant_context.resolve(None, "paper", str(paper["paper_id"]))
    assert result["label"] == "Verified paper"
    assert result["id"] == str(paper["paper_id"])


def test_collection_context_cannot_cross_users(db):
    owner = db.get_or_create_user("owner@example.com")
    stranger = db.get_or_create_user("stranger@example.com")
    collection = db.create_collection(str(owner["user_id"]), "Owned collection")
    with pytest.raises(ValidationError):
        assistant_context.resolve(str(stranger["user_id"]), "collection",
                                  str(collection["collection_id"]))


def test_anonymous_private_context_and_unknown_kind_refused():
    with pytest.raises(ValidationError):
        assistant_context.resolve(None, "notes")
    with pytest.raises(ValidationError):
        assistant_context.resolve(None, "unknown", "id")
    with pytest.raises(ValidationError):
        assistant_context.resolve(None, "paper", "not-a-uuid")


def test_curated_collection_is_visible_to_demo(db):
    curated = db.create_collection("system", "Shared example")
    db.collections[str(curated["collection_id"])]["is_curated"] = True
    context = assistant_context.resolve(None, "collection", str(curated["collection_id"]))
    assert context["label"] == "Shared example"


def test_panel_replays_owned_chat_and_shares_composer(client, db):
    response = client.get("/chat/assistant?context_kind=dashboard")
    assert response.status_code == 200
    assert b'data-surface="assistant"' in response.data
    assert b'id="chat-composer"' in response.data
    assert b'data-context-kind="dashboard"' in response.data


def test_panel_rejects_foreign_context_before_rendering(client, db):
    owner = db.get_or_create_user("other@example.com")
    collection = db.create_collection(str(owner["user_id"]), "Private")
    response = client.get("/chat/assistant?context_kind=collection&context_id=" +
                          str(collection["collection_id"]))
    assert response.status_code == 400


def test_panel_stream_gets_durable_assistant_origin_before_run(client, db, monkeypatch):
    from routes import chat
    monkeypatch.setattr(chat.agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(chat, "consume_quota", lambda metric: None)

    def fake_stream(question, tier, user_id, conversation_id, **options):
        assert options["surface"] == "assistant"
        assert options["context"]["label"] == "Paper search"
        yield chat._sse({"type": "conversation", "conversation_id": conversation_id})

    monkeypatch.setattr(chat, "_stream_turn", fake_stream)
    response = client.post("/chat/ask", json={"question": "Find relevant papers",
        "surface": "assistant", "context_kind": "search"},
        headers={"Accept": "text/event-stream"})
    assert response.status_code == 200
    assert b'"type": "conversation"' in response.data
    assert len(db.conversations) == 1
    saved = next(iter(db.conversations.values()))
    assert saved["origin"] == "assistant"
    assert saved["origin_context"] == "Paper search"


def test_wick_tool_policy_is_enforced_not_only_advertised():
    from exceptions import CapabilityDeniedError
    from services import agent_service

    assert "compare_papers" in agent_service.callable_tools("authenticated", "research")
    assert "compare_papers" not in agent_service.callable_tools("authenticated", "wick")
    with pytest.raises(CapabilityDeniedError):
        agent_service.ensure_callable("authenticated", "compare_papers", "wick")
    assert "create_collection" in agent_service.callable_tools("authenticated", "wick")
    assert "create_collection" not in agent_service.callable_tools("anonymous", "wick")


def test_wick_mode_in_full_chat_and_panel_cannot_claim_research(client):
    response = client.get("/chat?mode=wick")
    assert response.status_code == 200
    assert b'data-chat-mode="wick"' in response.data
    assert b'<option value="wick" selected>' in response.data
    composer = response.get_data(as_text=True).split('<form class="composer', 1)[1].split("</form>", 1)[0]
    assert 'id="chat-mode"' in composer
    assert 'class="composer-footer"' in composer
    assert 'Ask Wick about your workspace' in composer

    from routes import chat
    # Mode is rejected before quota consumption or a tool connection is attempted.
    response = client.post("/chat/ask", json={"question": "Show my collections",
        "surface": "assistant", "chat_mode": "research"})
    assert response.status_code == 400


def test_wick_context_is_resolved_for_full_chat(client, db):
    paper = db.seed_paper(title="Selected paper")
    response = client.get("/chat?mode=wick&context_kind=paper&context_id=" +
                          str(paper["paper_id"]))
    assert response.status_code == 200
    assert b'data-context-kind="paper"' in response.data
    assert b"Selected paper" in response.data


def test_context_choices_are_scoped_to_current_user(client, db):
    owner = db.get_or_create_user("someone-else@example.com")
    private = db.create_collection(str(owner["user_id"]), "Private elsewhere")
    response = client.get("/chat/assistant/contexts")
    assert response.status_code == 200
    assert str(private["collection_id"]) not in response.get_data(as_text=True)


def test_new_full_chat_wick_turn_is_listed_with_wick_history(client, db, monkeypatch):
    from routes import chat
    monkeypatch.setattr(chat.agent_service, "is_connected", lambda: True)
    monkeypatch.setattr(chat, "consume_quota", lambda metric: None)
    monkeypatch.setattr(chat, "_run_turn", lambda *args, **kwargs: {
        "status": "ok", "question": "Show collections", "answer": "Here are your collections.",
        "citations": [], "sources": [], "tool_calls": [],
        "usage": {"llm_turns": 1, "tool_calls": 0, "embedding_calls": 0},
    })
    response = client.post("/chat/ask", json={
        "question": "Show collections", "chat_mode": "wick", "surface": "agent"})
    assert response.status_code == 200
    saved = next(iter(db.conversations.values()))
    assert saved["origin"] == "assistant"
