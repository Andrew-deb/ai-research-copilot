"""
mcp_server/brokers/semantic_scholar_broker.py — Semantic Scholar API Client

SINGLE RESPONSIBILITY: This module makes HTTP calls to the Semantic Scholar
Graph API and returns standardized enrichment data. It does NOT:
  - Touch the database
  - Import Flask or MCP
  - Know about any other broker

Semantic Scholar (https://www.semanticscholar.org) is an AI-powered academic
search engine from the Allen Institute for AI. Our primary use of S2 is
ENRICHMENT — we use OpenAlex as the discovery layer (search, browse) and
S2 to add:
  1. AI-generated TLDRs (one-sentence paper summaries)
  2. Influence scores (citation impact beyond raw count)
  3. "More like this" paper recommendations
  4. Structured citation data with context snippets

RATE LIMITING STRATEGY:
  S2 has two tiers:
    - Unauthenticated: 100 requests per 5 minutes (~0.33 req/sec)
    - API key (ours):   1 request per second

  We always use the API key if available. For calls where we process
  a batch (e.g., enrich 50 papers), we sleep 1.1s between calls to
  stay within limits and be a polite API consumer.

AUTHENTICATION:
  S2 authenticates via the x-api-key request header.
  The key is loaded from config (which reads from the Databricks secret
  scope or .env — the broker never reads secrets directly).
"""

import logging
import time

import requests

from mcp_server.config import (
    S2_API_KEY,
    S2_BASE_URL,
    S2_RATE_LIMIT_DELAY,
)

logger = logging.getLogger(__name__)

# Fields we request from S2 — only ask for what we use to minimize payload size
_PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,year,venue,"
    "citationCount,influentialCitationCount,tldr,"
    "openAccessPdf,authors"
)

_CITATION_FIELDS = (
    "paperId,externalIds,title,abstract,year,citationCount,"
    "tldr,intents,contexts"
)

_RECOMMENDATION_FIELDS = (
    "paperId,externalIds,title,abstract,year,venue,"
    "citationCount,tldr"
)


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> dict:
    """
    Make a GET request to the Semantic Scholar Graph API.

    Always includes the API key header if configured. Respects
    the per-request rate limit delay.

    Args:
        endpoint: API path, e.g. "/graph/v1/paper/DOI:10.xxx"
        params:   Optional query parameters

    Returns:
        Parsed JSON response body

    Raises:
        requests.HTTPError: On 4xx/5xx responses (caller decides how to handle)
    """
    url = f"{S2_BASE_URL}{endpoint}"
    headers = {"Accept": "application/json"}

    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    # Respect S2 rate limits between consecutive calls
    time.sleep(S2_RATE_LIMIT_DELAY)

    resp = requests.get(url, headers=headers, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(endpoint: str, body: dict) -> dict:
    """
    Make a POST request to the Semantic Scholar API.

    Used for batch operations (e.g., recommendations endpoint).
    """
    url = f"{S2_BASE_URL}{endpoint}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    time.sleep(S2_RATE_LIMIT_DELAY)

    resp = requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data standardization
# ---------------------------------------------------------------------------

def _standardize_paper(s2_paper: dict) -> dict:
    """
    Normalize a Semantic Scholar paper object into our enrichment dict.

    S2 paper shape (abbreviated):
    {
      "paperId": "abc123",
      "externalIds": {"DOI": "10.xxx", "ArXiv": "1706.03762"},
      "title": "Attention Is All You Need",
      "abstract": "...",
      "year": 2017,
      "citationCount": 120000,
      "influentialCitationCount": 8500,
      "tldr": {"text": "Introduces the Transformer architecture..."},
      "openAccessPdf": {"url": "https://..."},
      "authors": [{"authorId": "...", "name": "Vaswani, A."}]
    }

    We extract the enrichment fields that OpenAlex doesn't provide:
    tldr, influence_score, semantic_scholar_id, and the S2 author IDs.
    """
    external_ids = s2_paper.get("externalIds") or {}
    doi = external_ids.get("DOI")

    # TLDR: S2's one-sentence AI-generated summary
    tldr_obj = s2_paper.get("tldr") or {}
    tldr = tldr_obj.get("text")

    # Influence score: S2's proprietary citation impact metric
    # influentialCitationCount counts citations from high-impact papers
    influence = s2_paper.get("influentialCitationCount")

    # Open access PDF
    oa_pdf = s2_paper.get("openAccessPdf") or {}
    oa_url = oa_pdf.get("url")

    # Authors
    authors = [
        {
            "s2_id": a.get("authorId"),
            "display_name": a.get("name", ""),
        }
        for a in (s2_paper.get("authors") or [])
    ]

    return {
        "semantic_scholar_id": s2_paper.get("paperId"),
        "doi": doi,
        "title": s2_paper.get("title") or "",
        "abstract": s2_paper.get("abstract"),
        "publication_year": s2_paper.get("year"),
        "venue": s2_paper.get("venue"),
        "citation_count": s2_paper.get("citationCount", 0),
        "tldr": tldr,
        "influence_score": float(influence) if influence is not None else None,
        "source_api": "semantic_scholar",
        "open_access_url": oa_url,
        "payload": s2_paper,
        "_authors": authors,
    }


def _standardize_citation(citation_obj: dict) -> dict:
    """
    Normalize an S2 citation object which wraps a paper with extra metadata.

    S2 citation shape:
    {
      "citingPaper": { ...paper fields... },
      "intents": ["methodology", "background"],
      "contexts": ["As shown in [1], the transformer..."]
    }
    """
    citing = citation_obj.get("citingPaper") or {}
    paper = _standardize_paper(citing)

    # Attach citation-specific metadata (not in the papers table, but useful
    # for the agent when explaining WHY a paper cites another)
    paper["_citation_intents"] = citation_obj.get("intents") or []
    paper["_citation_contexts"] = citation_obj.get("contexts") or []
    return paper


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def get_paper(paper_id: str) -> dict | None:
    """
    Fetch a single paper's enrichment data from Semantic Scholar.

    Accepts multiple ID formats (S2 automatically routes them):
      - S2 paper ID:  "abc123def..."
      - DOI:          "DOI:10.48550/arXiv.1706.03762"
      - ArXiv ID:     "ARXIV:1706.03762"
      - MAG ID:       "MAG:2741809807"

    Args:
        paper_id: Paper identifier (S2 ID, DOI:xxx, ARXIV:xxx, MAG:xxx)

    Returns:
        Standardized enrichment dict, or None if the paper is not found.

    Example:
        >>> paper = get_paper("DOI:10.48550/arXiv.1706.03762")
        >>> paper["tldr"]
        'Introduces the Transformer architecture based solely on attention...'
    """
    logger.info("S2 get_paper: id=%s", paper_id)

    try:
        data = _get(
            f"/graph/v1/paper/{paper_id}",
            params={"fields": _PAPER_FIELDS},
        )
        return _standardize_paper(data)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("S2: paper not found: %s", paper_id)
            return None
        raise


def search_papers(query: str, limit: int = 10) -> list[dict]:
    """
    Search Semantic Scholar by keyword query.

    Note: For our primary use case, we use OpenAlex for search and S2 for
    enrichment. This function is available for cases where we want S2-specific
    results (e.g., papers with TLDRs that OpenAlex doesn't have).

    Args:
        query: Free-text search string
        limit: Maximum number of results (max 100 per S2 docs)

    Returns:
        List of standardized paper dicts.
    """
    logger.info("S2 search_papers: query=%r limit=%d", query, limit)

    data = _get(
        "/graph/v1/paper/search",
        params={
            "query": query,
            "limit": min(limit, 100),
            "fields": _PAPER_FIELDS,
        },
    )
    return [_standardize_paper(p) for p in (data.get("data") or [])]


def get_recommendations(s2_paper_id: str, limit: int = 5) -> list[dict]:
    """
    Fetch "more like this" paper recommendations from Semantic Scholar.

    S2's recommendation engine uses learned paper embeddings to find
    semantically similar papers — different from citation-based similarity.
    This is what powers the agent's `get_similar_papers` tool.

    Args:
        s2_paper_id: Semantic Scholar paper ID (not DOI — must be S2 format)
        limit:       Number of recommendations to fetch (max 500)

    Returns:
        List of standardized recommendation dicts.
    """
    logger.info("S2 get_recommendations: paper_id=%s limit=%d", s2_paper_id, limit)

    try:
        data = _post(
            "/recommendations/v1/papers",
            body={
                "positivePaperIds": [s2_paper_id],
                "negativePaperIds": [],
            },
        )
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("S2: no recommendations for paper_id=%s", s2_paper_id)
            return []
        raise

    papers = data.get("recommendedPapers") or []
    return [
        _standardize_paper(p)
        for p in papers[:limit]
    ]


def get_citations(s2_paper_id: str, limit: int = 10) -> list[dict]:
    """
    Fetch papers that cite a given paper (forward citations).

    Unlike raw citation counts, S2 provides citation CONTEXT — the actual
    sentence where the paper is cited and the intent (methodology, background,
    result, etc.). The agent uses this for the compare_papers tool.

    Args:
        s2_paper_id: Semantic Scholar paper ID
        limit:       Number of citing papers to fetch (max 1000)

    Returns:
        List of standardized paper dicts, each with _citation_intents
        and _citation_contexts fields attached.
    """
    logger.info("S2 get_citations: paper_id=%s limit=%d", s2_paper_id, limit)

    data = _get(
        f"/graph/v1/paper/{s2_paper_id}/citations",
        params={
            "fields": _CITATION_FIELDS,
            "limit": min(limit, 1000),
        },
    )
    return [_standardize_citation(c) for c in (data.get("data") or [])]


def enrich_paper_by_doi(doi: str) -> dict | None:
    """
    Convenience function: fetch S2 enrichment data using a DOI.

    This is the most common enrichment call — we have a paper from OpenAlex
    (which always has a DOI when available) and want to add the TLDR and
    influence score from S2.

    Args:
        doi: DOI string, e.g. "10.48550/arXiv.1706.03762"

    Returns:
        Standardized enrichment dict with tldr and influence_score, or None.
    """
    if not doi:
        return None
    return get_paper(f"DOI:{doi}")
