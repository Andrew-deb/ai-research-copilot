-- =============================================================================
-- 24_ai_operations_user_index.sql — reading the telemetry back per account
-- =============================================================================
-- `ai_operations` has been written since the telemetry work and read by nothing.
-- Its two indexes serve the operator's questions — "what did the system do
-- yesterday", "how is rag_query behaving" — and both lead with time or metric.
--
-- The usage page asks a different question: everything ONE account did over a
-- window. Neither existing index helps with that, so the planner would scan the
-- whole table and filter.
--
-- That matters more here than the row count suggests. This database is reached
-- over a public endpoint with a round-trip floor of several hundred
-- milliseconds before it does any work, and a sequential scan on top of that is
-- how a palette query once took 38 seconds. The index is cheap; discovering it
-- was missing in production is not.
--
-- `occurred_at DESC` because every query over this ends with a window: the last
-- 7 days, the last 30. Matching the sort order lets the range be read straight
-- out of the index instead of fetched and re-sorted.
--
-- Partial on `user_id IS NOT NULL`: deleted accounts leave their rows behind
-- with a NULL user (ON DELETE SET NULL, so the operational record survives
-- without naming anybody). Those rows can never match a per-user lookup, so
-- there is no reason to carry them in this index.
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_ai_operations_user
    ON ai_operations (user_id, occurred_at DESC)
    WHERE user_id IS NOT NULL;

-- =============================================================================
-- Verification
-- =============================================================================
-- Expect an Index Scan rather than a Seq Scan, and single-digit milliseconds of
-- planning plus execution:
--
-- EXPLAIN ANALYZE
-- SELECT metric, count(*) FROM ai_operations
--  WHERE user_id = '<your-user-id>'
--    AND occurred_at >= now() - interval '30 days'
--  GROUP BY metric;
--
-- And confirm the partial index is being used rather than silently skipped:
--
-- SELECT indexrelname, idx_scan FROM pg_stat_user_indexes
--  WHERE relname = 'ai_operations';
