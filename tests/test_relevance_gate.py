"""
tests/test_relevance_gate.py — the semantic relevance gate.

The gate decides what enters the corpus, so its failure modes are asymmetric:

  Too strict, and real papers are silently never ingested. Nothing errors; the
  corpus is just thinner than it should be, and you cannot tell by looking at it.

  Too loose, and it does nothing — which is what a threshold of 0 would do, and
  what a gate that never runs would also do. Those two look identical in the
  database, which is why `pipeline_runs.candidates_rejected` exists.

The gate is also deliberately **provider-agnostic**: it scores `PaperCandidate`
records without caring which provider produced them, so arXiv and PubMed will be
filtered by the same code with no changes.

`relevance_scores` is thin glue over the model and is exercised with a fake
encoder; the decision logic it feeds is pure and tested directly.
"""

import ast
import pathlib

import pytest

NOTEBOOK = (pathlib.Path(__file__).resolve().parents[1]
            / "notebooks" / "ingest_papers_embeddings.py")

_WANTED_NAMES = {"candidate_text", "partition_by_relevance"}


def _load():
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    keep = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in _WANTED_NAMES]
    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(NOTEBOOK), "exec"), ns)
    missing = _WANTED_NAMES - ns.keys()
    assert not missing, f"notebook no longer defines {missing} — update this loader"
    return ns


@pytest.fixture(scope="module")
def nb():
    return _load()


def candidates(*names):
    return [{"source": "openalex", "source_id": n, "title": n, "abstract": "text"}
            for n in names]


# ---------------------------------------------------------------------------
# What text a candidate is judged on
# ---------------------------------------------------------------------------

def test_title_and_abstract_are_combined(nb):
    """
    The same shape the document embedding will later see, so the gate's score and
    the retrieval score mean the same thing.
    """
    assert nb["candidate_text"]({"title": "A Paper", "abstract": "About things"}) == \
        "A Paper. About things"


def test_a_missing_abstract_falls_back_to_the_title(nb):
    assert nb["candidate_text"]({"title": "A Paper", "abstract": None}) == "A Paper"
    assert nb["candidate_text"]({"title": "A Paper", "abstract": ""}) == "A Paper"


def test_a_missing_title_falls_back_to_the_abstract(nb):
    assert nb["candidate_text"]({"title": None, "abstract": "About things"}) == "About things"


def test_an_empty_candidate_yields_empty_text(nb):
    """Must not raise: an unusable candidate scores badly and is rejected, not crashed on."""
    assert nb["candidate_text"]({"title": None, "abstract": None}) == ""
    assert nb["candidate_text"]({}) == ""


def test_whitespace_is_stripped(nb):
    assert nb["candidate_text"]({"title": "  A Paper  ", "abstract": "  Text  "}) == \
        "A Paper. Text"


# ---------------------------------------------------------------------------
# The accept / reject decision
# ---------------------------------------------------------------------------

def test_candidates_split_at_the_threshold(nb):
    cands = candidates("high", "low")
    accepted, rejected = nb["partition_by_relevance"](cands, [0.80, 0.20], 0.40)
    assert [c["source_id"] for c in accepted] == ["high"]
    assert [c["source_id"] for c in rejected] == ["low"]


def test_a_score_exactly_on_the_threshold_is_accepted(nb):
    """`>=`, not `>`. Stated explicitly so a later refactor cannot flip it unnoticed."""
    accepted, rejected = nb["partition_by_relevance"](candidates("edge"), [0.40], 0.40)
    assert len(accepted) == 1 and not rejected


def test_the_score_is_recorded_on_every_candidate(nb):
    """
    Accepted *and* rejected. A rejected candidate's score is what tells you whether
    the threshold is set right, so discarding it discards the calibration data.
    """
    cands = candidates("high", "low")
    accepted, rejected = nb["partition_by_relevance"](cands, [0.80, 0.20], 0.40)
    assert accepted[0]["relevance_score"] == 0.80
    assert rejected[0]["relevance_score"] == 0.20


def test_a_zero_threshold_accepts_everything(nb):
    """
    The control condition. Running with threshold 0 proves the gate is executing at
    all, separating "nothing was rejected" from "the gate never ran".
    """
    cands = candidates("a", "b", "c")
    accepted, rejected = nb["partition_by_relevance"](cands, [0.9, 0.5, 0.01], 0.0)
    assert len(accepted) == 3 and not rejected


def test_an_impossible_threshold_rejects_everything(nb):
    cands = candidates("a", "b")
    accepted, rejected = nb["partition_by_relevance"](cands, [0.9, 0.5], 1.01)
    assert not accepted and len(rejected) == 2


def test_no_candidates_is_not_an_error(nb):
    assert nb["partition_by_relevance"]([], [], 0.40) == ([], [])


def test_input_order_is_preserved_within_each_group(nb):
    cands = candidates("a", "b", "c", "d")
    accepted, rejected = nb["partition_by_relevance"](cands, [0.9, 0.1, 0.8, 0.2], 0.4)
    assert [c["source_id"] for c in accepted] == ["a", "c"]
    assert [c["source_id"] for c in rejected] == ["b", "d"]


def test_the_gate_does_not_care_which_provider_produced_a_candidate(nb):
    """
    Provider-agnostic by construction — arXiv and PubMed candidates will be filtered
    by this same code with no changes.
    """
    mixed = [
        {"source": "arxiv", "source_id": "2401.00001", "title": "A", "abstract": "x"},
        {"source": "pubmed", "source_id": "PMC123", "title": "B", "abstract": "y"},
    ]
    accepted, rejected = nb["partition_by_relevance"](mixed, [0.9, 0.1], 0.4)
    assert accepted[0]["source"] == "arxiv"
    assert rejected[0]["source"] == "pubmed"
