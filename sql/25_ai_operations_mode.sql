-- =============================================================================
-- 25_ai_operations_mode.sql — telling Research and Wick apart
-- =============================================================================
-- `metric` answers "what allowance did this spend", and both the research agent
-- and Wick spend the same one: `agent_query`. That is correct for quota and
-- useless for a usage breakdown, where they are the two things somebody most
-- wants separated — one reads papers, the other edits their collections, and
-- they cost very different amounts.
--
-- A column rather than new metric values. `metric` is the quota key: splitting
-- it into `agent_query` and `wick_query` would silently give every account two
-- separate agent allowances, which is a pricing change disguised as a reporting
-- one.
--
-- NULL for everything written before this migration. Those rows genuinely do
-- not know which mode they were — the information was never captured — so the
-- usage page reports them under Research, which is the default mode and what
-- the large majority of them will have been. Rows from here on say so properly.
-- =============================================================================

ALTER TABLE ai_operations
    ADD COLUMN IF NOT EXISTS mode TEXT;

-- The usage breakdown filters on (user, mode) inside a time window, so mode
-- joins the per-user index rather than getting one of its own.
DROP INDEX IF EXISTS idx_ai_operations_user;
CREATE INDEX IF NOT EXISTS idx_ai_operations_user
    ON ai_operations (user_id, occurred_at DESC)
    INCLUDE (metric, mode)
    WHERE user_id IS NOT NULL;

-- =============================================================================
-- Verification
-- =============================================================================
-- Ask Wick something and ask the research agent something, then:
--
-- SELECT metric, mode, count(*)
--   FROM ai_operations
--  WHERE user_id = '<your-user-id>'
--  GROUP BY metric, mode
--  ORDER BY metric, mode;
--
-- Expect agent_query to appear twice, once with 'research' and once with
-- 'wick'. Rows predating this migration carry NULL, which is expected and not
-- backfilled — a guessed mode would be worse than an honest absence.
