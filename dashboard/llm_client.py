"""
dashboard/llm_client.py — OpenRouter chat client for RAG synthesis.

SRP: One job — send a prompt to OpenRouter and return the completion text.
     No SQL, no vector search, no Flask. The retrieval half of RAG lives in
     the repository layer; the orchestration lives in search_service.

Provider and model come from config, so local dev and every deployment share
one code path. The model is whatever OPENROUTER_MODEL names — the original
`openai/gpt-oss-120b:free` was retired by OpenRouter and now 404s.
"""

import asyncio
import logging
import threading
import time

import httpx

from config import (OPENROUTER_API_KEY, OPENROUTER_API_KEYS, OPENROUTER_BASE_URL,
                    OPENROUTER_MODEL)
from exceptions import ExternalAPIError, LLMTimeoutError

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
    """True when a credential is configured — routes use this to hide RAG UI gracefully."""
    return bool(OPENROUTER_API_KEYS)


# =============================================================================
# Provider failover
# =============================================================================
# Statuses that mean "this credential cannot serve this request", as opposed to
# "this request is wrong". Only the first kind is worth trying elsewhere:
#
#   401 / 403  the credential is rejected — revoked, expired, or not permitted
#   402        the account cannot pay for it
#   429        the provider is refusing for now
#
# A 400 is NOT here, and that is the important half. A malformed request will
# be malformed for every credential, so retrying it spends the next one to
# learn nothing. The same reasoning excludes 404: a retired model slug is
# retired everywhere.
_FAILOVER_STATUSES = frozenset({401, 402, 403, 429})

# What a visitor is told when the model cannot be reached, whatever the reason.
#
# One sentence, no status code, no provider name, and an instruction they can
# act on. `LLM request failed (429): ...` told somebody researching protein
# folding about HTTP semantics and gave them nothing to do about it.
_UNAVAILABLE_MESSAGE = (
    "The research assistant is unavailable right now. "
    "Please try again in a few minutes."
)


def _refusal_detail(resp) -> str:
    """
    The provider's own explanation, for the log and the telemetry row.

    Never shown to a visitor. It is the thing that makes a refusal diagnosable
    later — "rate limit exceeded" and "key disabled" need very different
    responses from whoever runs the deployment, and both look identical once
    they have been flattened into a friendly sentence.
    """
    try:
        body = resp.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        return str(body)
    return (getattr(resp, "text", "") or "").strip() or "<empty response body>"

# Which credential to reach for first. Advanced only when one fails over, so a
# working credential keeps being used and the others stay untouched.
#
# In-process and deliberately not persisted. The state is worth nothing beyond
# the life of a worker — provider limits reset on their own schedule, and a
# restart rediscovering a now-working credential is the correct behaviour
# rather than a bug to engineer around.
_preferred = 0
_preferred_lock = threading.Lock()


def _credential_order() -> list[tuple[int, str]]:
    """Configured credentials, starting from the one that last worked."""
    total = len(OPENROUTER_API_KEYS)
    return [((_preferred + offset) % total, OPENROUTER_API_KEYS[(_preferred + offset) % total])
            for offset in range(total)]


def _prefer(index: int) -> None:
    global _preferred
    with _preferred_lock:
        _preferred = index


async def _request_openrouter(payload: dict, timeout: float, credential: str):
    """
    Perform one OpenRouter HTTP request.

    This helper is async so the caller can wrap the entire operation in
    asyncio.wait_for(). HTTP client read/connect timeouts protect individual
    socket operations; wait_for provides the separate wall-clock deadline the
    agent budget actually means.
    """
    socket_timeout = httpx.Timeout(
        timeout=timeout,
        connect=min(10.0, timeout),
        read=timeout,
        write=min(10.0, timeout),
        pool=min(10.0, timeout),
    )
    async with httpx.AsyncClient(timeout=socket_timeout) as client:
        return await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {credential}", **_HEADERS_EXTRA},
            json={"model": OPENROUTER_MODEL, **payload},
        )


def _post(payload: dict, timeout: float | None = None,
          usage: Usage | None = None) -> dict:
    """
    One OpenRouter call with two independent timeout protections.

    httpx bounds connect/read/write/pool waits. asyncio.wait_for bounds the
    whole operation by wall clock, which is the guarantee the agent deadline
    needs. The previous requests timeout only measured socket inactivity, so an
    upstream that kept the connection active could leave Alfred "Thinking..."
    for minutes despite a nominal ~55-second planner budget.
    """
    if not OPENROUTER_API_KEYS:
        raise ExternalAPIError("No OpenRouter credential is configured.")

    effective_timeout = timeout or _TIMEOUT_SECONDS
    started = time.monotonic()
    logger.info(
        "OpenRouter request start model=%s hard_timeout=%.1fs messages=%d tools=%d",
        OPENROUTER_MODEL,
        effective_timeout,
        len(payload.get("messages") or []),
        len(payload.get("tools") or []),
    )

    attempts = _credential_order()
    last_refusal: tuple[int, str] | None = None
    resp = None

    for position, (index, credential) in enumerate(attempts):
        try:
            resp = asyncio.run(asyncio.wait_for(
                _request_openrouter(payload, effective_timeout, credential),
                timeout=effective_timeout,
            ))
        except TimeoutError as exc:
            # Transport failures are not a credential's fault, so they do not
            # cause failover — trying the next one would spend it to learn
            # nothing and double the wait somebody is already enduring.
            elapsed = time.monotonic() - started
            logger.error(
                "OpenRouter wall-clock timeout after %.2fs model=%s limit=%.1fs",
                elapsed, OPENROUTER_MODEL, effective_timeout,
            )
            raise LLMTimeoutError(
                f"LLM request exceeded its {effective_timeout:.1f}s wall-clock deadline."
            ) from exc
        except httpx.TimeoutException as exc:
            elapsed = time.monotonic() - started
            logger.error(
                "OpenRouter socket timeout after %.2fs model=%s: %s",
                elapsed, OPENROUTER_MODEL, exc,
            )
            raise LLMTimeoutError(f"LLM request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            elapsed = time.monotonic() - started
            logger.error("OpenRouter request failed after %.2fs: %s", elapsed, exc)
            raise ExternalAPIError(_UNAVAILABLE_MESSAGE, detail=f"{type(exc).__name__}: {exc}") from exc

        if resp.status_code not in _FAILOVER_STATUSES:
            # Worked, or failed in a way the next credential would fail too.
            _prefer(index)
            break

        detail = _refusal_detail(resp)
        last_refusal = (resp.status_code, detail)
        logger.warning(
            "OpenRouter credential %d of %d unavailable status=%s: %s",
            position + 1, len(attempts), resp.status_code, detail[:200],
        )
    else:
        # Every configured credential came back unusable. The visitor gets one
        # sentence; the status and the provider's own words go to the log and
        # to ai_operations, which is where they are diagnosable.
        status, detail = last_refusal or (503, "no credential could be used")
        logger.error("Every OpenRouter credential (%d) is unavailable; last status=%s",
                     len(attempts), status)
        raise ExternalAPIError(_UNAVAILABLE_MESSAGE, status_code=status, detail=detail)

    elapsed = time.monotonic() - started

    # Read the provider body BEFORE classifying an HTTP error. OpenRouter often
    # explains a routing/model failure in JSON; preserving that detail is what
    # made the retired-model failure diagnosable in production.
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

        if usage is not None and isinstance(body, dict):
            usage.add(body)

        # A status the failover loop decided not to retry — a malformed
        # request, or a model slug that no longer exists. Still not a status
        # code for a visitor to read.
        raise ExternalAPIError(_UNAVAILABLE_MESSAGE,
                               status_code=resp.status_code,
                               detail=detail_text[:500])

    if not isinstance(body, dict):
        logger.error(
            "OpenRouter returned non-JSON success status=%s elapsed=%.2fs",
            resp.status_code,
            elapsed,
        )
        raise ExternalAPIError(_UNAVAILABLE_MESSAGE,
                               status_code=resp.status_code,
                               detail="non-JSON response body")

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

    if usage is not None:
        usage.add(body)

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
                    usage: Usage | None = None,
                    tool_choice: str | dict | None = None) -> dict:
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
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    body = _post(payload, timeout=timeout, usage=usage)
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response shape: %s", str(body)[:500])
        raise ExternalAPIError(_UNAVAILABLE_MESSAGE,
                               status_code=resp.status_code,
                               detail="non-JSON response body") from exc


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
        raise ExternalAPIError(_UNAVAILABLE_MESSAGE,
                               status_code=resp.status_code,
                               detail="non-JSON response body") from exc

    text = message_text(message)
    if not text:
        logger.error("LLM returned no usable text: %s", str(body)[:500])
        raise ExternalAPIError("LLM returned an empty answer.")
    return text
