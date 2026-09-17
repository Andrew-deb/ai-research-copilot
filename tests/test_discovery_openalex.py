"""
tests/test_discovery_openalex.py — the discovery boundary.

`discover_openalex()` is the seam that keeps OpenAlex from being baked into the
pipeline. Everything provider-specific lives behind it; everything downstream sees
only `PaperCandidate` records. These tests pin both halves of that contract:

  1. Field translation. `research_fields` is a *generic* setting written in plain
     names. Turning "computer science" into `primary_topic.field.id:fields/17` is
     OpenAlex's private business — arXiv would turn the same input into `cat:cs.*`.

  2. Candidate normalization. Whatever OpenAlex returns becomes the same shape any
     future provider must return.

The sharp edge is an unrecognised field name. Skipping it would silently disable
filtering and let the corpus fill with biology again, with no symptom — so it raises.

Loaded out of the notebook with `ast`, like the other pipeline tests.
"""

import ast
import pathlib

import pytest

NOTEBOOK = (pathlib.Path(__file__).resolve().parents[1]
            / "notebooks" / "ingest_papers_embeddings.py")

_WANTED_NAMES = {
    "openalex_field_filter", "_openalex_to_candidate",
    "OPENALEX_FIELD_IDS", "OPENALEX_FIELD_ALIASES", "OPENALEX_SELECT",
}

# Every key a PaperCandidate must carry, whoever produced it.
CANDIDATE_KEYS = {
    "source", "source_id", "doi", "title", "abstract", "publication_year",
    "publication_date", "venue", "citation_count", "open_access_url", "raw",
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
def nb():
    return _load()


def work(**overrides):
    """A minimal well-formed OpenAlex work."""
    item = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.1145/3442188",
        "title": "Attention Is Cheap",
        "abstract_inverted_index": {"Linear": [0], "attention": [1], "works": [2]},
        "publication_year": 2024,
        "publication_date": "2024-03-01",
        "cited_by_count": 12,
        "primary_location": {"source": {"display_name": "NeurIPS"}},
        "open_access": {"oa_url": "https://example.org/a.pdf"},
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# Field translation — generic names in, OpenAlex syntax out
# ---------------------------------------------------------------------------

def test_a_field_name_becomes_an_openalex_filter(nb):
    assert nb["openalex_field_filter"](["computer science"]) == \
        "primary_topic.field.id:fields/17"


def test_multiple_fields_are_ORed(nb):
    """Interdisciplinary work spans fields; '|' is OR in OpenAlex filter syntax."""
    got = nb["openalex_field_filter"](["computer science", "neuroscience"])
    assert got == "primary_topic.field.id:fields/17|fields/28"


def test_field_names_are_case_and_space_insensitive(nb):
    assert nb["openalex_field_filter"]([" Computer Science "]) == \
        nb["openalex_field_filter"](["computer science"])


def test_shorthand_aliases_resolve(nb):
    for alias in ("cs", "computing"):
        assert nb["openalex_field_filter"]([alias]) == "primary_topic.field.id:fields/17"
    assert nb["openalex_field_filter"](["math"]) == "primary_topic.field.id:fields/26"


def test_duplicate_fields_are_collapsed(nb):
    assert nb["openalex_field_filter"](["cs", "computer science"]) == \
        "primary_topic.field.id:fields/17"


def test_no_fields_means_no_filter(nb):
    """Searching all of science stays possible — deliberately, not by accident."""
    assert nb["openalex_field_filter"]([]) is None
    assert nb["openalex_field_filter"]([""]) is None
    assert nb["openalex_field_filter"](["  "]) is None


def test_an_unknown_field_raises_rather_than_being_skipped(nb):
    """
    The important one. A typo that silently dropped the filter would reintroduce
    exactly the bug this feature fixes, and the only symptom would be a corpus
    slowly refilling with biology.
    """
    with pytest.raises(ValueError, match="comptuer science"):
        nb["openalex_field_filter"](["comptuer science"])


def test_the_error_lists_the_valid_fields(nb):
    with pytest.raises(ValueError) as excinfo:
        nb["openalex_field_filter"](["nonsense"])
    assert "computer science" in str(excinfo.value)


def test_one_bad_field_rejects_the_whole_set(nb):
    """Partial application would filter on some fields and silently drop others."""
    with pytest.raises(ValueError):
        nb["openalex_field_filter"](["computer science", "nonsense"])


def test_every_field_id_is_unique(nb):
    ids = list(nb["OPENALEX_FIELD_IDS"].values())
    assert len(ids) == len(set(ids))


def test_every_alias_points_at_a_real_field(nb):
    for alias, target in nb["OPENALEX_FIELD_ALIASES"].items():
        assert target in nb["OPENALEX_FIELD_IDS"], f"alias {alias!r} -> unknown {target!r}"


# ---------------------------------------------------------------------------
# Candidate normalization — the provider-neutral shape
# ---------------------------------------------------------------------------

def test_a_work_becomes_a_paper_candidate(nb):
    c = nb["_openalex_to_candidate"](work())
    assert set(c) == CANDIDATE_KEYS
    assert c["source"] == "openalex"
    assert c["source_id"] == "W2741809807"        # URL prefix stripped
    assert c["doi"] == "10.1145/3442188"          # URL prefix stripped
    assert c["abstract"] == "Linear attention works"
    assert c["venue"] == "NeurIPS"


def test_the_raw_payload_is_carried_through(nb):
    """Enrichment and PDF-URL resolution both read it; losing it loses both."""
    item = work()
    assert nb["_openalex_to_candidate"](item)["raw"] is item


def test_a_paper_without_an_abstract_is_rejected(nb):
    """
    The abstract is the only text guaranteed to exist for every paper, the index
    rests on it, and the relevance gate has nothing to score without it.
    """
    assert nb["_openalex_to_candidate"](work(abstract_inverted_index={})) is None


@pytest.mark.parametrize("field", ["id", "title"])
def test_records_missing_an_identifier_or_title_are_rejected(nb, field):
    assert nb["_openalex_to_candidate"](work(**{field: None})) is None


def test_missing_optional_fields_do_not_raise(nb):
    """
    The broker null bug in one sentence: `.get(k, default)` returns the *stored*
    value when the key exists and holds None, so the default never fires.
    """
    c = nb["_openalex_to_candidate"](work(
        doi=None, primary_location=None, open_access=None, publication_date=None))
    assert c["doi"] is None
    assert c["venue"] is None
    assert c["open_access_url"] is None
    assert c["publication_date"] is None


def test_the_select_asks_for_the_fields_normalization_needs(nb):
    """A dropped select field would make candidates silently lose data."""
    select = nb["OPENALEX_SELECT"]
    for required in ("abstract_inverted_index", "publication_date", "best_oa_location",
                     "locations", "open_access", "primary_location"):
        assert required in select, f"OPENALEX_SELECT no longer requests {required!r}"
