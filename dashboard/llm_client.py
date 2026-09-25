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
import time

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


class Usage:
    """
    What a set of LLM calls cost, summed across the turns of one operation.

    A sink passed down rather than a value returned. `chat_with_tools` has to
    return the assistant message verbatim - the caller appends it to the message
    list unchanged - and one agent turn makes several calls whose cost only
    means anything added up. An accumulator keeps both properties.

    Not a module global and not a thread-local: a streamed turn runs on a worker
    thread the server reuses, and a tally left behind on one is the last
    visitor's spend attributed to this one. Passed explicitly, it cannot outlive
    the operation that made it.
    """

    __slots__ = ("input_tokens", "output_tokens", "estimated_cost_usd",
                 "provider", "model", "calls")

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.estimated_cost_usd = 0.0
        self.provider: str | None = None
        self.model: str | None = None
        self.calls = 0

    def add(self, body: dict) -> None:
        """
        Fold one OpenRouter response into the running total.

        Never raises. A measurement that fails is a gap in a calibration sample;
        an exception here would cost the visitor the answer they already spent
        their allowance on, which is the trade telemetry_service refuses to make
        and this has to refuse for the same reason.
        """
        try:
            self.calls += 1
            self.provider = "openrouter"
            # Which model ANSWERED, not which was asked for: OpenRouter picks a
            # provider and can fall back to a different one mid-conversation, so
            # the request's model name is a hope and this is the fact.
            self.model = body.get("model") or self.model

            usage = body.get("usage") or {}
            self.input_tokens += int(usage.get("prompt_tokens") or 0)
            # completion_tokens already counts reasoning tokens, which matters
            # here more than usual: the configured model routinely puts a whole
            # answer in `reasoning` rather than `content` (see message_text).
            self.output_tokens += int(usage.get("completion_tokens") or 0)

            # Credits, which OpenRouter denominates 1:1 with USD. Absent on some
            # providers, so a missing cost stays absent rather than becoming a
            # zero that would quietly drag a calibration average down.
            cost = usage.get("cost")
            if cost is not None:
                self.estimated_cost_usd += float(cost)
        except (AttributeError, TypeError, ValueError):
            logger.debug("Unreadable usage block in an OpenRouter response",
                         exc_info=True)


def is_available() -> bool:
    """True when an API key is configured — routes use this to hide RAG UI gracefully."""
    return bool(OPENROUTER_API_KEY)


def _post(payload: dict, timeout: float | None = None,
          usage: Usage | None = None) -> dict:
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

    effective_timeout = timeout or _TIMEOUT_SECONDS
    started = time.monotonic()
    logger.info(
        "OpenRouter request start model=%s timeout=%.1fs messages=%d tools=%d",
        OPENROUTER_MODEL,
        effective_timeout,
        len(payload.get("messages") or []),
        len(payload.get("tools") or []),
    )

    try:
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", **_HEADERS_EXTRA},
            json={"model": OPENROUTER_MODEL, **payload},
            timeout=effective_timeout,
        )
    except requests.RequestException as exc:
        elapsed = time.monotonic() - started
        logger.error("OpenRouter request failed after %.2fs: %s", elapsed, exc)
        raise ExternalAPIError(f"LLM request failed: {exc}") from exc

    elapsed = time.monotonic() - started

    # Read the provider body BEFORE classifying an HTTP error. OpenRouter often
    # explains a routing/model failure in JSON, and calling raise_for_status()
    # first reduced a useful error such as "no endpoints found" to a generic
    # "404 Not Found", which hid the actual production failure.
    try:
        body = resp.json()
    except ValueError:
        body = None

    if resp.status_code >= 400:
        if isinstance(body, dict):
            detail = body.get("error") or body
        else:
            detail = (getattr(resp, "text", "") or "").strip() or "<empty response body>"

        detail_text = str(detail)
        logger.error(
            "OpenRouter request rejected status=%s elapsed=%.2fs model=%s body=%s",
            resp.status_code,
            elapsed,
            OPENROUTER_MODEL,
            detail_text[:500],
        )

        # A rejected request may still report usage. Keep it in calibration
        # telemetry if OpenRouter supplied it, just as we do for HTTP 200 error
        # bodies below.
        if usage is not None and isinstance(body, dict):
            usage.add(body)

        raise ExternalAPIError(
            f"LLM request failed ({resp.status_code}): {detail_text[:200]}"
        )

    if not isinstance(body, dict):
        logger.error(
            "OpenRouter returned non-JSON success status=%s elapsed=%.2fs",
            resp.status_code,
            elapsed,
        )
        raise ExternalAPIError("LLM returned an unexpected response format.")

    response_usage = body.get("usage") or {}
    logger.info(
        "OpenRouter response model=%s status=%s elapsed=%.2fs prompt_tokens=%s "
        "completion_tokens=%s tool_calls=%d",
        body.get("model") or OPENROUTER_MODEL,
        resp.status_code,
        elapsed,
        response_usage.get("prompt_tokens"),
        response_usage.get("completion_tokens"),
        len(((body.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []),
    )
    # Counted before the error check: a 200 carrying an error object can still
    # have burned input tokens, and an operation that failed expensively is
    # exactly the one calibration must not miss.
    if usage is not None:
        usage.add(body)

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
                    timeout: float | None = None,
                    usage: Usage | None = None) -> dict:
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

    body = _post(payload, timeout=timeout, usage=usage)
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response shape: %s", str(body)[:500])
        raise ExternalAPIError("LLM returned an unexpected response format.") from exc


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.2,
         max_tokens: int = 1024, usage: Usage | None = None) -> str:
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
    }, usage=usage)

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
