"""
mcp_server/brokers/semantic_scholar_broker.py — Semantic Scholar API client.

SRP: HTTP calls to Semantic Scholar only. No DB access, no Flask, no other brokers.

Primary role is ENRICHMENT: adds TLDRs, influence scores, and recommendations
to papers already discovered via OpenAlex. Authenticated via x-api-key header.
"""

import logging
import time

import requests

from config import S2_API_KEY, S2_BASE_URL, S2_RATE_LIMIT_DELAY

logger = logging.getLogger(__name__)

_PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,year,venue,"
    "citationCount,influentialCitationCount,tldr,openAccessPdf,authors"
)
_CITATION_FIELDS = "paperId,externalIds,title,abstract,year,citationCount,tldr,intents,contexts"
_RECOMMENDATION_FIELDS = "paperId,externalIds,title,abstract,year,venue,citationCount,tldr"


# ---------------------------------------------------------------------------
# Internal HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    h = {"Accept": "application/json"}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


def _get(endpoint: str, params: dict | None = None) -> dict:
    time.sleep(S2_RATE_LIMIT_DELAY)
    resp = requests.get(f"{S2_BASE_URL}{endpoint}", headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(endpoint: str, body: dict) -> dict:
    time.sleep(S2_RATE_LIMIT_DELAY)
    resp = requests.post(f"{S2_BASE_URL}{endpoint}", headers={**_headers(), "Content-Type": "application/json"}, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Standardization
# ---------------------------------------------------------------------------

def _standardize_paper(s2: dict) -> dict:
    """Map a raw S2 paper object to our unified paper dict schema."""
    ext = s2.get("externalIds") or {}
    tldr_obj = s2.get("tldr") or {}
    influence = s2.get("influentialCitationCount")
    oa_url = (s2.get("openAccessPdf") or {}).get("url")

    return {
        "semantic_scholar_id": s2.get("paperId"),
        "doi": ext.get("DOI"),
        "title": s2.get("title") or "",
        "abstract": s2.get("abstract"),
        "publication_year": s2.get("year"),
        "venue": s2.get("venue"),
        "citation_count": s2.get("citationCount") or 0,
        "tldr": tldr_obj.get("text"),
        # influentialCitationCount = citations from high-impact papers (more meaningful than raw count)
        "influence_score": float(influence) if influence is not None else None,
        "source_api": "semantic_scholar",
        "open_access_url": oa_url,
        "payload": s2,
        # S2 sends `"name": null` for unresolved authors — an unnamed author is
        # unusable downstream, so drop it rather than store an empty string.
        "_authors": [
            {"s2_id": a.get("authorId"), "display_name": a.get("name") or ""}
            for a in (s2.get("authors") or [])
            if a.get("name")
        ],
    }


def _standardize_citation(citation_obj: dict) -> dict:
    """Map an S2 citation wrapper (paper + intents + contexts) to our schema."""
    paper = _standardize_paper(citation_obj.get("citingPaper") or {})
    paper["_citation_intents"] = citation_obj.get("intents") or []
    paper["_citation_contexts"] = citation_obj.get("contexts") or []
    return paper


def _standardize_many(papers: list[dict]) -> list[dict]:
    """
    Standardize a batch, skipping (and logging) any record we cannot parse.
    One malformed record must not fail the whole call.
    """
    standardized: list[dict] = []
    for paper in papers:
        try:
            standardized.append(_standardize_paper(paper))
        except Exception as exc:  # noqa: BLE001 - a bad record is skipped, never fatal
            label = paper.get("paperId") if isinstance(paper, dict) else repr(paper)[:80]
            logger.warning("Skipping unparseable S2 paper %r: %s", label, exc)
    return standardized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_paper(paper_id: str) -> dict | None:
    """
    Fetch enrichment data for one paper. Accepts S2 ID, DOI:xxx, ARXIV:xxx, MAG:xxx.
    Returns None if not found.
    """
    try:
        return _standardize_paper(_get(f"/graph/v1/paper/{paper_id}", {"fields": _PAPER_FIELDS}))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def search_papers(query: str, limit: int = 10) -> list[dict]:
    """Search Semantic Scholar by keyword. Returns standardized paper dicts."""
    data = _get("/graph/v1/paper/search", {"query": query, "limit": min(limit, 100), "fields": _PAPER_FIELDS})
    return _standardize_many(data.get("data") or [])


def get_recommendations(s2_paper_id: str, limit: int = 5) -> list[dict]:
    """
    Fetch 'more like this' recommendations using S2's learned paper embeddings.
    Requires a valid S2 paper ID (not DOI). Returns empty list if not found.
    """
    try:
        data = _post("/recommendations/v1/papers", {"positivePaperIds": [s2_paper_id], "negativePaperIds": []})
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise
    return _standardize_many((data.get("recommendedPapers") or [])[:limit])


def get_citations(s2_paper_id: str, limit: int = 10) -> list[dict]:
    """Fetch papers that cite the given paper. Includes citation intent and context snippets."""
    data = _get(f"/graph/v1/paper/{s2_paper_id}/citations", {"fields": _CITATION_FIELDS, "limit": min(limit, 1000)})
    citations: list[dict] = []
    for citation in (data.get("data") or []):
        try:
            citations.append(_standardize_citation(citation))
        except Exception as exc:  # noqa: BLE001 - a bad record is skipped, never fatal
            logger.warning("Skipping unparseable S2 citation: %s", exc)
    return citations


def enrich_paper_by_doi(doi: str) -> dict | None:
    """Convenience: fetch S2 enrichment (TLDR, influence) for a paper identified by DOI."""
    return get_paper(f"DOI:{doi}") if doi else None
