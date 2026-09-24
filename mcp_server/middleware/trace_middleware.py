"""
mcp_server/middleware/trace_middleware.py — Telemetry & Tool Observability.

Decorator that wraps FastMCP tool executions, logs timing, captures input/output
payloads, and persists trace records to the mcp_traces table in Lakebase.
"""

import functools
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from middleware.request_context import get_bound_user_id, get_current_user_email
from repositories import lakebase

logger = logging.getLogger("mcp_trace")


def trace_tool(tool_name: str) -> Callable:
    """
    Decorator that records execution telemetry for an MCP tool into mcp_traces.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            request_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            started_at = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            # Both accessors are side-effect free. The previous email getter
            # provisioned the demo user as a side effect of being called, which
            # rebound the acting id a line before the tool ran.
            user_email = get_current_user_email()
            user_id = get_bound_user_id()
            
            error_message = None
            result = None
            status_code = 200

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error_message = str(e)
                status_code = 500
                logger.error(f"Tool execution failed [{tool_name}]: {e}")
                raise
            finally:
                t1 = time.perf_counter()
                finished_at = datetime.now(timezone.utc)
                duration_ms = (t1 - t0) * 1000.0

                trace_record = {
                    "request_id": request_id,
                    "session_id": session_id,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_ms": duration_ms,
                    "method": "MCP_TOOL",
                    "path": f"/tool/{tool_name}",
                    "status_code": status_code,
                    "user_email": user_email,
                    # The authoritative identity. The dashboard sends an id and
                    # no email, so user_email is null for signed-in callers and
                    # this is the column that answers "who was this for".
                    "user_id": user_id,
                    "mcp_session_id": session_id,
                    "tool_name": tool_name,
                    "session_result": {
                        "arguments": kwargs or args,
                        "success": error_message is None
                    },
                    "error_message": error_message
                }

                try:
                    lakebase.write_trace(trace_record)
                except Exception as write_err:
                    logger.warning(f"Failed to record trace for {tool_name}: {write_err}")

        return wrapper
    return decorator
