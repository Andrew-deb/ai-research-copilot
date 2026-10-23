"""Dedicated embedding transport, OAuth boundary and graceful failures; offline."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import config
import embedding
import embedding_databricks as backend
from exceptions import EmbeddingServiceError, ValidationError

VERSION = "a" * 64
VECTOR = [1.0] + [0.0] * 767


def response(payload=None, status=200, headers=None):
    return SimpleNamespace(
        status_code=status, headers=headers or {}, json=lambda: payload
    )


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(
        config,
        "DATABRICKS_EMBEDDING_URL",
        "https://embedding.example.databricksapps.com",
    )
    monkeypatch.setattr(config, "DATABRICKS_EMBEDDING_VERSION", VERSION)
    monkeypatch.setattr(config, "DATABRICKS_EMBEDDING_TIMEOUT_SECONDS", "15")
    monkeypatch.setattr(config, "DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setattr(config, "DATABRICKS_CLIENT_ID", "caller")
    monkeypatch.setattr(config, "DATABRICKS_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        backend,
        "_auth",
        SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer private"}),
    )
    monkeypatch.setattr(backend, "_ready", False)
    monkeypatch.setattr(backend.time, "sleep", Mock())


def ready():
    return {
        "state": "ready",
        "model": config.EMBEDDING_MODEL,
        "dimension": 768,
        "version": VERSION,
    }


def encoded():
    return {"embedding": VECTOR, "dimension": 768, "version": VERSION}


def test_query_and_document_dispatch_prefix_once(monkeypatch):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "databricks")
    transport = Mock(
        side_effect=[response(ready()), response(encoded()), response(encoded())]
    )
    monkeypatch.setattr(backend.requests, "request", transport)
    assert embedding.encode_query("  question ") == VECTOR
    assert embedding.encode_document(" passage ") == VECTOR
    calls = transport.call_args_list
    assert calls[0].args == ("GET", config.DATABRICKS_EMBEDDING_URL + "/api/ready")
    assert calls[1].kwargs["json"] == {"text": "question", "kind": "query"}
    assert calls[2].kwargs["json"] == {"text": "passage", "kind": "document"}
    assert all(c.kwargs["headers"]["Authorization"] == "Bearer private" for c in calls)
    assert all(c.kwargs["allow_redirects"] is False for c in calls)
    assert calls[1].kwargs["timeout"] == (5, 15.0)
    assert embedding.is_loaded()


def test_warmup_only_checks_readiness(monkeypatch):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "databricks")
    transport = Mock(return_value=response(ready()))
    monkeypatch.setattr(backend.requests, "request", transport)
    embedding.warmup()
    assert transport.call_count == 1
    assert transport.call_args.args[0] == "GET"
    assert embedding.is_loaded()


@pytest.mark.parametrize(
    "url",
    [
        "http://x.databricksapps.com",
        "https://evil.example",
        "https://x.databricksapps.com/other",
        "https://user:secret@x.databricksapps.com",
        "https://x.databricksapps.com:bad",
        "https://x.databricksapps.com?token=private",
    ],
)
def test_invalid_origin_never_sends_credentials(monkeypatch, url):
    monkeypatch.setattr(config, "DATABRICKS_EMBEDDING_URL", url)
    transport = Mock()
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError):
        backend.warmup()
    transport.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"version": "other"},
        {"model": "different"},
        {"dimension": 256},
        {"state": "loading"},
    ],
)
def test_reject_wrong_readiness(monkeypatch, patch):
    monkeypatch.setattr(
        backend.requests, "request", Mock(return_value=response({**ready(), **patch}))
    )
    with pytest.raises(EmbeddingServiceError):
        backend.encode("question", "query")
    assert not backend.is_loaded()


@pytest.mark.parametrize(
    "patch",
    [
        {"version": "other"},
        {"dimension": 256},
        {"embedding": [0.0] * 768},
        {"embedding": [float("nan")] * 768},
        {"embedding": [True] * 768},
        {"embedding": ["1"] * 768},
        {"embedding": [1.0] * 768},
        {"embedding": [1.0]},
    ],
)
def test_reject_wrong_or_malformed_vectors(monkeypatch, patch):
    monkeypatch.setattr(backend, "_ready", True)
    monkeypatch.setattr(
        backend.requests, "request", Mock(return_value=response({**encoded(), **patch}))
    )
    with pytest.raises(EmbeddingServiceError):
        backend.encode("question", "query")
    assert not backend.is_loaded()


@pytest.mark.parametrize("status", [301, 302, 401, 403, 422, 500, 503])
def test_failure_sanitizes_and_clears_readiness(monkeypatch, caplog, status):
    monkeypatch.setattr(backend, "_ready", True)
    transport = Mock(return_value=response({"error": "private body"}, status))
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError) as error:
        backend.encode("private prompt", "query")
    assert "private" not in str(error.value) + caplog.text
    assert transport.call_count == 1
    assert not backend.is_loaded()


def test_brief_busy_retry_is_bounded(monkeypatch):
    monkeypatch.setattr(backend, "_ready", True)
    transport = Mock(side_effect=[response(status=429), response(status=429)])
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError, match="busy"):
        backend.encode("question", "query")
    assert transport.call_count == 2
    backend.time.sleep.assert_called_once_with(1)


def test_long_retry_after_fails_fast(monkeypatch):
    transport = Mock(return_value=response(status=429, headers={"Retry-After": "120"}))
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError):
        backend.warmup()
    assert transport.call_count == 1
    backend.time.sleep.assert_not_called()


def test_timeout_no_retry_or_private_error(monkeypatch):
    transport = Mock(side_effect=requests.Timeout("secret host error"))
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError) as error:
        backend.warmup()
    assert "secret" not in str(error.value)
    assert transport.call_count == 1


@pytest.mark.parametrize("text", ["x" * 16001, "文" * 12000])
def test_input_bounds_before_io(monkeypatch, text):
    transport = Mock()
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError):
        backend.encode(text, "document")
    transport.assert_not_called()


def test_index_contract_bound_to_actual_backend(monkeypatch):
    import upload_config
    from services import document_indexing

    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "databricks")
    monkeypatch.setattr(upload_config, "INDEX_MODEL_VERSION", "different")
    with pytest.raises(ValidationError, match="verified"):
        document_indexing.contract()
    monkeypatch.setattr(upload_config, "INDEX_MODEL_VERSION", VERSION)
    assert VERSION in document_indexing.contract()


def test_service_failure_gets_friendly_503(client, db, monkeypatch):
    from services import search_service

    def fail(*args, **kwargs):
        raise EmbeddingServiceError(
            "Semantic search is temporarily unavailable. Please try again shortly."
        )

    monkeypatch.setattr(search_service, "semantic_search", fail)
    result = client.get(
        "/search?q=test&mode=semantic", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert result.status_code == 503
    assert "temporarily unavailable" in result.json["detail"]


def test_non_json_and_missing_credentials_fail_closed(monkeypatch):
    transport = Mock(
        return_value=SimpleNamespace(
            status_code=200, json=Mock(side_effect=ValueError("private body"))
        )
    )
    monkeypatch.setattr(backend.requests, "request", transport)
    with pytest.raises(EmbeddingServiceError):
        backend.warmup()
    transport.reset_mock()
    monkeypatch.setattr(config, "DATABRICKS_CLIENT_SECRET", "")
    with pytest.raises(EmbeddingServiceError):
        backend.warmup()
    transport.assert_not_called()


def test_factory_forces_m2m_and_reuses_provider(monkeypatch):
    import sys
    from types import ModuleType

    core = ModuleType("databricks.sdk.core")
    authenticate = Mock(return_value={"Authorization": "Bearer private"})
    core.Config = Mock(return_value=SimpleNamespace(authenticate=authenticate))
    monkeypatch.setitem(sys.modules, "databricks.sdk.core", core)
    monkeypatch.setattr(backend, "_auth", None)
    backend._headers()
    backend._headers()
    core.Config.assert_called_once_with(
        host="https://workspace.example",
        client_id="caller",
        client_secret="secret",
        auth_type="oauth-m2m",
        http_timeout_seconds=5,
        retry_timeout_seconds=5,
    )
    assert authenticate.call_count == 2


def test_fork_discards_parent_credentials_and_readiness():
    backend._ready = True
    backend._after_fork()
    assert backend._auth is None
    assert not backend.is_loaded()
    assert not backend._auth_lock.locked()
