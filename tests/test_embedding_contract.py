"""
tests/test_embedding_contract.py — the contract guarding persisted embedding models.

What this protects
------------------
Four values define what a stored vector *means*: the base model, the dimension, the
document prefix and the query prefix (plus normalisation). They are currently kept
aligned by hand across `notebooks/ingest_papers_embeddings.py`,
`dashboard/config.py`, `mcp_server/config.py` and `sql/02_*.sql`, and until now only
the dimension was checked at runtime.

The other three fail silently. A vector encoded by the wrong model, or without the
`search_document: ` prefix, is still a perfectly valid 768-dimensional unit vector:
it inserts cleanly, searches without error, and ranks badly. Nothing downstream can
detect it.

So `setup_embedding_model.py` writes `embedding_contract.json` beside the persisted
model, and the ingestion pipeline validates it before encoding anything — cheaply,
before loading 568 MB of weights.

Loaded out of the notebook with `ast`, like the other pipeline tests — no widgets,
no %pip, no database, no network.
"""

import ast
import pathlib

import pytest

NOTEBOOK = (pathlib.Path(__file__).resolve().parents[1]
            / "notebooks" / "ingest_papers_embeddings.py")

_WANTED_NAMES = {"validate_embedding_contract", "CONTRACT_FILENAME"}

GOOD = {
    "base_model": "nomic-ai/modernbert-embed-base",
    "embedding_dim": 768,
    "document_prefix": "search_document: ",
    "query_prefix": "search_query: ",
    "normalize": True,
}


def _load():
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED_NAMES:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & _WANTED_NAMES:
                keep.append(node)

    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(NOTEBOOK), "exec"), ns)
    missing = _WANTED_NAMES - ns.keys()
    assert not missing, f"notebook no longer defines {missing} — update this loader"
    return ns


@pytest.fixture(scope="module")
def validate():
    return _load()["validate_embedding_contract"]


def test_a_matching_contract_passes(validate):
    validate(dict(GOOD), dict(GOOD))


# ---------------------------------------------------------------------------
# Each field, because each one fails silently in its own way
# ---------------------------------------------------------------------------

def test_a_different_model_is_rejected(validate):
    """bge-base is also 768-dim. The dimension check alone would let this through."""
    stored = {**GOOD, "base_model": "BAAI/bge-base-en-v1.5"}
    with pytest.raises(ValueError, match="base_model"):
        validate(stored, dict(GOOD))


def test_a_different_dimension_is_rejected(validate):
    stored = {**GOOD, "embedding_dim": 384}
    with pytest.raises(ValueError, match="embedding_dim"):
        validate(stored, dict(GOOD))


def test_a_missing_document_prefix_is_rejected(validate):
    """
    The quiet one. An unprefixed document vector is valid, unit-length and in the
    wrong neighbourhood — it degrades ranking without erroring anywhere.
    """
    stored = {**GOOD, "document_prefix": ""}
    with pytest.raises(ValueError, match="document_prefix"):
        validate(stored, dict(GOOD))


def test_a_swapped_prefix_pair_is_rejected(validate):
    """Asymmetric models punish this precisely, and nothing else would catch it."""
    stored = {**GOOD, "document_prefix": "search_query: ", "query_prefix": "search_document: "}
    with pytest.raises(ValueError, match="document_prefix"):
        validate(stored, dict(GOOD))


def test_unnormalised_vectors_are_rejected(validate):
    """Cosine distance in pgvector assumes unit length; the pipeline promises it."""
    stored = {**GOOD, "normalize": False}
    with pytest.raises(ValueError, match="normalize"):
        validate(stored, dict(GOOD))


# ---------------------------------------------------------------------------
# Malformed contracts
# ---------------------------------------------------------------------------

def test_a_missing_field_names_the_remedy(validate):
    """An older setup notebook wrote fewer fields — say so, don't just fail."""
    stored = {k: v for k, v in GOOD.items() if k != "query_prefix"}
    with pytest.raises(ValueError, match="setup_embedding_model"):
        validate(stored, dict(GOOD))


@pytest.mark.parametrize("stored", [None, [], "text", 42])
def test_a_non_object_contract_is_rejected(validate, stored):
    with pytest.raises(ValueError):
        validate(stored, dict(GOOD))


def test_extra_fields_in_the_stored_contract_are_ignored(validate):
    """
    setup writes `saved_at`, and future versions may add more. Validation checks
    what the pipeline depends on, so adding provenance fields is not a breaking
    change.
    """
    validate({**GOOD, "saved_at": "2026-09-16T00:00:00Z", "future_field": "x"}, dict(GOOD))


def test_the_error_names_both_values(validate):
    """A mismatch is only actionable if you can see what differs."""
    stored = {**GOOD, "embedding_dim": 384}
    with pytest.raises(ValueError) as excinfo:
        validate(stored, dict(GOOD))
    message = str(excinfo.value)
    assert "384" in message and "768" in message
