"""
mcp_server/brokers/openalex_broker.py — OpenAlex API client.

SRP: HTTP calls to OpenAlex only. No DB access, no Flask, no other brokers.

Standardizes OpenAlex "Works" JSON into the unified paper dict shape
expected by the papers table. Joins the polite request pool via email
header so calls get a dedicated rate-limit lane.
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

# Only request fields we actually use — keeps responses small
_SELECT_FIELDS = (
    "id,doi,title,publication_year,cited_by_count,"
    "primary_location,abstract_inverted_index,open_access,authorships"
)


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> dict:
    """GET from OpenAlex with polite-pool headers and rate-limit delay."""
    url = f"{OPENALEX_BASE_URL}{endpoint}"
    headers = {"User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL})"}
    query = dict(params or {})
    query.setdefault("mailto", OPENALEX_EMAIL)
    time.sleep(OPENALEX_RATE_LIMIT_DELAY)
    resp = requests.get(url, headers=headers, params=query, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Standardization helpers
# ---------------------------------------------------------------------------

def _reconstruct_abstract(inverted_index: dict) -> str | None:
    """
    Rebuild plain text from OpenAlex's inverted-index abstract format.
    OpenAlex stores abstracts as {word: [positions]} to avoid copyright issues.
    """
    if not inverted_index:
        return None
    pos_word: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            pos_word[pos] = word
    return " ".join(pos_word[i] for i in sorted(pos_word)) if pos_word else None


def _extract_institution(authorship: dict) -> str | None:
    institutions = authorship.get("institutions") or []
    return institutions[0].get("display_name") if institutions else None


def _standardize_work(work: dict) -> dict:
    """Map a raw OpenAlex Work object to our unified paper dict schema."""
    raw_id = work.get("id", "")
    openalex_id = raw_id.replace("https://openalex.org/", "") or None

    raw_doi = work.get("doi", "")
    doi = raw_doi.replace("https://doi.org/", "") or None

    location = work.get("primary_location") or {}
    venue = (location.get("source") or {}).get("display_name")
    oa_url = (work.get("open_access") or {}).get("oa_url")

    authors = [
        {
            "openalex_id": (a.get("author") or {}).get("id", "").replace("https://openalex.org/", ""),
            "display_name": (a.get("author") or {}).get("display_name", ""),
            "institution": _extract_institution(a),
            "position": idx,
        }
        for idx, a in enumerate(work.get("authorships") or [])
    ]

    return {
        "openalex_id": openalex_id,
        "semantic_scholar_id": None,
        "doi": doi,
        "title": work.get("title") or "",
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index") or {}),
        "publication_year": work.get("publication_year"),
        "venue": venue,
        "citation_count": work.get("cited_by_count", 0),
        "tldr": None,
        "influence_score": None,
        "source_api": "openalex",
        "open_access_url": oa_url,
        "payload": work,
        "_authors": authors,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_works(query: str, filters: dict | None = None, per_page: int = 10, page: int = 1) -> list[dict]:
    """Search OpenAlex works by keyword. Returns standardized paper dicts."""
    params: dict[str, Any] = {"search": query, "per-page": per_page, "page": page, "select": _SELECT_FIELDS}
    if filters:
        params["filter"] = ",".join(f"{k}:{v}" for k, v in filters.items())
    logger.info("OpenAlex search: %r (per_page=%d)", query, per_page)
    data = _get("/works", params=params)
    return [_standardize_work(w) for w in (data.get("results") or [])]


def get_work(openalex_id: str) -> dict | None:
    """Fetch a single work by OpenAlex ID. Returns None if not found."""
    clean_id = openalex_id.replace("https://openalex.org/", "")
    try:
        return _standardize_work(_get(f"/works/{clean_id}"))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def get_work_by_doi(doi: str) -> dict | None:
    """Fetch a work by DOI using OpenAlex's filter endpoint. Returns None if not found."""
    clean_doi = doi.replace("https://doi.org/", "")
    data = _get("/works", params={"filter": f"doi:{clean_doi}", "per-page": 1, "select": _SELECT_FIELDS})
    results = data.get("results") or []
    return _standardize_work(results[0]) if results else None


def get_author(openalex_author_id: str) -> dict | None:
    """Fetch an author profile by OpenAlex author ID. Returns None if not found."""
    clean_id = openalex_author_id.replace("https://openalex.org/", "")
    try:
        data = _get(f"/authors/{clean_id}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    affiliations = data.get("affiliations") or []
    institution = (affiliations[0].get("institution") or {}).get("display_name") if affiliations else None
    return {"openalex_id": clean_id, "s2_id": None, "display_name": data.get("display_name", ""), "institution": institution}


def get_cited_by(openalex_work_id: str, limit: int = 10) -> list[dict]:
    """Fetch papers that cite a given work (forward citations)."""
    clean_id = openalex_work_id.replace("https://openalex.org/", "")
    data = _get("/works", params={
        "filter": f"cites:{clean_id}",
        "per-page": min(limit, 200),
        "sort": "cited_by_count:desc",
        "select": _SELECT_FIELDS,
    })
    return [_standardize_work(w) for w in (data.get("results") or [])]
