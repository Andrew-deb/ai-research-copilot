# Research and Wick MCP servers

Two independently deployed FastMCP servers share infrastructure and business operations. Research exposes its existing 13 tools; Wick exposes 12 workspace tools. Each server has its own registration and startup module.

| Source | Responsibility |
| --- | --- |
| `research/server.py` | Research tool catalog and startup |
| `research/services/` | Research discovery orchestration |
| `research/brokers/` | OpenAlex, Semantic Scholar and Wikipedia clients |
| `research/config.py` | Research provider settings |
| `assistant/server.py` | Wick workspace tool catalog and startup |
| `shared_resource/` | Common services, adapters, repositories, middleware, configuration and errors |
| `deploy/` | Artifact builder, App specifications and deployment runbook |

The obsolete top-level MVP entrypoint and module folders have been removed. Runtime imports are namespaced and common package imports are relative. Research and Wick launch directly; neither loads a legacy entrypoint or imports the other server.

## Local startup

From the repository root, install dependencies, then launch from the MCP source root:

```bash
pip install -r mcp_server/shared_resource/requirements.txt
cd mcp_server
MCP_TRANSPORT=streamable-http PORT=8080 python -m research.server
# In another terminal, from the same directory:
PORT=8081 python -m assistant.server
```

Research retains its streamable HTTP, SSE and stdio transport options. Wick uses streamable HTTP. HTTP startup uses `MCP_HOST` (default `0.0.0.0`) and `DATABRICKS_APP_PORT` / `PORT` / `8080`; `/mcp` serves MCP and `/healthz` serves health checks.

Shared database configuration loads the `database/lakebase-url` secret with `DATABASE_URL` fallback. Research retains the `semantic-scholar/api-key` and `openrouter/api-key` secret scopes and existing environment fallbacks, including `OPENALEX_EMAIL`. Wick does not load Research’s provider configuration.

## Runtime boundaries

Business services receive repositories explicitly. Thin shared adapters bind them to the MCP database adapter. Tool interfaces resolve the acting user and delegate to these operations; identity and tracing remain shared middleware. Provider HTTP calls remain in Research’s broker layer. Services raise typed domain errors.

The Databricks proxy authenticates the trusted Render caller before accepting its acting-user header. Wick requires identity middleware at startup. Private reads and writes require attributable identity; public access remains bounded. Moving middleware does not change authorization or app grants.

Render selects Research through `RESEARCH_MCP_SERVER_URL` (with legacy `MCP_SERVER_URL` fallback), and Wick through `WICK_MCP_SERVER_URL`, without Research fallback. Tool schema caches are keyed by mode and endpoint, with strict catalog validation. `agent/wick_system_prompt.md` is selected and bundled by Render orchestration; sharing an MCP package does not share prompts or tool access.

Structured Allow once / Always allow permissions and further write capabilities remain separate planned work. This cleanup adds no tools or database migrations.

## Deployment

See [the deployment runbook](deploy/DEPLOYMENT.md). Build from the repository root:

```bash
python mcp_server/deploy/build.py --output dist/release-candidate
```

The GitHub workflow validates and publishes independent `deploy/research` and `deploy/assistant` branches. Their artifact roots and startup commands remain unchanged. After merging this source cleanup and waiting for publication, manually redeploy both Databricks Apps; Render’s Blueprint rebuilds its artifact automatically.
