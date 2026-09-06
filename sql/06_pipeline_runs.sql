-- =============================================================================
-- 06_pipeline_runs.sql — Phase 2.5: make a scheduled pipeline worth scheduling
-- =============================================================================
-- Run ONCE against an existing database. Idempotent and additive; destroys nothing.
--
-- ADDS: pipeline_runs      — one row per run, so "did last night add anything?"
--                            is a query rather than a scroll through driver logs
--       topic_watermarks   — per-topic high-water mark for incremental discovery
--       papers.fulltext_attempts — bounds retries of transient fetch failures
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- pipeline_runs — the run ledger.
-- ---------------------------------------------------------------------------
-- A job cluster's stdout dies with the cluster. Without this table, tuning the
-- schedule is guesswork: there is no way to answer "was that run worth it?"
-- after the fact. Counts are deliberately granular, because a run that harvests
-- 40 papers and embeds 0 chunks is a very different problem from one that
-- harvests 0.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'running'
                           CHECK (status IN ('running', 'ok', 'failed', 'skipped_locked')),
    trigger            TEXT,                       -- 'scheduled' | 'manual' | NULL

    papers_harvested   INTEGER NOT NULL DEFAULT 0, -- returned by OpenAlex
    papers_inserted    INTEGER NOT NULL DEFAULT 0, -- genuinely new rows
    papers_enriched    INTEGER NOT NULL DEFAULT 0, -- S2 calls that returned data
    s2_requests        INTEGER NOT NULL DEFAULT 0, -- batch requests, not papers
    pdfs_fetched       INTEGER NOT NULL DEFAULT 0,
    sections_extracted INTEGER NOT NULL DEFAULT 0,
    chunks_embedded    INTEGER NOT NULL DEFAULT 0,
    notes_embedded     INTEGER NOT NULL DEFAULT 0,

    duration_seconds   DOUBLE PRECISION,
    error              TEXT,
    notes              JSONB                       -- config snapshot: model, topics, caps
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs (started_at DESC);

-- ---------------------------------------------------------------------------
-- topic_watermarks — per-topic incremental discovery cursor.
-- ---------------------------------------------------------------------------
-- Per topic, not global, on purpose: adding a sixth seed topic later must seed
-- that topic from the canon rather than inherit the other five's watermark and
-- silently skip everything published before today.
--
-- last_seeded_at NULL means "never run relevance seeding for this topic yet",
-- which is what selects the cold-start branch in the notebook.
CREATE TABLE IF NOT EXISTS topic_watermarks (
    topic             TEXT PRIMARY KEY,
    last_seeded_at    TIMESTAMPTZ,                 -- when the relevance seed ran
    watermark_date    DATE,                        -- newest publication_date ingested
    last_run_at       TIMESTAMPTZ,
    papers_ingested   INTEGER NOT NULL DEFAULT 0   -- cumulative, for this topic
);

-- ---------------------------------------------------------------------------
-- papers.fulltext_attempts — retry budget for transient fetch failures.
-- ---------------------------------------------------------------------------
-- `fulltext_status IS NULL` meant a paper was attempted exactly once, ever: a
-- host that was down for one afternoon was written off permanently. That is
-- precisely the failure a *scheduled* job should recover from on its own.
--
-- The counter is what stops the opposite failure — a permanently dead URL being
-- retried on every run, forever.
ALTER TABLE papers
    ADD COLUMN IF NOT EXISTS fulltext_attempts INTEGER NOT NULL DEFAULT 0;

COMMIT;

-- Verify:
--   SELECT started_at, status, papers_inserted, chunks_embedded, duration_seconds
--     FROM pipeline_runs ORDER BY started_at DESC LIMIT 5;
--   SELECT * FROM topic_watermarks ORDER BY topic;
--   SELECT fulltext_status, count(*), max(fulltext_attempts)
--     FROM papers GROUP BY 1 ORDER BY 2 DESC;
