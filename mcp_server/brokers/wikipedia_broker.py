"""
mcp_server/brokers/wikipedia_broker.py — Wikipedia REST API Client

SINGLE RESPONSIBILITY: This module makes HTTP calls to the Wikipedia REST API
and returns topic summary data. It does NOT:
  - Touch the database
  - Import Flask or MCP
  - Know about any other broker

Wikipedia serves as the "prerequisite knowledge" layer in our system.
When a user is exploring an unfamiliar research area, the agent can call
explain_topic to fetch a plain-language Wikipedia summary that provides
context BEFORE diving into the academic papers. This is particularly
valuable for interdisciplinary learners.

WHY WIKIPEDIA (vs. a search engine or LLM knowledge)?
  - Freely accessible, no API key required
  - Stable, citable summaries with consistent quality
  - REST API is simple, fast, and returns clean structured text
  - Wikipedia articles often contain prerequisite topic links
    which we can use to suggest follow-up topics

RATE LIMITING STRATEGY:
  Wikipedia's REST API has no formal rate limit for reasonable usage,
  but their guidelines recommend:
    - Max 200 requests/second
    - Include a descriptive User-Agent so they can contact you if needed

  We add a small delay anyway to be polite and to avoid any transient
  throttling from bursty notebook runs.

API USED:
  Wikipedia REST API v1 (not the Action API, not the Wikimedia API)
  Endpoint: https://en.wikipedia.org/api/rest_v1/page/summary/{title}
  Docs: https://en.wikipedia.org/api/rest_v1/
"""

import logging
import time
import urllib.parse

import requests

from mcp_server.config import (
    OPENALEX_EMAIL,
    WIKIPEDIA_BASE_URL,
    WIKIPEDIA_RATE_LIMIT_DELAY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> dict:
    """
    Make a GET request to the Wikipedia REST API.

    Includes a descriptive User-Agent as Wikipedia's API guidelines require.
    Returns parsed JSON response body.

    Args:
        endpoint: API path, e.g. "/page/summary/Transformer_(deep_learning)"
        params:   Optional query parameters

    Returns:
        Parsed JSON response body

    Raises:
        requests.HTTPError: On 4xx/5xx responses
    """
    url = f"{WIKIPEDIA_BASE_URL}{endpoint}"
    headers = {
        # Wikipedia requires a meaningful User-Agent with contact info
        "User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL}; AI research assistant)",
        "Accept": "application/json",
    }

    time.sleep(WIKIPEDIA_RATE_LIMIT_DELAY)

    resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data standardization
# ---------------------------------------------------------------------------

def _standardize_summary(summary: dict) -> dict:
    """
    Normalize a Wikipedia page summary object into our topic_context schema.

    Wikipedia REST summary shape:
    {
      "type": "standard",
      "title": "Transformer (deep learning architecture)",
      "displaytitle": "Transformer...",
      "description": "Type of neural network architecture",
      "extract": "A transformer is a deep learning architecture...",
      "extract_html": "<p>A transformer is...</p>",
      "content_urls": {
        "desktop": {"page": "https://en.wikipedia.org/wiki/..."},
        "mobile": {...}
      },
      "thumbnail": {"source": "https://..."},
      "originalimage": {...}
    }

    We extract only the text summary and URL we need for the topic_context table.
    """
    content_urls = summary.get("content_urls") or {}
    desktop_urls = content_urls.get("desktop") or {}
    wiki_url = desktop_urls.get("page")

    return {
        "topic_name": summary.get("title", ""),
        "wikipedia_summary": summary.get("extract"),
        "wiki_url": wiki_url,
        # Additional fields useful for the agent but not stored in topic_context
        "_description": summary.get("description"),
        "_display_title": summary.get("displaytitle"),
    }


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def get_topic_summary(topic: str) -> dict | None:
    """
    Fetch a Wikipedia summary for a given topic.

    Performs URL-encoding on the topic name so topics with spaces and
    special characters (e.g., "Transformer (deep learning)") work correctly.

    The summary endpoint returns the first paragraph of the Wikipedia article —
    typically 2-4 sentences providing a high-level definition. This is the
    content that populates the topic_context table and appears in the
    agent's explain_topic tool response.

    Args:
        topic: Topic name as it would appear on Wikipedia, e.g.:
               "Transformer (deep learning architecture)"
               "Reinforcement learning"
               "Natural language processing"

    Returns:
        Standardized topic dict, or None if the topic is not found.

    Example:
        >>> result = get_topic_summary("Attention mechanism (machine learning)")
        >>> result["wikipedia_summary"]
        'In neural networks, attention is a technique that...'
    """
    # URL-encode the topic: spaces → underscores (Wikipedia convention)
    # then percent-encode any remaining special characters
    encoded_topic = urllib.parse.quote(topic.replace(" ", "_"), safe="()")

    logger.info("Wikipedia get_topic_summary: topic=%r", topic)

    try:
        data = _get(f"/page/summary/{encoded_topic}")
        return _standardize_summary(data)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("Wikipedia: topic not found: %r", topic)
            return None
        raise


def search_topics(query: str, limit: int = 5) -> list[dict]:
    """
    Search Wikipedia for topic titles matching a query.

    Uses Wikipedia's open-search endpoint which returns article titles
    and brief descriptions — useful for helping the agent suggest
    prerequisite reading topics related to a paper's subject area.

    Note: open-search returns title strings, not full summaries.
    Call get_topic_summary() on each title to get the full content.

    Args:
        query: Search term, e.g. "attention neural network"
        limit: Maximum results to return (max 10 for Wikipedia open-search)

    Returns:
        List of dicts with "title" and "url" keys.

    Example:
        >>> topics = search_topics("transformer architecture", limit=3)
        >>> [t["title"] for t in topics]
        ['Transformer (deep learning architecture)', 'BERT (language model)', ...]
    """
    logger.info("Wikipedia search_topics: query=%r limit=%d", query, limit)

    # Wikipedia open-search returns a 4-element list:
    # [query, [titles], [descriptions], [urls]]
    time.sleep(WIKIPEDIA_RATE_LIMIT_DELAY)

    resp = requests.get(
        "https://en.wikipedia.org/w/api.php",
        headers={
            "User-Agent": f"ResearchCopilot/1.0 (mailto:{OPENALEX_EMAIL})",
            "Accept": "application/json",
        },
        params={
            "action": "opensearch",
            "search": query,
            "limit": min(limit, 10),
            "format": "json",
            "namespace": 0,     # Main article namespace only
        },
        timeout=10,
    )
    resp.raise_for_status()

    result = resp.json()
    # result = [query, [titles], [descriptions], [urls]]
    titles = result[1] if len(result) > 1 else []
    urls = result[3] if len(result) > 3 else []

    return [
        {"title": title, "url": url}
        for title, url in zip(titles, urls)
    ]
