"""
dashboard/services/mcp_client.py — the dashboard's connection to the MCP server.

Render authenticates to Databricks as a **service principal**, server-side. The
browser never holds a Databricks token, never calls this server and never learns
its URL. When a tool needs to act for a particular user, that identity is passed
explicitly as a header — the service principal's own credentials carry no user
identity at all, and must never be mistaken for one.

**Why this file is shaped around latency.** Measured against the deployed app:

    OAuth token      5720 ms   (cold)
    initialize       3265 ms   (per session)
    tools/list       1532 ms

Paid naively that is ten and a half seconds before the model has produced a
single token, on an instance whose request timeout is 120s. So:

  * the ``Config`` is module-level, and the SDK refreshes the token on it — the
    5.7s is paid once per process rather than once per question;
  * a session is opened once per *turn* and every tool call in that turn runs
    inside it, so ``initialize`` is not paid per call;
  * tool schemas are cached, because they do not change between requests.

**Why asyncio.run.** The MCP SDK is async and this app is sync gthread. Bridging
at this boundary keeps the async surface to one file instead of making every
caller up to the Flask view async for no benefit — every one of them blocks on
I/O regardless.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import config
from exceptions import ExternalAPIError, ResearchCopilotError

logger = logging.getLogger(__name__)

# The header the MCP server reads to learn which application user a tool call is
# acting for. Named here and consumed there; the two must not drift.
USER_ID_HEADER = "X-RC-User-Id"

_config_lock = threading.Lock()
_databricks_config = None       # databricks.sdk.core.Config, token cached inside
_tool_cache: list[dict] | None = None


def is_configured() -> bool:
    return config.mcp_is_configured()


def _root_cause(exc: BaseException) -> BaseException:
    """
    Dig the real failure out of an anyio task group's ExceptionGroup.

    The MCP client runs its transport inside a task group, so a plain HTTP 503
    surfaces as `ExceptionGroup: unhandled errors in a TaskGroup` wrapping an
    `HTTPStatusError`. Reporting the group tells nobody anything.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _as_external(exc: BaseException) -> ExternalAPIError:
    """
    Turn a transport failure into something the error handler can answer with.

    Without this the raw exception escapes `/chat/ask` as a 500 error page.
    Discovered when the Databricks App stopped mid-development and a 503 came
    back as an unhandled ExceptionGroup rather than "the assistant is
    unavailable" — an outage in a dependency should degrade the feature, not
    break the page.
    """
    cause = _root_cause(exc)

    # A failure raised by a TOOL is not a failure of the transport, and saying
    # so throws away the only useful part. Observed live: OpenAlex rate-limited
    # a search, and because the error travelled up inside an anyio
    # ExceptionGroup rather than as itself, it was relabelled "Could not reach
    # the research service" — which was both wrong and the opposite of a lead.
    if isinstance(cause, ResearchCopilotError):
        logger.error("MCP tool failure: %s", cause)
        return cause if isinstance(cause, ExternalAPIError) else ExternalAPIError(str(cause))

    detail = f"{type(cause).__name__}: {cause}"
    logger.error("MCP transport failure: %s", detail)

    text = str(cause)
    if "503" in text or "502" in text or "504" in text:
        return ExternalAPIError("The research service is not running right now.")
    if "401" in text or "403" in text:
        return ExternalAPIError("The research service refused our credentials.")
    return ExternalAPIError("Could not reach the research service.")


def _auth_headers(user_id: str | None) -> dict[str, str]:
    """
    Service-principal credentials, plus the acting user when there is one.

    The SDK caches the access token on the Config object and refreshes it before
    expiry, so building headers is cheap after the first call. Holding the lock
    only around construction keeps concurrent requests from racing to build two.
    """
    global _databricks_config

    if not is_configured():
        raise ExternalAPIError("The research assistant is not configured.")

    if _databricks_config is None:
        with _config_lock:
            if _databricks_config is None:
                from databricks.sdk.core import Config

                _databricks_config = Config(
                    host=config.DATABRICKS_HOST,
                    client_id=config.DATABRICKS_CLIENT_ID,
                    client_secret=config.DATABRICKS_CLIENT_SECRET,
                )

    try:
        headers = dict(_databricks_config.authenticate())
    except Exception as exc:
        logger.error("Databricks OAuth failed: %s", exc)
        raise ExternalAPIError("Could not authenticate to the research service.") from exc

    # Only sent when we actually have a user. An absent header is what tells the
    # MCP server there is no application user, which it must treat as a refusal
    # for anything that writes rather than quietly substituting a default.
    if user_id:
        headers[USER_ID_HEADER] = str(user_id)
    return headers


@asynccontextmanager
async def _session(user_id: str | None) -> AsyncIterator[Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = _auth_headers(user_id)
    async with streamablehttp_client(
        config.MCP_SERVER_URL, headers=headers, timeout=config.MCP_TIMEOUT_SECONDS
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


class Turn:
    """
    One conversation turn's worth of MCP access.

    Exists so a turn opens a single session and reuses it across however many
    tool calls the model makes. Opening one per call would add ~3.3s of
    `initialize` to each, which on a six-call ceiling is twenty seconds of pure
    handshake.

    Async internals stay private; `run` is the only way in, and it is sync.
    """

    def __init__(self, user_id: str | None = None):
        self.user_id = user_id

    def run(self, plan):
        """
        Execute `plan(call_tool)` inside one MCP session.

        `plan` is a sync callable receiving a `call_tool(name, arguments)`
        function. Inverting it this way — rather than exposing an open session —
        means no caller can hold a session beyond the turn that owns it.
        """
        try:
            return asyncio.run(self._run(plan))
        except ResearchCopilotError:
            # Our own domain errors are meaningful already and travel unchanged.
            raise
        except BaseException as exc:      # noqa: BLE001 - ExceptionGroup is a BaseException
            raise _as_external(exc) from exc

    async def _run(self, plan):
        async with _session(self.user_id) as session:
            loop = asyncio.get_running_loop()

            def call_tool(name: str, arguments: dict) -> Any:
                # The plan runs in a worker thread (see below), so it hands work
                # back to the event loop rather than trying to await here.
                future = asyncio.run_coroutine_threadsafe(
                    _call(session, name, arguments), loop)
                return future.result(timeout=config.MCP_TIMEOUT_SECONDS * 2)

            # The plan is sync and may block on the LLM for seconds at a time.
            # Running it in a thread keeps the event loop free to service the
            # MCP session it is calling back into.
            return await asyncio.to_thread(plan, call_tool)


async def _call(session, name: str, arguments: dict) -> Any:
    result = await session.call_tool(name, arguments or {})

    if getattr(result, "isError", False):
        raise ExternalAPIError(f"Tool '{name}' failed: {_text(result)}")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        # FastMCP wraps a bare return value under "result"; unwrap so callers
        # see what the tool actually returned.
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured

    # No structured content: the payload arrived as text. Several tools answer
    # this way — get_paper_details among them — and handing the caller a JSON
    # string means nothing downstream can read a field out of it, so a paper
    # fetched that way never became a source.
    text = _text(result)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _text(result) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def list_tools(force: bool = False) -> list[dict]:
    """
    Tool schemas from the server, cached for the life of the process.

    Cached because they do not change between requests and fetching them costs
    ~1.5s. `force` exists for the health check, which wants to know the server
    is reachable *now* rather than that it was at boot.
    """
    global _tool_cache

    if _tool_cache is not None and not force:
        return _tool_cache

    async def _fetch():
        async with _session(None) as session:
            listed = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema or {"type": "object", "properties": {}},
                }
                for t in listed.tools
            ]

    try:
        _tool_cache = asyncio.run(_fetch())
    except ResearchCopilotError:
        raise
    except BaseException as exc:          # noqa: BLE001
        raise _as_external(exc) from exc
    return _tool_cache


def reset_cache() -> None:
    """Drop cached credentials and schemas — for tests and for config reloads."""
    global _databricks_config, _tool_cache
    _databricks_config = None
    _tool_cache = None
