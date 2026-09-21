-- =============================================================================
-- 11_ai_operations.sql — per-operation AI telemetry (Phase 3.3)
-- =============================================================================
-- One row per AI operation: a semantic search, a RAG answer, an agent run.
--
-- This belongs to the quota-calibration design (authentication_and_demo_design
-- §6a) and should have shipped with 10_capabilities_and_quotas.sql, which
-- created usage_counters only. It arrives as its own file rather than as an
-- edit to 10 because 10 is already applied.
--
-- Separate from usage_counters, which answers a different question:
--
--   usage_counters   how many has this visitor spent today   -> enforcement
--   ai_operations    what did each one cost and how long     -> calibration
--
-- A counter cannot be calibrated from: it records that something happened, not
-- what it cost. Phase 3.6 sets every quota number from this table, and P95 is
-- what it reads - a ceiling chosen from the mean is breached by the first
-- genuinely hard question anyone asks.
--
-- A table rather than logs: Render's free-tier logs are ephemeral and lost on
-- restart, and calibration needs aggregation across days.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ai_operations (
    op_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric             TEXT NOT NULL,        -- semantic_search | rag_query | agent_query
    tier               TEXT NOT NULL,        -- anonymous | authenticated
    user_id            UUID REFERENCES users(user_id) ON DELETE SET NULL,
    provider           TEXT,                 -- openrouter | huggingface | ...
    model              TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    estimated_cost_usd NUMERIC(10, 6),       -- provider-reported where available
    latency_ms         INTEGER,
    llm_turns          INTEGER NOT NULL DEFAULT 0,
    tool_calls         INTEGER NOT NULL DEFAULT 0,
    embedding_calls    INTEGER NOT NULL DEFAULT 0,
    ok                 BOOLEAN NOT NULL DEFAULT true,
    error              TEXT
);

-- ON DELETE SET NULL, not CASCADE: deleting an account must not erase the cost
-- history that capacity planning depends on. The row survives without naming
-- anyone, which is the correct trade between privacy and operations.

CREATE INDEX IF NOT EXISTS idx_ai_operations_occurred ON ai_operations (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_operations_metric   ON ai_operations (metric, occurred_at DESC);

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT metric, tier, count(*),
--        round(avg(latency_ms))                                    AS mean_ms,
--        percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)  AS p95_ms,
--        round(avg(llm_turns), 2)                                  AS mean_turns,
--        round(avg(tool_calls), 2)                                 AS mean_tools
--   FROM ai_operations
--  WHERE occurred_at > now() - interval '7 days'
--  GROUP BY metric, tier
--  ORDER BY metric, tier;
