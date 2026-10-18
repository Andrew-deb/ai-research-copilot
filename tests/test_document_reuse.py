"""Saved-file selection and membership retain independent owner boundaries."""

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
import upload_config
from exceptions import ValidationError
from repositories import uploads, document_collections as repository
from services import (
    upload_service,
    attachment_context,
    document_library,
    document_collections,
    collection_service,
)
from tests.test_private_uploads import cursor_repo

DOC = str(uuid4())
CID = str(uuid4())
OTHER = str(uuid4())


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(upload_config, "ENABLED", True)
    monkeypatch.setattr(
        upload_service.lakebase,
        "get_conversation",
        lambda owner, cid: {"conversation_id": cid} if owner == "owner" else None,
    )
    row = {
        "document_id": DOC,
        "conversation_id": CID,
        "filename": "Saved.pdf",
        "retained_at": datetime.now(timezone.utc),
        "status": "ready",
        "expired": False,
        "original_bytes": 100,
        "expires_at": datetime.now(timezone.utc),
    }
    monkeypatch.setattr(upload_service, "owned", lambda owner, doc: row)
    return row


def test_cross_chat_selection_requires_retention_and_owned_target(ready):
    refs = attachment_context.references("owner", OTHER, [DOC])
    assert refs[0]["conversation_id"] == OTHER and refs[0]["retained"]
    with pytest.raises(ValidationError):
        attachment_context.references("foreign", OTHER, [DOC])
    ready["retained_at"] = None
    with pytest.raises(ValidationError):
        attachment_context.references("owner", OTHER, [DOC])


def test_saved_deleted_file_is_not_readable(ready):
    ready["status"] = "deleting"
    with pytest.raises(ValidationError):
        attachment_context.references("owner", OTHER, [DOC])
    assert (
        attachment_context.references("owner", OTHER, [DOC], strict=False)[0][
            "available"
        ]
        is False
    )


def test_document_picker_is_paginated_owner_scoped_metadata_only(monkeypatch, ready):
    query = Mock(return_value=[dict(ready)] * 21)
    monkeypatch.setattr(uploads, "find_retained", query)
    result = document_library.options("owner", "Saved", 2)
    assert len(result["items"]) == 20 and result["next_cursor"] == "3"
    query.assert_called_once_with("owner", "Saved", 21, 20)
    assert (
        result["items"][0]["kind"] == "attachment"
        and "object_key" not in result["items"][0]
    )


def test_cross_chat_reference_binding_rechecks_both_owners_and_state(monkeypatch):
    write = Mock(return_value=1)
    monkeypatch.setattr(uploads.lakebase, "run_write", write)
    uploads.bind_reference("owner", OTHER, DOC)
    sql, args = write.call_args.args
    assert args == (OTHER, "owner", DOC) and "d.user_id=c.user_id" in sql
    assert "d.status='ready'" in sql and "d.retained_at IS NOT NULL" in sql


def test_collection_add_locks_document_and_collection_and_rechecks_owner(
    monkeypatch, ready
):
    cur = cursor_repo(monkeypatch, [(CID,), (DOC,)])
    assert repository.add("owner", CID, DOC)["status"] == "added"
    calls = [c.args[0] for c in cur.execute.call_args_list]
    assert "pg_advisory_xact_lock" in calls[0]
    assert "FOR UPDATE" in calls[1] and "NOT is_curated" in calls[1]
    assert "d.user_id=%s" in calls[2] and "d.status='ready'" in calls[2]
    assert "retained_at IS NOT NULL" in calls[2] and "<100" in calls[2]


def test_foreign_or_curated_collection_refused_before_membership_write(monkeypatch):
    cur = cursor_repo(monkeypatch, [None])
    with pytest.raises(ValidationError):
        repository.add("owner", CID, DOC)
    assert not any("INSERT" in c.args[0] for c in cur.execute.call_args_list)


def test_removing_membership_does_not_delete_document_or_index(monkeypatch):
    write = Mock(return_value=1)
    monkeypatch.setattr(repository.lakebase, "run_write", write)
    repository.remove("owner", CID, DOC)
    sql, args = write.call_args.args
    assert (
        "DELETE FROM collection_documents" in sql
        and "private_uploads" not in sql
        and "document_chunks" not in sql
    )
    assert args == ("owner", "owner", CID, DOC)


def test_explicit_deletion_drops_private_index_and_memberships(monkeypatch):
    cur = cursor_repo(monkeypatch, [(DOC,)])
    uploads.request_delete("owner", DOC)
    sql = [c.args[0] for c in cur.execute.call_args_list]
    assert any("DELETE FROM document_chunks" in q for q in sql)
    assert any("DELETE FROM document_index_jobs" in q for q in sql)
    assert any("DELETE FROM collection_documents" in q for q in sql)
    assert not any("reserved_bytes=0" in q for q in sql)


def test_anon_collection_document_writes_denied(anon_client, ready):
    assert (
        anon_client.post(
            "/collection/" + CID + "/documents", json={"document_id": DOC}
        ).status_code
        == 403
    )
    assert (
        anon_client.post(
            "/collection/" + CID + "/documents/" + DOC + "/remove", json={}
        ).status_code
        == 403
    )


def test_collection_membership_csrf_precedes_service(app, client, ready, monkeypatch):
    change = Mock()
    monkeypatch.setattr(document_collections, "modify", change)
    app.config["WTF_CSRF_ENABLED"] = True
    assert (
        client.post(
            "/collection/" + CID + "/documents", json={"document_id": DOC}
        ).status_code
        == 400
    )
    change.assert_not_called()


def test_collection_organisation_rejects_bad_uuid_before_database(monkeypatch, ready):
    require = Mock()
    monkeypatch.setattr(collection_service, "_require_writable_collection", require)
    with pytest.raises(ValidationError):
        document_collections.modify("owner", "not-a-uuid", DOC)
    require.assert_not_called()


def test_saved_document_is_found_in_wick_all_assets(monkeypatch, ready):
    from services import assistant_context

    monkeypatch.setattr(
        assistant_context.shared_workspace,
        "find_workspace_resources",
        lambda *a, **k: {"items": [], "next_cursor": None},
    )
    monkeypatch.setattr(uploads, "find_retained", lambda *a: [ready])
    assert (
        assistant_context.search("owner", "Saved", category="assets")["items"][0][
            "kind"
        ]
        == "attachment"
    )


def test_cross_chat_selected_file_survives_history(client, db, monkeypatch, ready):
    from services import agent_service, conversation_service

    monkeypatch.setattr(agent_service, "is_connected", lambda *a: True)
    monkeypatch.setattr(
        agent_service,
        "ask",
        lambda question, **kwargs: agent_service.envelope(
            question, answer="Read the selected file."
        ),
    )
    first = client.post("/chat/ask", json={"question": "Start"}).get_json()
    target = first["conversation_id"]
    owner = db.conversations[target]["user_id"]
    # Restore the real fake-database owner check instead of this module's unit stub.
    monkeypatch.setattr(
        upload_service.lakebase, "get_conversation", db.get_conversation
    )
    monkeypatch.setattr(
        upload_service,
        "owned",
        lambda user, doc: ready if user == owner and doc == DOC else None,
    )
    bind = Mock(return_value=1)
    monkeypatch.setattr(uploads, "bind_reference", bind)
    prompt = "Read [▤Saved.pdf](/uploads/" + DOC + "/download)"
    result = client.post(
        "/chat/ask",
        json={"question": prompt, "conversation_id": target, "attachment_ids": [DOC]},
    )
    assert result.status_code == 200
    bind.assert_called_once_with(owner, target, DOC)
    history = conversation_service.load(owner, target)
    references = history["messages"][-2]["usage"]["attachment_references"]
    assert references[0]["document_id"] == DOC and references[0]["available"]
