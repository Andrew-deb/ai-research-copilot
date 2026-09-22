"""
dashboard/llm_client.py — OpenRouter chat client for RAG synthesis.

SRP: One job — send a prompt to OpenRouter and return the completion text.
     No SQL, no vector search, no Flask. The retrieval half of RAG lives in
     the repository layer; the orchestration lives in search_service.

Provider and model come from config, so local dev and every deployment share
one code path. The model is whatever OPENROUTER_MODEL names — the original
`openai/gpt-oss-120b:free` was retired by OpenRouter and now 404s.
"""

import logging

import requests

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from exceptions import ExternalAPIError

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


def _post(payload: dict, timeout: float | None = None) -> dict:
    """
    One OpenRouter call. Raises ExternalAPIError on anything that is not a 2xx.

    `timeout` lets a caller working to a deadline hand down what is left of it.
    Measured the hard way: a free-tier call hung and the whole turn ran **775
    seconds** without completing a single LLM turn, because the agent's
    wall-clock deadline is only checked between turns and this one never
    returned. Render's gunicorn would have killed the worker at 120s.

    Note this bounds each socket operation, not the total — `requests` has no
    total-request cap — but it turns an unbounded hang into a bounded one, and
    the caller can shrink it as its own budget runs down.
    """
    if not OPENROUTER_API_KEY:
        raise ExternalAPIError("OPENROUTER_API_KEY is not configured.")

    try:
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", **_HEADERS_EXTRA},
            json={"model": OPENROUTER_MODEL, **payload},
            timeout=timeout or _TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("OpenRouter request failed: %s", exc)
        raise ExternalAPIError(f"LLM request failed: {exc}") from exc

    body = resp.json()
    # OpenRouter can answer 200 with an error object in the body — a provider
    # rate limit arrives this way, so a status check alone misses it.
    if "error" in body:
        message = str(body["error"])
        logger.error("OpenRouter returned an error body: %s", message[:300])
        raise ExternalAPIError(f"LLM request failed: {message[:200]}")
    return body


def message_text(message: dict) -> str:
    """
    The assistant's prose, wherever the provider put it.

    Some models leave `content` empty and put the answer in `reasoning`; the
    configured one does exactly that on a turn following a tool result
    (measured: content=0 chars, reasoning=612). Reading only `content` there
    yields "" and the page renders a blank answer with no error raised
    anywhere, which looks like broken retrieval rather than broken parsing.
    """
    return ((message.get("content") or "").strip()
            or (message.get("reasoning") or "").strip())


def chat_with_tools(messages: list[dict], tools: list[dict],
                    temperature: float = 0.2, max_tokens: int = 1024,
                    timeout: float | None = None) -> dict:
    """
    One turn of a tool-calling conversation. Returns the raw assistant message.

    `tools` is resent on every turn, including turns that follow a tool result.
    Dropping them once the model has what it needs looks like an optimisation
    and is not: measured, the model then emits raw `<tool_call>` XML as prose
    instead of answering.

    Returning the message unchanged rather than a parsed shape keeps the
    tool-call loop's bookkeeping in one place — the caller has to append this
    verbatim to the message list for the next turn to make sense.
    """
    payload = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools

    body = _post(payload, timeout=timeout)
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response shape: %s", str(body)[:500])
        raise ExternalAPIError("LLM returned an unexpected response format.") from exc


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 1024) -> str:
    """
    Send a two-message conversation to OpenRouter and return the assistant text.

    Raises ExternalAPIError on missing key, network failure, or a non-2xx response
    so the Flask error handler can turn it into a single user-facing message.
    """
    if not OPENROUTER_API_KEY:
        raise ExternalAPIError("OPENROUTER_API_KEY is not configured — RAG summaries are unavailable.")

    body = _post({
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    })

    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response shape: %s", str(body)[:500])
        raise ExternalAPIError("LLM returned an unexpected response format.") from exc

    text = message_text(message)
    if not text:
        logger.error("LLM returned no usable text: %s", str(body)[:500])
        raise ExternalAPIError("LLM returned an empty answer.")
    return text
