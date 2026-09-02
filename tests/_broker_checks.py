"""
tests/_broker_checks.py — broker standardizer assertions, run inside mcp_server/.

NOT collected by pytest (the leading underscore). `tests/test_brokers.py` executes
this file as a subprocess with `mcp_server/` as the import root, because both apps
use flat imports and their top-level module names (config, services, repositories,
middleware, exceptions) collide — they cannot share one interpreter.

Each check is a zero-arg function whose name becomes a test case. It returns None
on success or raises; results are emitted as JSON on stdout.
"""

import json
import sys
import traceback

from brokers.openalex_broker import _standardize_many, _standardize_work, _strip_prefix
from brokers.semantic_scholar_broker import _standardize_paper
from brokers.wikipedia_broker import _standardize_summary


def _work(**overrides) -> dict:
    """A minimal OpenAlex Work with every field present, overridable per check."""
    work = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.7717/peerj.4375",
        "title": "A Paper",
        "publication_year": 2017,
        "cited_by_count": 42,
        "primary_location": {"source": {"display_name": "PeerJ"}},
        "abstract_inverted_index": {"Hello": [0], "world": [1]},
        "open_access": {"oa_url": "https://example.org/pdf"},
        "authorships": [],
    }
    work.update(overrides)
    return work


# ---------------------------------------------------------------------------
# Explicit-null regressions (author.id is the one that fired in production)
# ---------------------------------------------------------------------------

def check_doi_explicit_null_does_not_crash():
    """OpenAlex sends `"doi": null` for preprints/theses/datasets."""
    paper = _standardize_work(_work(doi=None))
    assert paper["doi"] is None, paper["doi"]


def check_doi_prefix_stripped():
    paper = _standardize_work(_work(doi="https://doi.org/10.1234/abc"))
    assert paper["doi"] == "10.1234/abc", paper["doi"]


def check_id_explicit_null_does_not_crash():
    paper = _standardize_work(_work(id=None))
    assert paper["openalex_id"] is None, paper["openalex_id"]


def check_id_prefix_stripped():
    paper = _standardize_work(_work(id="https://openalex.org/W123"))
    assert paper["openalex_id"] == "W123", paper["openalex_id"]


# ---------------------------------------------------------------------------
# Authorship edge cases
# ---------------------------------------------------------------------------

def check_author_id_null_keeps_the_author():
    """THE production crash: author object present, `"id": null` inside it."""
    work = _work(authorships=[{"author": {"id": None, "display_name": "Ada Lovelace"},
                               "institutions": []}])
    authors = _standardize_work(work)["_authors"]
    assert len(authors) == 1, authors
    assert authors[0]["openalex_id"] is None, authors[0]
    assert authors[0]["display_name"] == "Ada Lovelace", authors[0]


def check_author_object_null_is_skipped():
    work = _work(authorships=[{"author": None, "institutions": []}])
    assert _standardize_work(work)["_authors"] == []


def check_author_without_name_is_skipped():
    work = _work(authorships=[
        {"author": {"id": "https://openalex.org/A1", "display_name": None}, "institutions": []},
        {"author": {"id": "https://openalex.org/A2", "display_name": "Grace Hopper"}, "institutions": []},
    ])
    authors = _standardize_work(work)["_authors"]
    assert [a["display_name"] for a in authors] == ["Grace Hopper"], authors


# ---------------------------------------------------------------------------
# Other explicit nulls
# ---------------------------------------------------------------------------

def check_citation_count_null_becomes_zero():
    assert _standardize_work(_work(cited_by_count=None))["citation_count"] == 0


def check_title_null_becomes_empty_string():
    assert _standardize_work(_work(title=None))["title"] == ""


def check_missing_abstract_is_none():
    assert _standardize_work(_work(abstract_inverted_index=None))["abstract"] is None


def check_all_optional_fields_null():
    """The worst realistic record: everything nullable is null."""
    paper = _standardize_work({
        "id": None, "doi": None, "title": None, "publication_year": None,
        "cited_by_count": None, "primary_location": None,
        "abstract_inverted_index": None, "open_access": None, "authorships": None,
    })
    assert paper["doi"] is None and paper["openalex_id"] is None
    assert paper["title"] == "" and paper["citation_count"] == 0
    assert paper["venue"] is None and paper["open_access_url"] is None
    assert paper["_authors"] == []


# ---------------------------------------------------------------------------
# Batch resilience — one bad record must not fail the whole search
# ---------------------------------------------------------------------------

def check_batch_survives_one_unparseable_record():
    good_a, good_b = _work(id="https://openalex.org/W1"), _work(id="https://openalex.org/W2")
    results = _standardize_many([good_a, None, good_b])   # None is not a dict -> raises
    assert len(results) == 2, results
    assert [r["openalex_id"] for r in results] == ["W1", "W2"], results


def check_batch_of_only_bad_records_returns_empty():
    assert _standardize_many([None, "not-a-dict", 42]) == []


def check_strip_prefix_is_null_safe():
    assert _strip_prefix(None, "https://doi.org/") is None
    assert _strip_prefix("", "https://doi.org/") is None
    assert _strip_prefix("https://doi.org/", "https://doi.org/") is None
    assert _strip_prefix("https://doi.org/10.1/x", "https://doi.org/") == "10.1/x"


# ---------------------------------------------------------------------------
# Semantic Scholar + Wikipedia — same null-default class of bug
# ---------------------------------------------------------------------------

def check_s2_citation_count_null_becomes_zero():
    assert _standardize_paper({"paperId": "p1", "citationCount": None})["citation_count"] == 0


def check_s2_unnamed_author_is_dropped():
    paper = _standardize_paper({
        "paperId": "p1",
        "authors": [{"authorId": "a1", "name": None}, {"authorId": "a2", "name": "Alan Turing"}],
    })
    assert [a["display_name"] for a in paper["_authors"]] == ["Alan Turing"], paper["_authors"]


def check_s2_all_nulls_does_not_crash():
    paper = _standardize_paper({
        "paperId": None, "externalIds": None, "title": None, "abstract": None,
        "year": None, "venue": None, "citationCount": None, "tldr": None,
        "influentialCitationCount": None, "openAccessPdf": None, "authors": None,
    })
    assert paper["title"] == "" and paper["citation_count"] == 0
    assert paper["influence_score"] is None and paper["_authors"] == []


def check_wikipedia_title_null_becomes_empty_string():
    assert _standardize_summary({"title": None, "content_urls": None})["topic_name"] == ""


def main() -> int:
    checks = {name: fn for name, fn in sorted(globals().items())
              if name.startswith("check_") and callable(fn)}
    results = {}
    for name, fn in checks.items():
        try:
            fn()
            results[name] = "ok"
        except Exception:
            results[name] = traceback.format_exc(limit=3).strip()
    json.dump(results, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
