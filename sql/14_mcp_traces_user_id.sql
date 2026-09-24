-- =============================================================================
-- 14_mcp_traces_user_id.sql — who a tool call was actually for (Phase 3.5)
-- =============================================================================
-- mcp_traces recorded `user_email` and nothing else about identity. The
-- dashboard authenticates the person and sends their **id** in X-RC-User-Id; it
-- never sends an email, because the id is what authorises and an address is a
-- label. So for every signed-in caller the one identity column in this table
-- was filled by a fallback rather than by the caller.
--
-- That fallback was not merely uninformative. `get_current_user_email()`
-- provisioned the demo account when no email was bound, and provisioning writes
-- the demo id into the acting-user contextvar — one line before the tool ran.
-- Every traced call executed as demo@research-copilot.dev, and this table dutifully
-- recorded it. The trace was not reporting the failure; it was causing it.
--
-- Adding the id here closes that loop: the column records what actually bound,
-- from an accessor that cannot change it, so the table can be used to verify
-- identity propagation rather than to disguise its absence.
--
-- Nullable, and no foreign key. A trace is an observation, not a relationship:
-- a call made by the service principal alone has no user and must still be
-- recorded, and telemetry must never be able to fail a tool call by referencing
-- a row that has since been deleted.
-- =============================================================================

ALTER TABLE mcp_traces
    ADD COLUMN IF NOT EXISTS user_id UUID;

-- Newest first, because every question asked of this column is "who has been
-- calling recently" rather than "everything this user ever did".
CREATE INDEX IF NOT EXISTS idx_mcp_traces_user_id
    ON mcp_traces (user_id, created_at DESC);

-- =============================================================================
-- Verification — run after redeploying the MCP server
-- =============================================================================
-- Identity propagation is working when a signed-in visitor's tool calls carry
-- their own id. A run of nulls here means the header is not arriving at all,
-- which is a different fault (the proxy in front of the app) from the one this
-- file is part of fixing.
--
-- SELECT t.tool_name,
--        t.user_id,
--        u.email AS acting_user,
--        t.user_email AS bound_email,
--        t.created_at
--   FROM mcp_traces t
--   LEFT JOIN users u ON u.user_id = t.user_id
--  WHERE t.created_at > now() - interval '1 hour'
--  ORDER BY t.created_at DESC
--  LIMIT 20;
--
-- And the summary that answers it in one row:
--
-- SELECT count(*) FILTER (WHERE user_id IS NOT NULL) AS attributed,
--        count(*) FILTER (WHERE user_id IS NULL)     AS unattributed
--   FROM mcp_traces
--  WHERE created_at > now() - interval '1 hour';
