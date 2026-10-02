# Shared MCP resources

Common code is grouped by architectural layer. Research and Wick register their own tools and use the same runtime infrastructure without importing each other’s server.

| Module | Responsibility |
| --- | --- |
| `services/` | Reusable business operations, with explicit repository dependencies |
| `repositories/` | Parameterized persistence queries and the MCP Lakebase connection adapter |
| `adapters/` | Thin runtime bindings that supply Lakebase to business services |
| `middleware/` | Acting-user context, identity propagation and per-call tracing |
| `config.py` | Shared database secrets and embedding contract |
| `exceptions.py` | Common domain errors |
| `requirements.txt` | Existing MCP dependency pins used by both deployment artifacts |

Research-only discovery orchestration, provider clients and provider settings live under `research/`. Tool registration and startup stay in each server. Shared module presence never grants a tool: only explicit registrations define each catalog. System prompts belong to the Render orchestration under `agent/`.

Internal imports are relative or explicitly namespaced, so they cannot resolve to the dashboard’s flat service, repository or middleware packages. Render receives only pure shared services, exceptions and repository contracts; it retains its own database connection and Flask middleware. MCP runtime configuration, adapters and middleware are excluded from its build.

Collection mutations require an attributable owner and reject curated examples. Private workspace reads require identity; anonymous discovery is limited to supported public pages, global papers and explicitly curated collections. Repository queries remain owner-scoped and parameterized. The restructuring changes no tool signatures, response fields, database schema, environment variables or authentication requirements.

Workspace discovery covers paper, collection, note, goal and a finite page allowlist. Lists are capped at 50 items with numeric offset cursors up to 10,000. Note content is chunked at 4,000 characters within the 10,000-character note contract; other large fields disclose truncation. Offset cursors are reauthorized on each request and may shift when records change. The shared layer never visits arbitrary URLs or executes page actions.

Rich note creation supports standalone or paper-linked notes, titles and tags. Research’s legacy paper-note signature remains unchanged. These contracts use existing migrations; this cleanup introduces none. Shared services are authoritative source code; generated deployment branches contain build copies and must not be edited directly.
