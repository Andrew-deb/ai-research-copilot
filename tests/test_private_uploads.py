"""Private byte lifecycle: ownership, bounded parsing, leases and provider errors."""

import io
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
from werkzeug.datastructures import FileStorage
import upload_config
from exceptions import ValidationError, UploadNotFoundError, ExternalAPIError
from services import upload_service as service, upload_worker as worker, upload_storage
from repositories import uploads
from tests.conftest import DEV_EMAIL
from mcp_server.shared_resource.storage.azure_blob import AzureBlobStorage
from mcp_server.shared_resource.storage.contract import StorageUnavailable


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(upload_config, "ENABLED", True)
    monkeypatch.setattr(upload_config, "WORKER_ENABLED", False)
    monkeypatch.setattr(uploads, "release_upload", Mock())


@pytest.mark.parametrize(
    "filename,data",
    [
        ("x.exe", b"x"),
        ("x.txt", b"\x00binary"),
        ("x.md", b"\xff"),
        ("x.pdf", b"not pdf"),
        ("../x.txt", b"x"),
        ("x.txt", b""),
        ("x.txt", b"\x01binary"),
    ],
)
def test_invalid_files_fail_before_storage(filename, data):
    with pytest.raises(ValidationError):
        service.validate_file(filename, data)


def test_extension_does_not_bypass_actual_size():
    with pytest.raises(ValidationError):
        service.validate_file("x.txt", b"x" * (upload_config.LIMITS.max_file_bytes + 1))


def test_foreign_conversation_never_reads_file_or_reserves(monkeypatch, enabled):
    monkeypatch.setattr(service.lakebase, "get_conversation", Mock(return_value=None))
    reserve = Mock()
    monkeypatch.setattr(uploads, "reserve", reserve)
    file = Mock()
    with pytest.raises(UploadNotFoundError):
        service.upload("owner", str(uuid4()), file)
    file.stream.read.assert_not_called()
    reserve.assert_not_called()


def test_upload_reserves_then_stores_then_queues(monkeypatch, enabled):
    order = []
    cid = str(uuid4())
    repo = {}
    monkeypatch.setattr(
        service.lakebase,
        "get_conversation",
        Mock(return_value={"conversation_id": cid}),
    )
    storage = Mock()
    storage.put.side_effect = lambda *a: order.append("put")
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)

    def reserve(owner, conversation, record, limits):
        order.append("reserve")
        repo.update(record)
        return {
            **record,
            "conversation_id": conversation,
            "status": "uploading",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }

    monkeypatch.setattr(uploads, "reserve", reserve)
    monkeypatch.setattr(uploads, "queue_extraction", lambda *a: order.append("queue"))
    result = service.upload(
        "owner", cid, FileStorage(io.BytesIO(b"Private text"), filename="source.txt")
    )
    assert order == ["reserve", "put", "queue"] and result["status"] == "queued"
    assert repo["content_hash"] == hashlib.sha256(b"Private text").hexdigest()
    assert (
        "storage_location_id" not in result
        and "object_key" not in result
        and result["agent_access"] is False
    )


def test_ambiguous_storage_failure_keeps_durable_cleanup_record(monkeypatch, enabled):
    cid = str(uuid4())
    doc = str(uuid4())
    monkeypatch.setattr(
        service.lakebase,
        "get_conversation",
        Mock(return_value={"conversation_id": cid}),
    )
    storage = Mock()
    storage.put.side_effect = StorageUnavailable("secret")
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    monkeypatch.setattr(service, "uuid4", lambda: doc)
    monkeypatch.setattr(
        uploads, "reserve", lambda *a: {"document_id": doc, "object_key": "original"}
    )
    delete = Mock()
    queue = Mock()
    monkeypatch.setattr(uploads, "request_delete", delete)
    monkeypatch.setattr(uploads, "queue_extraction", queue)
    with pytest.raises(ExternalAPIError) as error:
        service.upload(
            "owner", cid, FileStorage(io.BytesIO(b"Private"), filename="source.txt")
        )
    assert "secret" not in str(error.value)
    delete.assert_called_once_with("owner", doc)
    queue.assert_not_called()


@pytest.mark.parametrize(
    "status,expired", [("deleting", False), ("deleted", False), ("ready", True)]
)
def test_deleted_or_expired_files_cannot_be_downloaded(monkeypatch, status, expired):
    doc = str(uuid4())
    provider = Mock()
    monkeypatch.setattr(
        uploads, "get_owned", Mock(return_value={"status": status, "expired": expired})
    )
    monkeypatch.setattr(upload_storage, "for_location", provider)
    with pytest.raises(ValidationError):
        service.download("owner", doc)
    provider.assert_not_called()


def test_foreign_file_is_not_downloadable(monkeypatch):
    monkeypatch.setattr(uploads, "get_owned", Mock(return_value=None))
    with pytest.raises(UploadNotFoundError):
        service.download("other", str(uuid4()))


def text_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Private paper text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def test_real_bounded_pdf_subprocess_preserves_page_provenance():
    result, error = worker.extract_bytes(text_pdf(), "application/pdf")
    assert error is None and result["version"] == "text-v1"
    assert (
        result["pages"][0]["page"] == 1
        and "Private paper text" in result["pages"][0]["text"]
    )


def test_text_extraction_and_invalid_text_subprocess():
    result, error = worker.extract_bytes(b"# Notes\nPrivate content", "text/markdown")
    assert error is None and result["pages"][0]["text"].startswith("# Notes")
    result, error = worker.extract_bytes(b"\xff", "text/plain")
    assert result is None and error == "invalid_text"


def test_blank_scanned_pdf_does_not_claim_ready():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(300, 300)
    data = io.BytesIO()
    writer.write(data)
    assert worker.extract_bytes(data.getvalue(), "application/pdf") == (
        None,
        "no_extractable_text",
    )


def job(action="extract", attempts=1):
    return {
        "document_id": str(uuid4()),
        "action": action,
        "attempts": attempts,
        "lease_token": str(uuid4()),
        "storage_location_id": "azure-primary",
        "object_key": "original",
        "artifact_key": "derived",
        "media_type": "text/plain",
        "content_hash": hashlib.sha256(b"Private content").hexdigest(),
    }


def test_worker_commits_artifact_only_after_storage_success(monkeypatch):
    item = job()
    storage = Mock()
    storage.read.return_value = b"Private content"
    monkeypatch.setattr(uploads, "claim", lambda: item)
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    finish = Mock()
    monkeypatch.setattr(uploads, "finish", finish)
    assert worker.process_one()
    data = storage.put.call_args.args[1]
    assert b"Private content" in data
    result = finish.call_args.kwargs["result"]
    assert (
        result["artifact_hash"] == hashlib.sha256(data).hexdigest()
        and result["page_count"] == 1
    )


def test_worker_storage_failure_does_not_claim_ready(monkeypatch):
    item = job()
    storage = Mock()
    storage.read.side_effect = StorageUnavailable("safe")
    monkeypatch.setattr(uploads, "claim", lambda: item)
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    finish = Mock()
    monkeypatch.setattr(uploads, "finish", finish)
    worker.process_one()
    finish.assert_called_once_with(item, error="storage_unavailable")
    storage.put.assert_not_called()


def test_worker_deletes_both_objects_before_releasing_capacity(monkeypatch):
    item = job("delete")
    order = []
    storage = Mock()
    storage.delete.side_effect = lambda key: order.append(key)
    monkeypatch.setattr(uploads, "claim", lambda: item)
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    monkeypatch.setattr(uploads, "finish", lambda *a, **k: order.append("release"))
    worker.process_one()
    assert order == ["original", "derived", "release"]


def cursor_repo(monkeypatch, one_results):
    cursor = Mock()
    cursor.fetchone.side_effect = one_results
    conn = Mock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=False)

    @contextmanager
    def get_connection():
        yield conn

    monkeypatch.setattr(uploads.lakebase, "get_connection", get_connection)
    return cursor


def test_capacity_is_locked_and_checked_before_insert(monkeypatch):
    cur = cursor_repo(
        monkeypatch,
        [
            {"conversation_id": "cid"},
            {"total": upload_config.LIMITS.deployment_bytes, "personal": 0, "files": 0},
        ],
    )
    with pytest.raises(ValidationError):
        uploads.reserve("owner", "cid", {"original_bytes": 10}, upload_config.LIMITS)
    sql = [c.args[0] for c in cur.execute.call_args_list]
    assert "pg_advisory_xact_lock" in sql[0] and "user_id=%s FOR UPDATE" in sql[1]
    assert not any("INSERT" in s for s in sql)


def test_lease_claim_is_durable_and_skips_locked_jobs(monkeypatch):
    item = job()
    cur = cursor_repo(monkeypatch, [{"locked": True}, item])
    cur.fetchall.return_value = [{"document_id": item["document_id"]}]
    result = uploads.claim()
    assert result["attempts"] == 2 and result["lease_token"] != item["lease_token"]
    sql = [c.args[0] for c in cur.execute.call_args_list]
    assert (
        any("SKIP LOCKED" in query for query in sql)
        and "j.lease_until<=now()" in sql[0]
    )
    assert "d.upload_lease_until<=now()" in sql[0]
    assert any("interval '5 minutes'" in s for s in sql)


def test_stale_worker_cannot_finalize_and_deletion_wins(monkeypatch):
    item = job()
    cur = cursor_repo(monkeypatch, [None])
    assert uploads.finish(item, result={}) is False
    assert len(cur.execute.call_args_list) == 2
    cur = cursor_repo(monkeypatch, [("delete",)])
    assert uploads.finish(item, result={}) is False
    assert not any("status='ready'" in c.args[0] for c in cur.execute.call_args_list)


def test_azure_rejects_public_container_before_upload():
    provider = AzureBlobStorage.__new__(AzureBlobStorage)
    provider.container = Mock()
    provider.container.get_container_properties.return_value = {"public_access": "blob"}
    with pytest.raises(StorageUnavailable):
        provider.put("key", b"content", "text/plain")
    provider.container.get_blob_client.assert_not_called()


def test_azure_read_bounds_changed_objects_and_sanitizes_errors():
    provider = AzureBlobStorage.__new__(AzureBlobStorage)
    provider.container = Mock()
    provider.container.get_container_properties.return_value = {"public_access": None}
    blob = provider.container.get_blob_client.return_value
    blob.get_blob_properties.return_value.size = 3
    blob.download_blob.return_value.readall.return_value = b"oversized"
    with pytest.raises(StorageUnavailable):
        provider.read("key", 3)
    assert blob.download_blob.call_args.kwargs["length"] == 4
    blob.upload_blob.side_effect = RuntimeError("client-secret")
    with pytest.raises(StorageUnavailable) as error:
        provider.put("key", b"x", "text/plain")
    assert "client-secret" not in str(error.value)


def test_anonymous_upload_endpoints_are_forbidden(anon_client, enabled):
    assert (
        anon_client.post(
            "/uploads/conversations", json={"mode": "research"}
        ).status_code
        == 403
    )
    assert (
        anon_client.post(
            "/uploads", data={}, headers={"Accept": "application/json"}
        ).status_code
        == 403
    )


def test_upload_foundation_creates_owned_conversation_without_ai_request(
    client, db, enabled
):
    response = client.post("/uploads/conversations", json={"mode": "wick"})
    assert response.status_code == 201
    owner = db.get_or_create_user(DEV_EMAIL)
    saved = db.get_conversation(owner["user_id"], response.json["conversation_id"])
    assert saved["origin"] == "assistant"


def test_download_is_attachment_nostore_and_owned(client, db, enabled, monkeypatch):
    doc = str(uuid4())
    download = Mock(return_value=({"filename": "private.md"}, b"private"))
    monkeypatch.setattr(service, "download", download)
    response = client.get("/uploads/" + doc + "/download")
    assert (
        response.status_code == 200
        and response.headers["Cache-Control"] == "private, no-store"
    )
    assert (
        "attachment" in response.headers["Content-Disposition"]
        and response.headers["X-Content-Type-Options"] == "nosniff"
    )
    download.assert_called_once_with(db.get_or_create_user(DEV_EMAIL)["user_id"], doc)


def test_encrypted_pdf_is_not_processed():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(300, 300)
    writer.encrypt("password")
    data = io.BytesIO()
    writer.write(data)
    assert worker.extract_bytes(data.getvalue(), "application/pdf") == (
        None,
        "encrypted_pdf",
    )


def test_parser_timeout_does_not_inherit_credentials(monkeypatch):
    import subprocess

    def timeout(args, **kwargs):
        assert "AZURE_CLIENT_SECRET" not in kwargs["env"]
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(worker.subprocess, "run", timeout)
    assert worker.extract_bytes(b"Private", "text/plain") == (None, "extraction_limit")


def test_failed_deletion_keeps_capacity_reserved_for_retry(monkeypatch):
    item = job("delete")
    cur = cursor_repo(monkeypatch, [("delete",)])
    assert uploads.finish(item, error="storage_unavailable") is False
    sql = [c.args[0] for c in cur.execute.call_args_list]
    assert any("interval '1 minute'" in s for s in sql)
    assert not any("reserved_bytes=0" in s for s in sql)


def test_cleanup_retains_locators_for_deleted_accounts_and_abandoned_uploads(
    monkeypatch,
):
    cur = cursor_repo(monkeypatch, [])
    cur.fetchall.return_value = []
    uploads.enqueue_cleanup()
    sql = cur.execute.call_args.args[0]
    assert "user_id IS NULL" in sql and "conversation_id IS NULL" in sql
    assert "interval '5 minutes'" in sql and "expires_at<=now()" in sql


def test_download_detects_integrity_mismatch(monkeypatch):
    doc = str(uuid4())
    row = {
        "status": "ready",
        "expired": False,
        "storage_location_id": "azure-primary",
        "object_key": "original",
        "content_hash": hashlib.sha256(b"original").hexdigest(),
    }
    monkeypatch.setattr(uploads, "get_owned", Mock(return_value=row))
    storage = Mock()
    storage.read.return_value = b"changed"
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    with pytest.raises(ExternalAPIError, match="integrity"):
        service.download("owner", doc)


def test_migration_preserves_orphan_cleanup_metadata():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "sql/29_private_uploads.sql"
    ).read_text()
    assert source.count("ON DELETE SET NULL") == 2
    assert (
        "storage_location_id TEXT NOT NULL" in source
        and "reserved_bytes >= 0" in source
    )


def test_multipart_upload_is_owned_and_never_consumes_ai_quota(
    client, db, enabled, monkeypatch
):
    from services import quota_service

    owner = db.get_or_create_user(DEV_EMAIL)
    conversation = db.create_conversation(owner["user_id"], "Files")
    storage = Mock()
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    saved = []

    def reserve(user, cid, record, limits):
        saved.append((user, cid, record))
        return {
            **record,
            "conversation_id": cid,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }

    monkeypatch.setattr(uploads, "reserve", reserve)
    queue = Mock()
    monkeypatch.setattr(uploads, "queue_extraction", queue)
    quota = Mock()
    monkeypatch.setattr(quota_service, "check_and_consume", quota)
    response = client.post(
        "/uploads",
        data={
            "conversation_id": conversation["conversation_id"],
            "file": (io.BytesIO(b"Private text"), "notes.md"),
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 202 and response.json["status"] == "queued"
    assert (
        saved[0][0] == owner["user_id"]
        and saved[0][1] == conversation["conversation_id"]
    )
    assert storage.put.call_args.args[1] == b"Private text"
    quota.assert_not_called()
    queue.assert_called_once()


def test_upload_requires_csrf_before_any_storage(app, client, enabled, monkeypatch):
    upload = Mock()
    monkeypatch.setattr(service, "upload", upload)
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post(
        "/uploads",
        data={"file": (io.BytesIO(b"Private"), "notes.txt")},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    upload.assert_not_called()


def test_live_upload_lease_blocks_cleanup_claim(monkeypatch):
    cur = cursor_repo(monkeypatch, [])
    cur.fetchall.return_value = []
    assert uploads.claim() is None
    assert (
        "d.upload_lease_until IS NULL OR d.upload_lease_until<=now()"
        in cur.execute.call_args.args[0]
    )


def test_upload_lease_is_released_when_provider_write_fails(
    client, db, enabled, monkeypatch
):
    owner = db.get_or_create_user(DEV_EMAIL)
    conversation = db.create_conversation(owner["user_id"], "Files")
    storage = Mock()
    storage.put.side_effect = StorageUnavailable("safe")
    monkeypatch.setattr(upload_storage, "for_location", lambda *a: storage)
    monkeypatch.setattr(uploads, "reserve", lambda user, cid, record, limits: record)
    monkeypatch.setattr(uploads, "request_delete", Mock())
    release = Mock()
    monkeypatch.setattr(uploads, "release_upload", release)
    with pytest.raises(ExternalAPIError):
        service.upload(
            owner["user_id"],
            conversation["conversation_id"],
            FileStorage(io.BytesIO(b"Private"), "notes.txt"),
        )
    assert release.call_count == 1 and release.call_args.args[0] == owner["user_id"]
