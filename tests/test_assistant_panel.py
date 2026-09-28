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
