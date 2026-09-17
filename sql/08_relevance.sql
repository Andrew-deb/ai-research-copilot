-- =============================================================================
-- 08_relevance.sql — Phase 2.8: corpus relevance scoring
-- =============================================================================
-- Run ONCE against an existing database. Additive; deletes nothing.
--
-- Why this exists. The harvest sends `search=<topic>` to OpenAlex, which matches
-- those words across all of science. Measured 2026-09-16, "vector database
-- indexing" returned MegaBLAST, the Ribosomal Database Project and BLAST+ —
-- bioinformatics papers that match the words and not the subject. A large share
-- of the corpus is off-topic, which also explains the poor full-text coverage:
-- biomedical publishers paywall and block robots.
--
-- NOTHING IS DELETED HERE, AND NOTHING WILL BE DELETED BY THE PIPELINE.
-- The gate is new and its threshold is uncalibrated, so historical papers are
-- scored and *flagged* only. Deciding what to do with the flagged rows is a
-- separate, later, deliberate step.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- papers — the relevance verdict, and enough context to interpret it.
-- ---------------------------------------------------------------------------
-- A bare score is not interpretable. 0.31 means nothing without knowing *what it
-- was scored against* and *what bar was in force at the time* — and both change.
-- Topics are edited; the threshold is being calibrated right now. Storing all
-- four means a row scored today can still be read six months from now.
--
-- The embedding model is deliberately NOT stored here: `embedding_contract.json`
-- is validated before any encoding happens (Phase 2.7), so every score in this
-- table provably comes from the same model. If that ever stops being true, this
-- column list needs a fifth entry.
ALTER TABLE papers
    ADD COLUMN IF NOT EXISTS relevance_score     DOUBLE PRECISION,
    -- The topic text the score was computed against. For papers ingested before
    -- Phase 2.8 the originating topic was never recorded, so the backfill scores
    -- against every configured topic and stores the best-matching one.
    ADD COLUMN IF NOT EXISTS relevance_topic     TEXT,
    -- The threshold in force when the status was assigned. Without it, changing
    -- the threshold silently invalidates every stored status.
    ADD COLUMN IF NOT EXISTS relevance_threshold DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS relevance_scored_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS relevance_status    TEXT NOT NULL DEFAULT 'unscored';

ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_relevance_status_check;
ALTER TABLE papers ADD CONSTRAINT papers_relevance_status_check
    CHECK (relevance_status IN ('accepted', 'flagged', 'unscored'));

--   unscored  never scored — the starting state for every existing row
--   accepted  scored at or above the threshold in force
--   flagged   scored below it. Still fully searchable. Flagged is a note to a
--             human, not an exclusion: nothing in the application filters on it.

CREATE INDEX IF NOT EXISTS idx_papers_relevance_status ON papers (relevance_status);

-- ---------------------------------------------------------------------------
-- pipeline_runs — did the gate do anything?
-- ---------------------------------------------------------------------------
-- Separating found from rejected is what makes the gate auditable. A run that
-- finds 60 and rejects 55 is a topic problem; one that finds 60 and rejects 0 is
-- a gate that is not running.
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS candidates_found    INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS candidates_rejected INTEGER NOT NULL DEFAULT 0;

COMMIT;

-- Verify:
--   SELECT relevance_status, count(*) FROM papers GROUP BY 1;
--   -- expect everything 'unscored' until the pipeline runs once.
--
-- After a run, inspect the calibration before trusting the threshold:
--   SELECT round(relevance_score::numeric, 3) AS score, relevance_topic,
--          left(title, 70) AS title
--     FROM papers WHERE relevance_status = 'flagged'
--    ORDER BY relevance_score DESC LIMIT 25;
--   -- the TOP of this list is what matters: those are the papers closest to the
--   -- bar. If they look on-topic, the threshold is too high.
--
--   SELECT round(relevance_score::numeric, 3) AS score, relevance_topic,
--          left(title, 70) AS title
--     FROM papers WHERE relevance_status = 'accepted'
--    ORDER BY relevance_score ASC LIMIT 25;
--   -- and the BOTTOM of this one: if they look off-topic, it is too low.
