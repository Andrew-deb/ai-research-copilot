"""
dashboard/llm_client.py — OpenRouter chat client for RAG synthesis.

SRP: One job — send a prompt to OpenRouter and return the completion text.
     No SQL, no vector search, no Flask. The retrieval half of RAG lives in
     the repository layer; the orchestration lives in search_service.

Same provider/model as Day 2 and the agent (`openai/gpt-oss-120b:free`),
read from config so local dev and Databricks deployment share one code path.
"""

import logging

import requests

from dashboard.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from dashboard.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60
_HEADERS_EXTRA = {
    # OpenRouter attribution headers — optional but recommended.
    "HTTP-Referer": "https://github.com/ai-research-copilot",
    "X-Title": "AI Research & Learning Copilot",
}


def is_available() -> bool:
    """True when an API key is configured — routes use this to hide RAG UI gracefully."""
    return bool(OPENROUTER_API_KEY)


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 1024) -> str:
    """
    Send a two-message conversation to OpenRouter and return the assistant text.

    Raises ExternalAPIError on missing key, network failure, or a non-2xx response
    so the Flask error handler can turn it into a single user-facing message.
    """
    if not OPENROUTER_API_KEY:
        raise ExternalAPIError("OPENROUTER_API_KEY is not configured — RAG summaries are unavailable.")

    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", **_HEADERS_EXTRA},
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("OpenRouter request failed: %s", exc)
        raise ExternalAPIError(f"LLM request failed: {exc}") from exc

    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("Unexpected OpenRouter response shape: %s", resp.text[:500])
        raise ExternalAPIError("LLM returned an unexpected response format.") from exc
