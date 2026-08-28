-- =============================================================================
-- 03_create_trace_table.sql — MCP Server Telemetry / Observability
-- =============================================================================
--
-- This table records every tool call made through the MCP server, providing
-- full observability into agent behavior. The TraceMiddleware (a cross-cutting
-- concern in the middleware layer) writes to this table automatically — no
-- individual tool needs to know about tracing.
--
-- This follows the same pattern established in Day 3's weather MCP server,
-- where every tool invocation was logged with timing, status, and session
-- context for debugging and auditing.
--
-- USE CASES:
--   • Debug agent behavior: "Why did the agent call search_papers 5 times?"
--   • Performance monitoring: "Which tools are slowest?"
--   • Usage analytics: "Which tools do users trigger most?"
--   • Audit trail: "What actions did user X take in session Y?"
--
-- =============================================================================


CREATE TABLE IF NOT EXISTS mcp_traces (
    trace_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    duration_ms     DOUBLE PRECISION NOT NULL,
    method          TEXT,
    path            TEXT,
    status_code     INTEGER NOT NULL DEFAULT 200,
    user_email      TEXT,
    mcp_session_id  TEXT,
    tool_name       TEXT NOT NULL,
    session_result  JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index on session_id for "show me all tool calls in this conversation."
CREATE INDEX IF NOT EXISTS idx_mcp_traces_session ON mcp_traces (session_id);

-- Index on tool_name for "how often is search_papers called?"
CREATE INDEX IF NOT EXISTS idx_mcp_traces_tool ON mcp_traces (tool_name);

-- Index on user_email for "what did this user do?"
CREATE INDEX IF NOT EXISTS idx_mcp_traces_user ON mcp_traces (user_email);

-- Index on created_at for time-range queries (e.g., last 24 hours of activity).
CREATE INDEX IF NOT EXISTS idx_mcp_traces_created ON mcp_traces (created_at);
