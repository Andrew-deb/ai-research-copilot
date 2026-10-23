"""Probe contracts without torch, cloud credentials or model/network calls."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "embedding_service"


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def app(monkeypatch):
    runtime = module("runtime")
    monkeypatch.setitem(sys.modules, "runtime", runtime)
    encoder = SimpleNamespace(
        state="ready",
        load_seconds=1,
        lock=threading.Lock(),
        encode=lambda text, kind: [1.0] + [0.0] * 767,
    )
    return module("server").create_app(encoder, "snapshot"), encoder


def test_api_contract_and_readiness(app):
    app, encoder = app
    client = app.test_client()
    assert client.get("/api/ready").json["version"] == "snapshot"
    response = client.post("/api/embed", json={"text": "hello", "kind": "query"})
    assert len(response.json["embedding"]) == 768
    assert response.headers["Cache-Control"] == "no-store"
    encoder.state = "failed"
    assert client.get("/api/ready").status_code == 503
    assert (
        client.post("/api/embed", json={"text": "hi", "kind": "query"}).status_code
        == 503
    )
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"text": "", "kind": "query"},
        {"text": "hi", "kind": "other"},
        {"text": "x" * 16001, "kind": "query"},
        {"text": "hi", "kind": "query", "path": "/private"},
    ],
)
def test_bad_input(app, payload):
    assert app[0].test_client().post("/api/embed", json=payload).status_code == 400


def test_busy_does_not_queue(app):
    app, encoder = app
    encoder.lock.acquire()
    try:
        response = app.test_client().post(
            "/api/embed", json={"text": "hi", "kind": "document"}
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
    finally:
        encoder.lock.release()


def test_failed_inference_releases_admission(app):
    app, encoder = app

    def fail(*args):
        raise RuntimeError("private input must never appear in response")

    encoder.encode = fail
    response = app.test_client().post(
        "/api/embed", json={"text": "hi", "kind": "query"}
    )
    assert response.status_code == 503
    assert "private" not in response.get_data(as_text=True)
    assert not encoder.lock.locked()


def test_prefix_token_limit_and_vector_validation():
    runtime = module("runtime")
    seen = []

    class Model:
        max_seq_length = 8
        tokenizer = staticmethod(
            lambda text, **kw: {"input_ids": list(range(len(text.split())))}
        )

        def encode(self, text, **kwargs):
            seen.append((text, kwargs))
            return SimpleNamespace(tolist=lambda: [1.0] + [0.0] * 767)

    encoder = runtime.Encoder()
    encoder.model = Model()
    encoder.encode(" hello ", "document")
    assert seen == [("search_document: hello", {"normalize_embeddings": True})]
    with pytest.raises(ValueError, match="token limit"):
        encoder.encode("word " * 9, "query")
    encoder.model.encode = lambda *a, **k: SimpleNamespace(
        tolist=lambda: [float("nan")] * 768
    )
    with pytest.raises(ValueError, match="Invalid"):
        encoder.encode("hello", "query")


def test_snapshot_staging_contract_and_fingerprint(tmp_path):
    stage = module("staging")
    contract = {
        "base_model": "nomic-ai/modernbert-embed-base",
        "embedding_dim": 768,
        "query_prefix": "search_query: ",
        "document_prefix": "search_document: ",
        "normalize": True,
    }
    files = {
        name: b"{}"
        for name in (
            "config.json",
            "modules.json",
            "tokenizer.json",
            "model.safetensors",
            "1_Pooling/config.json",
        )
    }
    files["embedding_contract.json"] = json.dumps(contract).encode()
    source = "/Volumes/catalog/schema/models/model"

    def listing(directory):
        return [
            SimpleNamespace(path=source + "/" + name, is_directory=False)
            for name in files
        ]

    client = SimpleNamespace(
        files=SimpleNamespace(
            list_directory_contents=listing,
            download=lambda path: SimpleNamespace(
                contents=io.BytesIO(files[path[len(source) + 1 :]])
            ),
        )
    )
    fingerprint = stage.stage_model(source, tmp_path / "one", client)
    assert fingerprint == stage.stage_model(source, tmp_path / "two", client)
    files["config.json"] = b"changed"
    assert fingerprint != stage.stage_model(source, tmp_path / "three", client)
    files["embedding_contract.json"] = b"{}"
    with pytest.raises(ValueError, match="contract"):
        stage.stage_model(source, tmp_path / "bad", client)


def test_numerical_comparison_rejects_incompatible_vectors(monkeypatch):
    monkeypatch.setitem(sys.modules, "runtime", module("runtime"))
    probe = module("probe")
    vector = [1.0] + [0.0] * 767
    assert probe.compare(vector, vector)["passed"]
    assert not probe.compare(vector, [0.0, 1.0] + [0.0] * 766)["passed"]
    with pytest.raises(ValueError):
        probe.compare(vector, [float("nan")] * 768)


def test_payload_limit(app):
    response = (
        app[0]
        .test_client()
        .post("/api/embed", data=b"x" * 65537, content_type="application/json")
    )
    assert response.status_code == 413


def test_staging_rejects_escape_and_byte_overflow(tmp_path, monkeypatch):
    stage = module("staging")
    source = "/Volumes/catalog/schema/volume/model"
    entries = [SimpleNamespace(path=source + "/../secret.json", is_directory=False)]
    client = SimpleNamespace(
        files=SimpleNamespace(
            list_directory_contents=lambda path: entries,
            download=lambda path: SimpleNamespace(contents=io.BytesIO(b"123")),
        )
    )
    with pytest.raises(ValueError, match="path"):
        stage.stage_model(source, tmp_path / "escape", client)
    entries[0] = SimpleNamespace(path=source + "/config.json", is_directory=False)
    monkeypatch.setattr(stage, "MAX_BYTES", 2)
    with pytest.raises(ValueError, match="limit"):
        stage.stage_model(source, tmp_path / "oversize", client)


def test_notebook_oauth_exchange_scope_cache_and_refresh():
    auth_module = module("notebook_auth")
    calls = []
    now = [0]
    client = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.example/"),
        apps=SimpleNamespace(
            get=lambda name: SimpleNamespace(oauth2_app_client_id="app-audience")
        ),
    )

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"access_token": "private-oauth", "expires_in": 3600},
        )

    auth = auth_module.NotebookAppAuth(
        client,
        "probe",
        lambda: "private-notebook",
        session=SimpleNamespace(post=post),
        clock=lambda: now[0],
    )
    assert auth.authenticate() == {"Authorization": "Bearer private-oauth"}
    auth.authenticate()
    assert len(calls) == 1
    url, request = calls[0]
    assert url == "https://workspace.example/oidc/v1/token"
    assert request["data"]["audience"] == "app-audience"
    assert request["data"]["subject_token"] == "private-notebook"
    assert request["allow_redirects"] is False
    assert request["timeout"] == 30
    now[0] = 3540
    auth.authenticate()
    assert len(calls) == 2


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_notebook_oauth_failure_does_not_expose_response(status):
    auth_module = module("notebook_auth")
    client = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.example"),
        apps=SimpleNamespace(
            get=lambda name: SimpleNamespace(oauth2_app_client_id="audience")
        ),
    )
    auth = auth_module.NotebookAppAuth(
        client,
        "probe",
        lambda: "secret",
        session=SimpleNamespace(
            post=lambda *a, **k: SimpleNamespace(status_code=status, text="secret")
        ),
    )
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as failure:
        auth.authenticate()
    assert "secret" not in str(failure.value)


def test_notebook_probe_uses_supplied_auth(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "runtime", module("runtime"))
    probe = module("probe")
    monkeypatch.setattr(probe, "FIXTURES", [("query", "example")])

    class Encoder:
        load_seconds = 0.1

        def load(self, path):
            pass

        def encode(self, text, kind):
            return [1.0] + [0.0] * 767

    monkeypatch.setattr(probe, "Encoder", Encoder)
    seen = []

    def response(*args, **kwargs):
        seen.append(kwargs["headers"])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "state": "ready",
                "version": "snapshot",
                "embedding": [1.0] + [0.0] * 767,
            },
        )

    monkeypatch.setattr(
        probe.requests, "Session", lambda: SimpleNamespace(get=response, post=response)
    )
    probe.main(
        [
            "--model-path",
            "unused",
            "--url",
            "https://app.example",
            "--version",
            "snapshot",
            "--output",
            str(tmp_path / "report.json"),
        ],
        auth=SimpleNamespace(
            authenticate=lambda: {"Authorization": "Bearer app-oauth"}
        ),
    )
    assert len(seen) == 7
    assert all(headers == {"Authorization": "Bearer app-oauth"} for headers in seen)
