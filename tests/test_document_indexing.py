"""Private index lifecycle and model contracts, with no live embedding calls."""

import json
from unittest.mock import Mock
from uuid import uuid4
import pytest
import upload_config, embedding
from exceptions import ValidationError, EmbeddingError
from repositories import (
    document_index,
    uploads,
    document_collections as collection_repo,
)
from services import (
    document_indexing as service,
    document_index_worker as worker,
    document_artifacts,
)
from services import (
    upload_service,
    document_library,
    attachment_context,
    document_collections,
)
from mcp_server.shared_resource.services.document_chunks import split, MAX_CHUNKS
from tests.test_private_uploads import cursor_repo

DOC = str(uuid4())
CID = str(uuid4())
OTHER = str(uuid4())


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(upload_config, "ENABLED", True)
    monkeypatch.setattr(upload_config, "INDEXING_ENABLED", True)
    monkeypatch.setattr(upload_config, "INDEX_MODEL_VERSION", "test-pinned-revision")


@pytest.fixture
def job(enabled):
    return {
        "document_id": DOC,
        "user_id": "owner",
        "contract": service.contract(),
        "artifact_hash": "hash",
        "indexed_hash": "hash",
        "attempts": 0,
        "lease_token": "lease",
        "index_bytes": document_index.RESERVATION,
    }


def test_chunking_preserves_real_pages_offsets_and_overlap():
    result = split(
        {"pages": [{"page": 1, "text": "a" * 2500}, {"page": 2, "text": "second page"}]}
    )
    assert [r["offset"] for r in result if r["page"] == 1] == [0, 1000, 2000]
    assert result[0]["text"][1000:] == result[1]["text"][:200]
    assert result[-1]["page"] == 2


@pytest.mark.parametrize(
    "artifact",
    [
        {},
        {"pages": []},
        {"pages": [{"page": 2, "text": "wrong"}]},
        {"pages": [{"page": 1, "text": "x" * 500000}]},
    ],
)
def test_bad_or_unbounded_extraction_fails(artifact):
    with pytest.raises(ValueError):
        split(artifact)


def test_disabled_indexing_never_encodes_or_queues(monkeypatch):
    monkeypatch.setattr(upload_config, "ENABLED", True)
    monkeypatch.setattr(upload_config, "INDEXING_ENABLED", False)
    encode = Mock()
    queue = Mock()
    monkeypatch.setattr(embedding, "encode_document", encode)
    monkeypatch.setattr(document_index, "queue", queue)
    with pytest.raises(ValidationError):
        service.request_index("owner", DOC)
    assert worker.process_one() is False
    encode.assert_not_called()
    queue.assert_not_called()


def test_contract_requires_explicit_version_and_tracks_settings(monkeypatch, enabled):
    old = service.contract()
    monkeypatch.setattr(upload_config, "INDEX_MODEL_VERSION", "changed-revision")
    assert service.contract() != old
    assert json.loads(old)["document_prefix"] == "search_document: "
    monkeypatch.setattr(upload_config, "INDEX_MODEL_VERSION", "")
    with pytest.raises(ValidationError):
        service.contract()


def test_document_embedding_uses_correct_prefix_and_normalizes(monkeypatch):
    encode = Mock(return_value=[1.0] * 768)
    monkeypatch.setattr(embedding, "active_backend", lambda: "local")
    monkeypatch.setattr(embedding, "_encode_local", encode)
    result = embedding.encode_document(" Text ")
    encode.assert_called_once_with("search_document: Text")
    assert abs(sum(x * x for x in result) - 1) < 0.00001
    with pytest.raises(EmbeddingError):
        embedding._validate([float("nan")] * 768)


def test_worker_publishes_only_after_all_chunks_and_renewals(monkeypatch, job):
    monkeypatch.setattr(document_index, "claim", lambda: job)
    renew = Mock(return_value=True)
    finish = Mock()
    monkeypatch.setattr(document_index, "renew", renew)
    monkeypatch.setattr(document_index, "finish", finish)
    monkeypatch.setattr(
        document_artifacts,
        "load",
        lambda r: {
            "pages": [{"page": 1, "text": "first"}, {"page": 2, "text": "second"}]
        },
    )
    monkeypatch.setattr(embedding, "encode_document", lambda text: [1.0] * 768)
    assert worker.process_one()
    assert renew.call_count == 3
    chunks = finish.call_args.kwargs["chunks"]
    assert len(chunks) == 2 and chunks[1]["page"] == 2


def test_deletion_or_lease_loss_prevents_publication(monkeypatch, job):
    monkeypatch.setattr(document_index, "claim", lambda: job)
    monkeypatch.setattr(
        document_artifacts, "load", lambda r: {"pages": [{"page": 1, "text": "first"}]}
    )
    monkeypatch.setattr(document_index, "renew", lambda j: False)
    encode = Mock()
    finish = Mock()
    monkeypatch.setattr(embedding, "encode_document", encode)
    monkeypatch.setattr(document_index, "finish", finish)
    worker.process_one()
    encode.assert_not_called()
    finish.assert_not_called()


def test_provider_failure_marks_job_failed_without_partial_index(monkeypatch, job):
    monkeypatch.setattr(document_index, "claim", lambda: job)
    monkeypatch.setattr(document_index, "renew", lambda j: True)
    monkeypatch.setattr(
        document_artifacts, "load", lambda r: {"pages": [{"page": 1, "text": "first"}]}
    )
    monkeypatch.setattr(
        embedding, "encode_document", Mock(side_effect=EmbeddingError("unavailable"))
    )
    finish = Mock()
    monkeypatch.setattr(document_index, "finish", finish)
    worker.process_one()
    assert finish.call_args.kwargs == {"error": "indexing_failed"}


def test_contract_change_fails_before_read_or_inference(monkeypatch, job):
    job["contract"] = "old"
    monkeypatch.setattr(document_index, "claim", lambda: job)
    load = Mock()
    finish = Mock()
    monkeypatch.setattr(document_artifacts, "load", load)
    monkeypatch.setattr(document_index, "finish", finish)
    worker.process_one()
    load.assert_not_called()
    assert finish.call_args.kwargs["error"] == "contract_changed"


def test_search_is_owner_contract_and_artifact_scoped(monkeypatch, enabled):
    query = Mock(return_value=[])
    monkeypatch.setattr(document_index.lakebase, "run_query", query)
    document_index.search("owner", [0.1, 0.2], service.contract(), 4, DOC)
    sql, args = query.call_args.args
    assert "c.user_id=%s AND d.user_id=%s" in sql
    assert "d.status='ready'" in sql and "j.status='ready'" in sql
    assert "c.artifact_hash=d.artifact_hash" in sql and "j.contract=c.contract" in sql
    assert args[1:3] == ("owner", "owner") and DOC in args


def test_index_queue_reserves_capacity_and_deduplicates(monkeypatch, job):
    row = dict(job, index_bytes=0)
    cur = cursor_repo(monkeypatch, [row, None, {"total": 0, "personal": 0}])
    assert (
        document_index.queue("owner", DOC, job["contract"], upload_config.LIMITS)[
            "status"
        ]
        == "queued"
    )
    assert any(
        "private-upload-capacity" in c.args[0] for c in cur.execute.call_args_list
    )
    assert any("index_bytes=%s" in c.args[0] for c in cur.execute.call_args_list)
    cur = cursor_repo(
        monkeypatch,
        [
            row,
            {"contract": job["contract"], "artifact_hash": "hash", "status": "ready"},
        ],
    )
    assert (
        document_index.queue("owner", DOC, job["contract"], upload_config.LIMITS)[
            "status"
        ]
        == "ready"
    )
    assert not any("INSERT" in c.args[0] for c in cur.execute.call_args_list)


def test_full_capacity_refuses_index_before_job_write(monkeypatch, job):
    cur = cursor_repo(
        monkeypatch,
        [
            dict(job, index_bytes=0),
            None,
            {"total": upload_config.LIMITS.deployment_bytes, "personal": 0},
        ],
    )
    with pytest.raises(ValidationError):
        document_index.queue("owner", DOC, job["contract"], upload_config.LIMITS)
    assert not any("INSERT" in c.args[0] for c in cur.execute.call_args_list)


def test_stale_worker_cannot_publish_or_resurrect_deleted_file(monkeypatch, job):
    cur = cursor_repo(monkeypatch, [None])
    assert document_index.finish(job, chunks=[]) is False
    assert not any("INSERT" in c.args[0] for c in cur.execute.call_args_list)


def test_failure_releases_index_reservation_but_not_original_bytes(monkeypatch, job):
    cur = cursor_repo(monkeypatch, [job])
    document_index.finish(job, error="failure")
    sql = [c.args[0] for c in cur.execute.call_args_list]
    assert any("reserved_bytes=reserved_bytes-index_bytes+%s" in q for q in sql)
    assert not any("reserved_bytes=0" in q for q in sql)


def test_only_selected_file_semantic_reads_fallback_when_provider_fails(
    monkeypatch, enabled
):
    monkeypatch.setattr(
        document_index,
        "state",
        lambda *a: {"status": "ready", "contract": service.contract()},
    )
    monkeypatch.setattr(service, "search", Mock(side_effect=EmbeddingError("down")))
    assert service.selected_passages("owner", DOC, "query") is None


def test_anonymous_index_search_and_document_options_are_denied(anon_client, enabled):
    assert anon_client.post("/documents/" + DOC + "/index", json={}).status_code == 403
    assert anon_client.get("/documents/search?q=hello").status_code == 403
    assert anon_client.get("/documents/options").status_code == 403


def test_empty_semantic_query_is_not_metered(client, enabled, monkeypatch):
    from services import quota_service

    check = Mock()
    monkeypatch.setattr(quota_service, "check_and_consume", check)
    assert client.get("/documents/search?q=").status_code == 400
    check.assert_not_called()


def test_failed_job_cooldown_blocks_repeated_provider_calls(monkeypatch, job):
    cur = cursor_repo(
        monkeypatch,
        [
            dict(job, index_bytes=0),
            {
                "contract": job["contract"],
                "artifact_hash": "hash",
                "status": "failed",
                "cooling": True,
            },
        ],
    )
    with pytest.raises(ValidationError, match="one hour"):
        document_index.queue("owner", DOC, job["contract"], upload_config.LIMITS)
    assert not any("INSERT" in c.args[0] for c in cur.execute.call_args_list)


def test_worker_renewal_cannot_cross_owner_or_deleted_document(monkeypatch, job):
    query = Mock(return_value=0)
    monkeypatch.setattr(document_index.lakebase, "run_write", query)
    assert document_index.renew(job) is False
    sql, args = query.call_args.args
    assert "d.user_id=%s" in sql and "d.status='ready'" in sql
    assert args == (DOC, "lease", "owner")


def test_successful_index_publication_updates_bytes_and_ready_atomically(
    monkeypatch, job
):
    cur = cursor_repo(monkeypatch, [job])
    assert document_index.finish(
        job,
        chunks=[{"page": 2, "offset": 100, "text": "evidence", "vector": [0.1] * 768}],
    )
    sql = [c.args[0] for c in cur.execute.call_args_list]
    assert any("INSERT INTO document_chunks" in q for q in sql)
    assert any("reserved_bytes=reserved_bytes-index_bytes+%s" in q for q in sql)
    assert cur.execute.call_args.args[1][0] == "ready"
