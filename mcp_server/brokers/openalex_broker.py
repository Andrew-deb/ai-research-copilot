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

from config import (
    OPENALEX_BASE_URL,
    OPENALEX_EMAIL,
    OPENALEX_RATE_LIMIT_DELAY,
)

logger = logging.getLogger(__name__)

_OPENALEX_PREFIX = "https://openalex.org/"
_DOI_PREFIX = "https://doi.org/"

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


def _strip_prefix(value: Any, prefix: str) -> str | None:
    """
    Null-safe URL-prefix strip.

    OpenAlex sends an explicit `null` (not an absent key) for fields a work does
    not have: `doi` on preprints/theses/datasets, `author.id` on unmatched
    authorships. `dict.get(key, "")` returns that None — the default only fires
    when the key is *missing* — so calling .replace() on the result raises
    AttributeError. Every prefix strip goes through here.
    """
    if not value:
        return None
    return str(value).replace(prefix, "") or None


def _extract_institution(authorship: dict) -> str | None:
    institutions = authorship.get("institutions") or []
    return institutions[0].get("display_name") if institutions else None


def _standardize_work(work: dict) -> dict:
    """Map a raw OpenAlex Work object to our unified paper dict schema."""
    openalex_id = _strip_prefix(work.get("id"), _OPENALEX_PREFIX)
    doi = _strip_prefix(work.get("doi"), _DOI_PREFIX)

    location = work.get("primary_location") or {}
    venue = (location.get("source") or {}).get("display_name")
    oa_url = (work.get("open_access") or {}).get("oa_url")

    authors = []
    for idx, authorship in enumerate(work.get("authorships") or []):
        author = authorship.get("author") or {}
        display_name = author.get("display_name") or ""
        if not display_name:
            continue  # an authorship with no name is unusable downstream
        authors.append({
            "openalex_id": _strip_prefix(author.get("id"), _OPENALEX_PREFIX),
            "display_name": display_name,
            "institution": _extract_institution(authorship),
            "position": idx,
        })

    return {
        "openalex_id": openalex_id,
        "semantic_scholar_id": None,
        "doi": doi,
        "title": work.get("title") or "",
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index") or {}),
        "publication_year": work.get("publication_year"),
        "venue": venue,
        "citation_count": work.get("cited_by_count") or 0,
        "tldr": None,
        "influence_score": None,
        "source_api": "openalex",
        "open_access_url": oa_url,
        "payload": work,
        "_authors": authors,
    }


def _standardize_many(works: list[dict]) -> list[dict]:
    """
    Standardize a batch, skipping (and logging) any record we cannot parse.

    One malformed record must not cost the caller the entire result set — that
    is how a single authorship with `"id": null` turned into a failed search for
    the whole query.
    """
    standardized: list[dict] = []
    for work in works:
        try:
            standardized.append(_standardize_work(work))
        except Exception as exc:  # noqa: BLE001 - a bad record is skipped, never fatal
            # `work` may not even be a dict, so resolve the label defensively —
            # the handler itself must never raise.
            label = work.get("id") if isinstance(work, dict) else repr(work)[:80]
            logger.warning("Skipping unparseable OpenAlex work %r: %s", label, exc)
    return standardized


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
    return _standardize_many(data.get("results") or [])


def get_work(openalex_id: str) -> dict | None:
    """Fetch a single work by OpenAlex ID. Returns None if not found."""
    clean_id = _strip_prefix(openalex_id, _OPENALEX_PREFIX)
    if not clean_id:
        return None
    try:
        return _standardize_work(_get(f"/works/{clean_id}"))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def get_work_by_doi(doi: str) -> dict | None:
    """Fetch a work by DOI using OpenAlex's filter endpoint. Returns None if not found."""
    clean_doi = _strip_prefix(doi, _DOI_PREFIX)
    if not clean_doi:
        return None
    data = _get("/works", params={"filter": f"doi:{clean_doi}", "per-page": 1, "select": _SELECT_FIELDS})
    results = data.get("results") or []
    return _standardize_work(results[0]) if results else None


def get_author(openalex_author_id: str) -> dict | None:
    """Fetch an author profile by OpenAlex author ID. Returns None if not found."""
    clean_id = _strip_prefix(openalex_author_id, _OPENALEX_PREFIX)
    if not clean_id:
        return None
    try:
        data = _get(f"/authors/{clean_id}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    affiliations = data.get("affiliations") or []
    institution = (affiliations[0].get("institution") or {}).get("display_name") if affiliations else None
    return {"openalex_id": clean_id, "s2_id": None, "display_name": data.get("display_name") or "", "institution": institution}


def get_cited_by(openalex_work_id: str, limit: int = 10) -> list[dict]:
    """Fetch papers that cite a given work (forward citations)."""
    clean_id = _strip_prefix(openalex_work_id, _OPENALEX_PREFIX)
    if not clean_id:
        return []
    data = _get("/works", params={
        "filter": f"cites:{clean_id}",
        "per-page": min(limit, 200),
        "sort": "cited_by_count:desc",
        "select": _SELECT_FIELDS,
    })
    return _standardize_many(data.get("results") or [])
