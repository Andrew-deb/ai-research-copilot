"""
mcp_server/brokers/wikipedia_broker.py — Wikipedia REST API client.

SRP: HTTP calls to Wikipedia only. No DB access, no Flask, no other brokers.

Provides plain-language topic summaries (prerequisite context) that the agent
uses before presenting academic papers to users unfamiliar with the field.
No API key needed — identified via User-Agent header only.
"""

import logging
import time
import urllib.parse

import requests

from mcp_server.config import OPENALEX_EMAIL, WIKIPEDIA_BASE_URL, WIKIPEDIA_RATE_LIMIT_DELAY

logger = logging.getLogger(__name__)

_USER_AGENT = f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL}; AI research assistant)"


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> dict:
    time.sleep(WIKIPEDIA_RATE_LIMIT_DELAY)
    resp = requests.get(
        f"{WIKIPEDIA_BASE_URL}{endpoint}",
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        params=params or {},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Standardization
# ---------------------------------------------------------------------------

def _standardize_summary(summary: dict) -> dict:
    """Map a Wikipedia REST summary object to our topic_context schema."""
    desktop = (summary.get("content_urls") or {}).get("desktop") or {}
    return {
        "topic_name": summary.get("title", ""),
        "wikipedia_summary": summary.get("extract"),
        "wiki_url": desktop.get("page"),
        "_description": summary.get("description"),
        "_display_title": summary.get("displaytitle"),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_topic_summary(topic: str) -> dict | None:
    """
    Fetch the Wikipedia summary (first paragraph) for a topic.

    Spaces are replaced with underscores per Wikipedia's URL convention.
    Returns None if the article is not found.
    """
    # Wikipedia convention: spaces → underscores, safe="()" preserves disambiguation parens
    encoded = urllib.parse.quote(topic.replace(" ", "_"), safe="()")
    try:
        return _standardize_summary(_get(f"/page/summary/{encoded}"))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.warning("Wikipedia topic not found: %r", topic)
            return None
        raise


def search_topics(query: str, limit: int = 5) -> list[dict]:
    """
    Search Wikipedia for article titles matching a query.

    Uses the open-search (action API) endpoint which returns titles and URLs.
    Call get_topic_summary() on a result to get the full content.
    """
    time.sleep(WIKIPEDIA_RATE_LIMIT_DELAY)
    resp = requests.get(
        "https://en.wikipedia.org/w/api.php",
        headers={"User-Agent": _USER_AGENT},
        params={"action": "opensearch", "search": query, "limit": min(limit, 10), "format": "json", "namespace": 0},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    titles = result[1] if len(result) > 1 else []
    urls = result[3] if len(result) > 3 else []
    return [{"title": t, "url": u} for t, u in zip(titles, urls)]
