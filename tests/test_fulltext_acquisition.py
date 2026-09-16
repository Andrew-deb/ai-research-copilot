"""
tests/test_fulltext_acquisition.py — Phase 2.6 PDF candidate selection and failure
classification.

Why these exist
---------------
The first real run attempted 152 papers and recorded every HTTP failure as
`fetch_failed`, which the retry policy treats as transient. 32 of those 35 were 403
or 404 — a publisher declining automated download, or a dead URL. Neither improves
with time, so the policy had queued 64 pointless requests across future runs.

A separate 42 papers returned HTTP 200 with an HTML body and were recorded as
`parse_failed`, though nothing was ever parsed. That conflation hid the actual
finding: `open_access.oa_url` frequently points at the publisher's landing page,
while OpenAlex separately knows a repository `pdf_url` that we never asked for.

So two rules are pinned here: candidates are tried repository-first, and a status
means what it says.

Loaded out of the notebook with `ast`, like the other pipeline tests — no widgets,
no %pip, no database, no network.
"""

import ast
import pathlib

import pytest

NOTEBOOK = (pathlib.Path(__file__).resolve().parents[1]
            / "notebooks" / "ingest_papers_embeddings.py")

_WANTED_NAMES = {"pdf_candidates", "classify_http_error", "RETRYABLE_HTTP"}

# The statuses the retry query treats as transient. Everything else is permanent.
RETRYABLE_STATUSES = {"fetch_failed"}


def _load(max_candidates=3):
    tree = ast.parse(NOTEBOOK.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED_NAMES:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & _WANTED_NAMES:
                keep.append(node)

    ns = {"MAX_PDF_CANDIDATES": max_candidates}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(NOTEBOOK), "exec"), ns)

    missing = _WANTED_NAMES - ns.keys()
    assert not missing, f"notebook no longer defines {missing} — update this loader"
    return ns


@pytest.fixture(scope="module")
def nb():
    return _load()


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def _http_error(status_code):
    """A requests-style exception carrying a response, as raise_for_status raises."""
    exc = Exception(f"{status_code} Client Error")
    exc.response = _Response(status_code)
    return exc


# ---------------------------------------------------------------------------
# Candidate ordering
# ---------------------------------------------------------------------------

def test_repository_copies_are_tried_before_the_publisher(nb):
    """
    The publisher copy is the one that returns 403. The repository copy — arXiv,
    PMC, an institutional archive — is deposited precisely so it can be fetched.
    """
    payload = {
        "best_oa_location": {"pdf_url": "https://dl.acm.org/doi/pdf/10.1145/x"},
        "locations": [
            {"pdf_url": "https://dl.acm.org/doi/pdf/10.1145/x",
             "source": {"type": "journal"}},
            {"pdf_url": "https://arxiv.org/pdf/2401.00001",
             "source": {"type": "repository"}},
        ],
    }
    assert nb["pdf_candidates"](payload, None)[0] == "https://arxiv.org/pdf/2401.00001"


def test_oa_url_is_the_last_resort(nb):
    """
    open_access.oa_url is what the first run used exclusively, and it is where the
    HTML landing pages came from. It stays — as the fallback, not the first choice.
    """
    payload = {"locations": [
        {"pdf_url": "https://arxiv.org/pdf/2401.00001", "source": {"type": "repository"}},
    ]}
    got = nb["pdf_candidates"](payload, "https://publisher.example/landing-page")
    assert got[-1] == "https://publisher.example/landing-page"


def test_papers_ingested_before_this_change_still_work(nb):
    """A payload with no `locations` must degrade to the previous behaviour."""
    assert nb["pdf_candidates"]({}, "https://example.org/a.pdf") == ["https://example.org/a.pdf"]
    assert nb["pdf_candidates"](None, "https://example.org/a.pdf") == ["https://example.org/a.pdf"]


def test_no_urls_anywhere_yields_no_candidates(nb):
    """This is `no_url` — expected for the 19 papers OpenAlex has no OA copy for."""
    assert nb["pdf_candidates"]({}, None) == []
    assert nb["pdf_candidates"]({"locations": [{"source": {"type": "repository"}}]}, None) == []


def test_duplicate_urls_are_not_tried_twice(nb):
    """best_oa_location usually repeats one of the locations. Fetching it twice is
    a wasted request and a doubled politeness delay."""
    url = "https://arxiv.org/pdf/2401.00001"
    payload = {
        "best_oa_location": {"pdf_url": url},
        "locations": [{"pdf_url": url, "source": {"type": "repository"}}],
    }
    assert nb["pdf_candidates"](payload, url) == [url]


def test_candidate_count_is_capped(nb):
    """Bounds the time one stubborn paper can spend before the run moves on."""
    ns = _load(max_candidates=2)
    payload = {"locations": [
        {"pdf_url": f"https://repo{i}.example/a.pdf", "source": {"type": "repository"}}
        for i in range(5)
    ]}
    assert len(ns["pdf_candidates"](payload, "https://fallback.example/a.pdf")) == 2


def test_malformed_locations_do_not_raise(nb):
    """OpenAlex records are not guaranteed well-formed — the broker bug taught that."""
    payload = {"locations": [None, "nonsense", {"pdf_url": None}, {"source": None},
                             {"pdf_url": "https://ok.example/a.pdf", "source": {"type": "repository"}}]}
    assert nb["pdf_candidates"](payload, None) == ["https://ok.example/a.pdf"]


# ---------------------------------------------------------------------------
# Failure classification — the retry policy depends entirely on this
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code,expected", [
    (401, "access_denied"),
    (403, "access_denied"),     # 24 of the 35 failures in the first run
    (404, "not_found"),         # 8 more
    (410, "not_found"),
    (408, "fetch_failed"),
    (429, "fetch_failed"),
    (500, "fetch_failed"),      # dipot.ulb.ac.be in the first run
    (502, "fetch_failed"),
    (503, "fetch_failed"),
])
def test_http_status_maps_to_the_right_outcome(nb, status_code, expected):
    assert nb["classify_http_error"](_http_error(status_code)) == expected


def test_publisher_blocks_are_never_retried(nb):
    """
    The core of the fix. 403 from ACM/OUP/Elsevier will be 403 next week too;
    classifying it as retryable is what queued 64 pointless requests.
    """
    for code in (401, 403, 404, 410):
        assert nb["classify_http_error"](_http_error(code)) not in RETRYABLE_STATUSES


def test_transient_failures_stay_retryable(nb):
    """The opposite error: a 5xx or a dropped connection deserves another try."""
    for code in (429, 500, 502, 503, 504):
        assert nb["classify_http_error"](_http_error(code)) in RETRYABLE_STATUSES


def test_connection_errors_have_no_response_and_are_retryable(nb):
    """A timeout or DNS failure raises with no `.response` at all."""
    assert nb["classify_http_error"](Exception("connection reset")) in RETRYABLE_STATUSES
