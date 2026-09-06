"""
tests/test_pipeline_harvest.py — Phase 2.5 harvest and enrichment logic.

The one that matters is `enrich_batch`. Semantic Scholar's batch endpoint returns a
JSON list **positionally aligned with the ids you sent**, with `null` in the slot for
a miss — not a keyed object. Matching by index rather than by id is what makes it
fast; getting it wrong silently attaches one paper's TLDR and influence score to a
*different* paper. That is worse than no enrichment: the data looks present, reads
plausibly, and is wrong.

Loaded the same way as tests/test_section_extraction.py — pure functions lifted out
of the notebook with `ast`, so no widgets, no %pip, no database, no network.
"""

import ast
import pathlib

import pytest

NOTEBOOK = (pathlib.Path(__file__).resolve().parents[1]
            / "notebooks" / "ingest_papers_embeddings.py")

_WANTED_NAMES = {"_standardize_openalex", "enrich_batch", "S2_BATCH_URL", "S2_FIELDS"}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeRequests:
    """Records every POST and replays a queued response."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, params=None, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "json": json})
        if not self._responses:
            raise AssertionError("enrich_batch made more requests than the test queued")
        return self._responses.pop(0)


def _load(responses, batch_size=100):
    """Exec the notebook's harvest helpers against fake network and timing."""
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED_NAMES:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & _WANTED_NAMES:
                keep.append(node)

    fake_requests = _FakeRequests(responses)
    ns = {
        "requests": fake_requests,
        "time": type("T", (), {"sleep": staticmethod(lambda _s: None)}),
        "S2_API_KEY": "test-key",
        "S2_BATCH_SIZE": batch_size,
        "S2_BATCH_DELAY": 0.0,
        "s2_request_count": 0,
        "print": lambda *a, **k: None,
    }
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(NOTEBOOK), "exec"), ns)

    missing = _WANTED_NAMES - ns.keys()
    assert not missing, f"notebook no longer defines {missing} — update this loader"
    return ns, fake_requests


def _s2(paper_id, tldr, influence):
    return {"paperId": paper_id, "tldr": {"text": tldr}, "influentialCitationCount": influence}


# ---------------------------------------------------------------------------
# The alignment trap
# ---------------------------------------------------------------------------

def test_records_are_matched_to_the_doi_at_the_same_index():
    ns, _ = _load([_FakeResponse([
        _s2("S2_A", "first paper", 10),
        _s2("S2_B", "second paper", 20),
        _s2("S2_C", "third paper", 30),
    ])])

    got = ns["enrich_batch"](["10.1/a", "10.1/b", "10.1/c"])

    assert got["10.1/a"]["semantic_scholar_id"] == "S2_A"
    assert got["10.1/b"]["tldr"] == "second paper"
    assert got["10.1/c"]["influence_score"] == 30


def test_a_null_slot_only_drops_its_own_doi():
    """
    S2 returns null *in place* for an unknown id rather than omitting it. If that
    slot were dropped instead of preserved, every DOI after it would shift up one
    position and receive the wrong paper's data.
    """
    ns, _ = _load([_FakeResponse([
        _s2("S2_A", "first", 1),
        None,                       # S2 does not know 10.1/b
        _s2("S2_C", "third", 3),
    ])])

    got = ns["enrich_batch"](["10.1/a", "10.1/b", "10.1/c"])

    assert "10.1/b" not in got
    assert got["10.1/a"]["semantic_scholar_id"] == "S2_A"
    assert got["10.1/c"]["semantic_scholar_id"] == "S2_C"   # not shifted


def test_a_misaligned_response_is_discarded_entirely():
    """
    If the response length does not match the request, there is no way to say which
    record belongs to which DOI. Attaching nothing is the only safe answer — a
    best-effort guess here is exactly how wrong data gets written.
    """
    ns, _ = _load([_FakeResponse([_s2("S2_A", "first", 1)])])   # 1 back for 3 sent
    assert ns["enrich_batch"](["10.1/a", "10.1/b", "10.1/c"]) == {}


def test_a_non_list_response_is_discarded():
    ns, _ = _load([_FakeResponse({"error": "rate limited"})])
    assert ns["enrich_batch"](["10.1/a"]) == {}


# ---------------------------------------------------------------------------
# Request shape and batching
# ---------------------------------------------------------------------------

def test_dois_are_sent_with_the_doi_prefix():
    ns, fake = _load([_FakeResponse([_s2("S2_A", "x", 1)])])
    ns["enrich_batch"](["10.1/a"])
    assert fake.calls[0]["json"] == {"ids": ["DOI:10.1/a"]}
    assert fake.calls[0]["headers"]["x-api-key"] == "test-key"


def test_requests_are_split_into_batches():
    """One request per batch, not one per paper — the entire point of the change."""
    ns, fake = _load(
        [_FakeResponse([_s2(f"S2_{i}", "x", i) for i in range(2)]),
         _FakeResponse([_s2("S2_2", "x", 2)])],
        batch_size=2,
    )
    got = ns["enrich_batch"](["10.1/a", "10.1/b", "10.1/c"])
    assert len(fake.calls) == 2
    assert len(got) == 3


def test_no_dois_makes_no_request():
    ns, fake = _load([])
    assert ns["enrich_batch"]([]) == {}
    assert fake.calls == []


def test_a_failed_batch_does_not_lose_the_others():
    """A 429 on one batch must not discard the batches that succeeded."""
    ns, _ = _load(
        [_FakeResponse(None, status_code=429),
         _FakeResponse([_s2("S2_C", "third", 3)])],
        batch_size=2,
    )
    got = ns["enrich_batch"](["10.1/a", "10.1/b", "10.1/c"])
    assert set(got) == {"10.1/c"}


def test_lookup_keys_are_lowercased():
    """DOIs vary in case across sources; the caller looks up with .lower()."""
    ns, _ = _load([_FakeResponse([_s2("S2_A", "x", 1)])])
    assert "10.1/abc" in ns["enrich_batch"](["10.1/ABC"])


# ---------------------------------------------------------------------------
# OpenAlex record standardisation
# ---------------------------------------------------------------------------

def test_abstract_is_reconstructed_from_the_inverted_index():
    ns, _ = _load([])
    row = ns["_standardize_openalex"]({
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1/x",
        "title": "A Paper",
        "abstract_inverted_index": {"Linear": [0], "attention": [1], "works": [2]},
        "publication_year": 2024,
        "publication_date": "2024-03-01",
    })
    assert row["abstract"] == "Linear attention works"
    assert row["openalex_id"] == "W1"        # URL prefix stripped
    assert row["doi"] == "10.1/x"


def test_a_paper_without_an_abstract_is_not_ingested():
    """
    The abstract is the only text guaranteed to exist for every paper, and the whole
    index rests on it. A paper without one would occupy a row and never be findable.
    """
    ns, _ = _load([])
    assert ns["_standardize_openalex"]({
        "id": "https://openalex.org/W1", "title": "No Abstract", "abstract_inverted_index": {},
    }) is None


def test_missing_ids_do_not_raise():
    """
    The broker null bug in one sentence: `.get(k, default)` returns the *stored*
    value when the key exists and holds None, so the default never fires.
    """
    ns, _ = _load([])
    row = ns["_standardize_openalex"]({
        "id": "https://openalex.org/W1",
        "doi": None,
        "title": "No DOI",
        "abstract_inverted_index": {"text": [0]},
        "primary_location": None,
        "open_access": None,
    })
    assert row["doi"] is None
    assert row["venue"] is None
    assert row["open_access_url"] is None


@pytest.mark.parametrize("field", ["id", "title"])
def test_records_missing_an_identifier_or_title_are_skipped(field):
    ns, _ = _load([])
    item = {
        "id": "https://openalex.org/W1",
        "title": "A Paper",
        "abstract_inverted_index": {"text": [0]},
    }
    item[field] = None
    assert ns["_standardize_openalex"](item) is None
