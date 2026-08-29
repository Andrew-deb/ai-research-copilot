# dashboard/middleware/ — Cross-Cutting Concerns

Infrastructure aspects that cut across every route: end-user identity resolution and domain-exception handling. Route functions stay thin because these concerns are handled once, centrally.

## Modules

| File | Purpose |
|------|---------|
| `auth.py` | Resolves the Databricks-injected `X-Forwarded-Email` header (or the seeded demo user in local dev) to a `users` row once per request, exposed on Flask's `g.user` / `g.user_id`. `REQUIRE_FORWARDED_AUTH=true` switches "no header" from *demo fallback* to *401*. |
| `error_handler.py` | Maps typed domain exceptions (`ValidationError`, `PaperNotFoundError`, `ExternalAPIError`, …) to HTTP responses — JSON for `fetch`/XHR callers, a flashed message + redirect for form posts and page loads. |

---

## Key Design & Implementation Decisions

### 1. Identity via Proxy Header, Not a Login Form
* Databricks Apps run behind an authenticating proxy that injects `X-Forwarded-Email` (plus `X-Forwarded-Preferred-Username`, `X-Forwarded-User`, and optionally `X-Forwarded-Access-Token`) on every request — the app never sees a password and needs no session store.
* `auth.py` upserts the user on first sight (`get_or_create_user`), so a brand-new Databricks user gets a row automatically.
* Local development has no proxy, so with `REQUIRE_FORWARDED_AUTH=false` (the default) the middleware falls back to `demo@research-copilot.dev` — the *same* identity `mcp_server/middleware/request_context.py` defaults to, so the agent and the dashboard read and write one account.
* In the Databricks App environment, set `REQUIRE_FORWARDED_AUTH=true`. A request that reaches the app *without* the header has bypassed the proxy and is refused with `401` rather than silently served as the demo user.
* `/healthz` and `/favicon.ico` are exempt from the hook — they must answer during deploy health checks before any database or identity resolution.

### 2. `contextvars` in the MCP Server, `flask.g` in the Dashboard
* The MCP server uses `contextvars` because FastMCP tool calls can run concurrently in one process with no request object.
* Flask already gives us a request-scoped namespace (`g`) that is reset per request and never leaks between them, so the dashboard uses that instead of re-inventing it.

### 3. Exceptions, Not Error Dicts (Same Contract as the MCP Server)
* Services raise; routes don't catch. A single `@app.errorhandler(ResearchCopilotError)` classifies the exception, picks a status code, and chooses a representation based on how the caller asked (`Accept` header / `/api/` prefix / `is_json`).
* Adding a new failure mode means adding one exception class and one row to `_STATUS_MAP` — no changes to any route.
