-- =============================================================================
-- 03_create_trace_table.sql — MCP server telemetry
-- =============================================================================
-- Written by TraceMiddleware automatically on every tool call.
-- Tracks timing, session context, tool name, and errors for observability.
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

CREATE INDEX IF NOT EXISTS idx_mcp_traces_session ON mcp_traces (session_id);
CREATE INDEX IF NOT EXISTS idx_mcp_traces_tool    ON mcp_traces (tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_traces_user    ON mcp_traces (user_email);
CREATE INDEX IF NOT EXISTS idx_mcp_traces_created ON mcp_traces (created_at);
