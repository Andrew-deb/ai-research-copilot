# mcp_server/ — MCP Server (Databricks App #1)

FastMCP server exposing 13 tools to the AI agent. Follows a strict layered architecture — see the project implementation plan for the full layer contract.

## Directory Structure

```
mcp_server/
├── config.py                   # Single source of truth for all configuration
├── exceptions.py               # Domain exception classes
├── research_mcp_server.py      # Tool registration (interface layer)
├── brokers/                    # External API clients (HTTP only)
│   ├── openalex_broker.py      # OpenAlex paper discovery
│   ├── semantic_scholar_broker.py  # S2 enrichment (TLDRs, influence, recommendations)
│   └── wikipedia_broker.py     # Wikipedia topic summaries
├── services/                   # Business logic layer
│   ├── discovery_service.py
│   ├── collection_service.py
│   ├── planning_service.py
│   └── progress_service.py
├── repositories/               # Data access layer (all SQL lives here)
│   └── lakebase.py
├── middleware/                  # Cross-cutting concerns
│   ├── request_context.py      # User identity capture
│   └── trace_middleware.py     # Per-call telemetry → mcp_traces table
├── app.yaml                    # Databricks App deployment config
└── requirements.txt
```

## Setup

```bash
# From the repo root (ai-research-copilot/), with .env holding DATABASE_URL + API keys
pip install -r mcp_server/requirements.txt   # needs mcp<2 (FastMCP 1.x) — pinned there

# HTTP transport (what Databricks Apps runs) — MCP endpoint at POST /mcp, health at GET /healthz
MCP_TRANSPORT=streamable-http PORT=8080 python mcp_server/research_mcp_server.py

# stdio transport (Claude Desktop, MCP Inspector)
MCP_TRANSPORT=stdio python mcp_server/research_mcp_server.py
```

**Imports here are flat** (`from config import …`, not `from mcp_server.config import …`).
A Databricks App deploy flattens this folder's *contents* to `/app/python/source_code/`,
so no `mcp_server` package exists at runtime. The entrypoint puts its own directory on
`sys.path`, so the same code runs from the repo and from the flattened app root
(`python -m research_mcp_server`). `tests/test_mcp_server.py` enforces both.
`MCP_TRANSPORT` ∈ `streamable-http` (default) · `sse` · `stdio`. For the HTTP transports
the server binds `MCP_HOST` (default `0.0.0.0`) : `DATABRICKS_APP_PORT` / `PORT` / `8080`.

Quick check that all 13 tools are live:

```bash
curl -s -X POST localhost:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Key Design Decisions

**`config.py` as single config source** — No module calls `os.getenv()` directly. All constants, API URLs, and secret-scope lookups are centralised in `config.py`. Secret loading tries the Databricks SDK first, then falls back to the `.env` file, so the same code runs locally and on Databricks without changes.

**Broker layer SRP** — Each broker owns exactly one external API. The service layer orchestrates brokers but never calls `requests` directly. This means switching from OpenAlex to Lens.org requires changing one file only.

**Tool = one line** — Every `@mcp.tool` function in `research_mcp_server.py` calls exactly one service function and returns its result. No business logic, no SQL, no HTTP in the tool layer.

**Exceptions, not error dicts** — Services raise typed domain exceptions (`PaperNotFoundError`, `ValidationError`, etc.). The MCP server's error handler converts these to structured error responses. Individual tools never contain `try/except`.

**Middleware for cross-cutting concerns** — Request tracing and user-identity resolution are handled by middleware, not by individual tools. Adding a new cross-cutting concern (e.g., audit logging) requires one middleware change, not changes to 13 tools.

## Secret Scope Names

| Scope | Key | Value |
|-------|-----|-------|
| `database` | `lakebase-url` | Lakebase Postgres connection URL |
| `semantic-scholar` | `api-key` | Semantic Scholar API key |
| `openrouter` | `api-key` | OpenRouter API key |
| `openalex` | `email` | OpenAlex polite pool email |
