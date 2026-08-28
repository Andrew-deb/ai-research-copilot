"""
mcp_server/brokers/openalex_broker.py — OpenAlex API Client

SINGLE RESPONSIBILITY: This module makes HTTP calls to the OpenAlex API
and returns standardized paper/author dicts. It does NOT:
  - Touch the database
  - Import Flask or MCP
  - Know about any other broker

OpenAlex (https://openalex.org) is an open catalog of global research with
250M+ works. It provides paper metadata, author profiles, institution data,
topic classification, citation counts, and open-access URLs.

RATE LIMITING STRATEGY (Polite Pool):
  OpenAlex has two request pools:
    - Anonymous pool: 10 req/sec, shared with all unauthenticated users
    - Polite pool:    10 req/sec, dedicated lane for identified users

  To join the polite pool, simply add your email to the User-Agent header
  or the `mailto` query parameter. No formal API key or sign-up needed.
  We do both for maximum reliability.

STANDARDIZATION:
  OpenAlex returns paper data as "Works" with a specific JSON shape.
  This broker normalizes that shape into a flat dict that maps directly
  to the `papers` table columns. This is the "standardization" step
  that makes multi-source integration possible — Semantic Scholar and
  OpenAlex return very different JSON; our unified dict is the contract.
"""

import logging
import time
from typing import Any

import requests

from mcp_server.config import (
    OPENALEX_BASE_URL,
    OPENALEX_EMAIL,
    OPENALEX_RATE_LIMIT_DELAY,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> dict:
    """
    Make a GET request to the OpenAlex API.

    Adds the polite-pool identifier to both the User-Agent header and
    the mailto query param. Raises requests.HTTPError on 4xx/5xx.

    Args:
        endpoint: API path, e.g. "/works" or "/works/W2741809807"
        params:   Optional query parameters dict

    Returns:
        Parsed JSON response body as a dict
    """
    url = f"{OPENALEX_BASE_URL}{endpoint}"
    headers = {
        # Polite pool: include email in User-Agent (OpenAlex convention)
        "User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL})",
        "Accept": "application/json",
    }
    query = dict(params or {})
    if OPENALEX_EMAIL:
        # Also include as mailto param — belt AND suspenders
        query.setdefault("mailto", OPENALEX_EMAIL)

    # Respect the polite pool rate limit between consecutive calls
    time.sleep(OPENALEX_RATE_LIMIT_DELAY)

    resp = requests.get(url, headers=headers, params=query, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data standardization
# ---------------------------------------------------------------------------

def _standardize_work(work: dict) -> dict:
    """
    Normalize a raw OpenAlex Work object into our unified paper schema.

    OpenAlex Work shape (abbreviated):
    {
      "id": "https://openalex.org/W2741809807",
      "doi": "https://doi.org/10.48550/arXiv.1706.03762",
      "title": "Attention Is All You Need",
      "publication_year": 2017,
      "cited_by_count": 120000,
      "primary_location": {"source": {"display_name": "NeurIPS"}},
      "abstract_inverted_index": {...},   ← inverted index, needs reconstruction
      "open_access": {"oa_url": "https://..."},
      "authorships": [{"author": {"id": ..., "display_name": ...}, ...}]
    }

    We extract and flatten this into the columns our `papers` table expects.
    """
    # Extract clean IDs
    raw_id = work.get("id", "")
    openalex_id = raw_id.replace("https://openalex.org/", "") if raw_id else None

    raw_doi = work.get("doi", "")
    doi = raw_doi.replace("https://doi.org/", "") if raw_doi else None

    # Reconstruct abstract from the inverted index
    # OpenAlex stores abstracts as {word: [position, ...]} for copyright reasons
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index") or {})

    # Extract venue name
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    venue = source.get("display_name")

    # Extract open-access URL
    open_access = work.get("open_access") or {}
    oa_url = open_access.get("oa_url")

    # Extract authors as a list of dicts
    authorships = work.get("authorships") or []
    authors = [
        {
            "openalex_id": (auth.get("author") or {}).get("id", "").replace(
                "https://openalex.org/", ""
            ),
            "display_name": (auth.get("author") or {}).get("display_name", ""),
            "institution": _extract_institution(auth),
            "position": idx,
        }
        for idx, auth in enumerate(authorships)
    ]

    return {
        # Standardized fields (map to `papers` table columns)
        "openalex_id": openalex_id,
        "semantic_scholar_id": None,     # Filled by S2 enrichment step
        "doi": doi,
        "title": work.get("title") or "",
        "abstract": abstract,
        "publication_year": work.get("publication_year"),
        "venue": venue,
        "citation_count": work.get("cited_by_count", 0),
        "tldr": None,                    # Filled by S2 enrichment step
        "influence_score": None,         # Filled by S2 enrichment step
        "source_api": "openalex",
        "open_access_url": oa_url,
        "payload": work,                 # Raw response for audit/future use
        # Authors list (used by discovery_service to upsert into authors table)
        "_authors": authors,
    }


def _reconstruct_abstract(inverted_index: dict) -> str | None:
    """
    Reconstruct a readable abstract from OpenAlex's inverted-index format.

    OpenAlex avoids copyright issues by storing abstracts as an inverted index:
    {
        "The": [0, 15],
        "dominant": [1],
        "sequence": [2],
        ...
    }
    Each key is a word; each value is a list of its positions in the text.
    We invert back: position → word, then sort and join.
    """
    if not inverted_index:
        return None

    position_word: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            position_word[pos] = word

    if not position_word:
        return None

    return " ".join(position_word[i] for i in sorted(position_word))


def _extract_institution(authorship: dict) -> str | None:
    """Extract the first institution name from an authorship entry."""
    institutions = authorship.get("institutions") or []
    if institutions:
        return institutions[0].get("display_name")
    return None


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def search_works(
    query: str,
    filters: dict | None = None,
    per_page: int = 10,
    page: int = 1,
) -> list[dict]:
    """
    Search OpenAlex works (papers) by keyword query.

    Uses OpenAlex's full-text search across titles and abstracts.
    Results are sorted by relevance score by default.

    Args:
        query:    Free-text search string, e.g. "transformer attention mechanism"
        filters:  Optional OpenAlex filter dict, e.g. {"publication_year": 2020}
        per_page: Number of results (max 200 per OpenAlex docs)
        page:     Pagination page number

    Returns:
        List of standardized paper dicts (empty list if no results).

    Example:
        >>> papers = search_works("BERT language model", per_page=5)
        >>> papers[0]["title"]
        'BERT: Pre-training of Deep Bidirectional Transformers...'
    """
    params: dict[str, Any] = {
        "search": query,
        "per-page": per_page,
        "page": page,
        # Request only the fields we need — reduces response size
        "select": (
            "id,doi,title,publication_year,cited_by_count,"
            "primary_location,abstract_inverted_index,"
            "open_access,authorships"
        ),
    }

    # Add any extra filters (e.g., publication_year, open_access.is_oa)
    if filters:
        filter_str = ",".join(f"{k}:{v}" for k, v in filters.items())
        params["filter"] = filter_str

    logger.info("OpenAlex search: query=%r per_page=%d page=%d", query, per_page, page)

    data = _get("/works", params=params)
    results = data.get("results") or []

    logger.info("OpenAlex returned %d results", len(results))
    return [_standardize_work(w) for w in results]


def get_work(openalex_id: str) -> dict | None:
    """
    Fetch a single work by its OpenAlex ID.

    Args:
        openalex_id: OpenAlex ID with or without prefix, e.g.
                     "W2741809807" or "https://openalex.org/W2741809807"

    Returns:
        Standardized paper dict, or None if not found.
    """
    # Normalize ID — strip prefix if present
    clean_id = openalex_id.replace("https://openalex.org/", "")

    logger.info("OpenAlex get_work: id=%s", clean_id)

    try:
        data = _get(f"/works/{clean_id}")
        return _standardize_work(data)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("OpenAlex work not found: %s", clean_id)
            return None
        raise


def get_work_by_doi(doi: str) -> dict | None:
    """
    Fetch a single work by DOI using OpenAlex's filter endpoint.

    Args:
        doi: DOI string, e.g. "10.48550/arXiv.1706.03762"
             (with or without "https://doi.org/" prefix)

    Returns:
        Standardized paper dict, or None if not found.
    """
    clean_doi = doi.replace("https://doi.org/", "")
    logger.info("OpenAlex get_work_by_doi: doi=%s", clean_doi)

    data = _get("/works", params={"filter": f"doi:{clean_doi}", "per-page": 1})
    results = data.get("results") or []

    if not results:
        logger.warning("OpenAlex: no work found for DOI %s", clean_doi)
        return None

    return _standardize_work(results[0])


def get_author(openalex_author_id: str) -> dict | None:
    """
    Fetch an author profile by OpenAlex author ID.

    Args:
        openalex_author_id: e.g. "A2208157607"

    Returns:
        Dict with author fields, or None if not found.
    """
    clean_id = openalex_author_id.replace("https://openalex.org/", "")
    logger.info("OpenAlex get_author: id=%s", clean_id)

    try:
        data = _get(f"/authors/{clean_id}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise

    affiliations = data.get("affiliations") or []
    institution = None
    if affiliations:
        inst = (affiliations[0].get("institution") or {})
        institution = inst.get("display_name")

    return {
        "openalex_id": clean_id,
        "s2_id": None,
        "display_name": data.get("display_name", ""),
        "institution": institution,
    }


def get_cited_by(openalex_work_id: str, limit: int = 10) -> list[dict]:
    """
    Fetch papers that cite a given work (forward citations).

    Args:
        openalex_work_id: ID of the work to find citations for
        limit:            Maximum number of citing papers to return

    Returns:
        List of standardized paper dicts for citing works.
    """
    clean_id = openalex_work_id.replace("https://openalex.org/", "")
    logger.info("OpenAlex get_cited_by: id=%s limit=%d", clean_id, limit)

    data = _get(
        "/works",
        params={
            "filter": f"cites:{clean_id}",
            "per-page": min(limit, 200),
            "sort": "cited_by_count:desc",
            "select": (
                "id,doi,title,publication_year,cited_by_count,"
                "primary_location,abstract_inverted_index,"
                "open_access,authorships"
            ),
        },
    )
    return [_standardize_work(w) for w in (data.get("results") or [])]
