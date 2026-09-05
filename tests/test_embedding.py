"""
tests/test_embedding.py — query-embedding backends.

The invariant these guard: a query vector must land in the same space as the
vectors the Spark pipeline stored (nomic-ai/modernbert-embed-base, 768-dim,
unit length, `search_document: `-prefixed). Both backends must satisfy that
identically, or swapping hosts silently degrades every semantic-search ranking.

The prefix is the quiet one. ModernBERT-embed is asymmetric, so a query sent
without `search_query: ` still returns a perfectly valid 768-dim unit vector -
it just sits in the wrong neighbourhood. Nothing raises; ranking simply gets
worse. Hence the explicit prefix tests below.

No network: the HTTP call is stubbed.
"""

import math

import pytest

import embedding
from config import EMBEDDING_DIMENSION, EMBEDDING_QUERY_PREFIX
from exceptions import EmbeddingError

# Read the dimension from config rather than restating it: these tests must fail
# when the model changes and the code does not, not quietly test the old number.
DIM = EMBEDDING_DIMENSION


def _unit(vector):
    return math.sqrt(sum(c * c for c in vector))


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("configured,token,expected", [
    ("local", None, "local"),
    ("local", "hf_x", "local"),        # explicit wins over the token
    ("hf_api", "hf_x", "hf_api"),
    ("auto", "hf_x", "hf_api"),        # auto -> api when a token exists
    ("auto", None, "local"),           # auto -> local otherwise
    ("nonsense", None, "local"),       # unknown value falls back safely
])
def test_active_backend_resolution(monkeypatch, configured, token, expected):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", configured)
    monkeypatch.setattr(embedding, "HF_API_TOKEN", token)
    assert embedding.active_backend() == expected


# ---------------------------------------------------------------------------
# Normalisation — the compatibility guarantee
# ---------------------------------------------------------------------------

def test_vectors_are_unit_length():
    normalized = embedding._l2_normalize([3.0, 4.0])
    assert normalized == pytest.approx([0.6, 0.8])
    assert _unit(normalized) == pytest.approx(1.0)


def test_zero_vector_is_rejected():
    with pytest.raises(EmbeddingError):
        embedding._l2_normalize([0.0] * DIM)


def test_wrong_dimension_is_rejected():
    """A different model would rank plausibly but wrongly — fail loudly instead."""
    with pytest.raises(EmbeddingError, match="1536"):
        embedding._validate([0.1] * 1536)


def test_validate_normalizes_even_when_backend_did_not():
    out = embedding._validate([2.0] * DIM)
    assert _unit(out) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Response shapes the Inference API can return
# ---------------------------------------------------------------------------

def test_flat_sentence_vector():
    assert embedding._flatten_token_embeddings([1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]


def test_token_embeddings_are_mean_pooled():
    # [tokens][dims] -> column-wise mean, matching the model's pooling layer
    assert embedding._flatten_token_embeddings([[1.0, 3.0], [3.0, 5.0]]) == [2.0, 4.0]


def test_batch_wrapped_token_embeddings_are_unwrapped():
    # [1][tokens][dims]
    assert embedding._flatten_token_embeddings([[[1.0, 3.0], [3.0, 5.0]]]) == [2.0, 4.0]


@pytest.mark.parametrize("payload", [[], {}, "text", [[]]])
def test_unusable_shapes_raise(payload):
    with pytest.raises(EmbeddingError):
        embedding._flatten_token_embeddings(payload)


# ---------------------------------------------------------------------------
# hf_api backend
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_hf_api_returns_unit_vector(monkeypatch):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "hf_api")
    monkeypatch.setattr(embedding, "HF_API_TOKEN", "hf_test")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _Resp([float(i % 7) + 1.0 for i in range(DIM)])

    monkeypatch.setattr(embedding.requests, "post", fake_post)

    vector = embedding.encode_query("  transformer attention  ")
    assert len(vector) == DIM
    assert _unit(vector) == pytest.approx(1.0)
    # Trimmed, and prefixed for the asymmetric model.
    assert captured["json"] == {"inputs": "search_query: transformer attention"}
    assert captured["headers"]["Authorization"] == "Bearer hf_test"
    assert captured["headers"]["x-wait-for-model"] == "true"         # survive cold starts


def test_hf_api_without_token_explains_itself(monkeypatch):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "hf_api")
    monkeypatch.setattr(embedding, "HF_API_TOKEN", None)
    with pytest.raises(EmbeddingError, match="HF_API_TOKEN"):
        embedding.encode_query("anything")


def test_hf_api_surfaces_endpoint_errors(monkeypatch):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "hf_api")
    monkeypatch.setattr(embedding, "HF_API_TOKEN", "hf_test")
    monkeypatch.setattr(embedding.requests, "post",
                        lambda *a, **k: _Resp({"error": "Model is overloaded"}))
    with pytest.raises(EmbeddingError, match="overloaded"):
        embedding.encode_query("anything")


def test_network_failure_becomes_a_domain_error(monkeypatch):
    """Routes must see EmbeddingError, not a bare requests exception."""
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "hf_api")
    monkeypatch.setattr(embedding, "HF_API_TOKEN", "hf_test")

    def boom(*a, **k):
        raise embedding.requests.ConnectionError("dns failure")

    monkeypatch.setattr(embedding.requests, "post", boom)
    with pytest.raises(EmbeddingError, match="Embedding request failed"):
        embedding.encode_query("anything")


def test_empty_query_rejected():
    with pytest.raises(EmbeddingError):
        embedding.encode_query("   ")


def test_warmup_never_raises(monkeypatch):
    """A dead embedding backend must not stop the app from booting."""
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "hf_api")
    monkeypatch.setattr(embedding, "HF_API_TOKEN", "hf_test")
    monkeypatch.setattr(embedding.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    embedding.warmup()   # must not raise


# ---------------------------------------------------------------------------
# The asymmetric-prefix trap
# ---------------------------------------------------------------------------

def _capturing_backend(monkeypatch, seen: list):
    monkeypatch.setattr(embedding, "EMBEDDING_BACKEND", "hf_api")
    monkeypatch.setattr(embedding, "HF_API_TOKEN", "hf_test")

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(json["inputs"])
        # Make the vector depend on the text, so identical inputs are the only
        # way two calls can produce identical vectors.
        seed = sum(ord(c) for c in json["inputs"])
        return _Resp([float((seed + i) % 7) + 1.0 for i in range(DIM)])

    monkeypatch.setattr(embedding.requests, "post", fake_post)


def test_query_is_prefixed_before_it_reaches_the_backend(monkeypatch):
    """
    The prefix is applied in encode_query, not at the call sites, so every
    caller gets it whether or not they know it exists.
    """
    seen: list = []
    _capturing_backend(monkeypatch, seen)
    embedding.encode_query("graph neural networks")
    assert seen == [EMBEDDING_QUERY_PREFIX + "graph neural networks"]


def test_prefixed_and_unprefixed_queries_differ(monkeypatch):
    """
    §1.6's trap: embed the same sentence with and without `search_query: ` and
    confirm the vectors differ. Identical vectors would mean the prefix never
    reached the model - the exact failure that degrades ranking without erroring.
    """
    seen: list = []
    _capturing_backend(monkeypatch, seen)

    with_prefix = embedding.encode_query("graph neural networks")
    without_prefix = embedding._validate(embedding._encode_hf_api("graph neural networks"))

    assert seen == [EMBEDDING_QUERY_PREFIX + "graph neural networks",
                    "graph neural networks"]
    assert with_prefix != without_prefix


def test_warmup_prefixes_its_probe(monkeypatch):
    """Warmup must exercise the same path a real query takes, prefix included."""
    seen: list = []
    _capturing_backend(monkeypatch, seen)
    embedding.warmup()
    assert seen and seen[0].startswith(EMBEDDING_QUERY_PREFIX)
