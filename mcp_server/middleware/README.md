# mcp_server/middleware/ — Cross-Cutting Concerns

This directory encapsulates infrastructure aspects that cross-cut across multiple services and tools: identity resolution and observability telemetry.

## Modules

| File | Purpose |
|------|---------|
| `request_context.py` | Thread-safe user context propagation via `contextvars`, supporting header-injected Databricks identity and demo defaults. |
| `trace_middleware.py` | Tool telemetry decorator (`@trace_tool`) logging start/finish timestamps, duration (ms), inputs/outputs, and exceptions to `mcp_traces`. |

---

## Key Design & Implementation Decisions

### 1. Thread-Safe Context Propagation (`contextvars`)
* In asynchronous or concurrent execution environments, storing user context in global variables creates race conditions.
* We utilize Python's built-in `contextvars.ContextVar` to guarantee clean isolation between concurrent agent calls while avoiding having to pass `user_id` explicitly through every layer of the tool interface.

### 2. Non-Intrusive Observability Decorator
* Tool functions in `research_mcp_server.py` focus purely on delegating to services.
* The `@trace_tool(tool_name)` decorator transparently intercepts calls, times execution, captures parameters, and handles telemetry persistence in a `finally` block so failures in logging never disrupt tool results.
