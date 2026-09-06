-- =============================================================================
-- 05_paper_sections.sql — Phase 2: abstract + selected body sections (D7 · D)
-- =============================================================================
-- Run ONCE against an existing database, then re-run the ingestion notebook.
-- Idempotent: every statement is IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.
--
-- ADDS:      paper_sections table
--            papers.fulltext_status, papers.fulltext_checked_at
--            paper_embeddings.section_name
-- DESTROYS:  nothing. Existing abstract embeddings stay valid and keep working —
--            they simply carry section_name = NULL, which is what "abstract" means.
--
-- Why sections are stored, not just chunked in memory:
--   Extracting a section costs a PDF download and a parse. Re-chunking should
--   never cost that again. paper_sections is the durable intermediate, so
--   changing CHUNK_SIZE or the embedding model is a re-encode, not a re-crawl.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- paper_sections — extracted body text, one row per (paper, section).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_sections (
    section_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id     UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    -- 'conclusion' | 'discussion' | 'methods' — kept as free text, not an enum,
    -- because the wanted-section list is a notebook widget and must be tunable
    -- without a schema migration.
    section_name TEXT NOT NULL,
    section_text TEXT NOT NULL,
    char_count   INTEGER NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (paper_id, section_name)
);

CREATE INDEX IF NOT EXISTS idx_paper_sections_paper ON paper_sections (paper_id);

-- ---------------------------------------------------------------------------
-- papers — full-text acquisition outcome, per paper.
-- ---------------------------------------------------------------------------
-- Deliberately visible rather than inferred. "No sections for this paper" has
-- five distinct causes and they need different responses:
--   ok            → sections extracted
--   no_url        → no open_access_url to try (abstract-only, expected)
--   fetch_failed  → download failed: 404, paywall, timeout
--   parse_failed  → downloaded, but not parseable: HTML landing page, scanned PDF
--   no_sections   → parsed fine, no recognisable headings
-- Without this column all five look identical to a zero row count, which is the
-- silent-failure shape this project has already been bitten by once.
ALTER TABLE papers
    ADD COLUMN IF NOT EXISTS fulltext_status TEXT,
    ADD COLUMN IF NOT EXISTS fulltext_checked_at TIMESTAMPTZ;

ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_fulltext_status_check;
ALTER TABLE papers ADD CONSTRAINT papers_fulltext_status_check
    CHECK (fulltext_status IS NULL OR fulltext_status IN
           ('ok', 'no_url', 'fetch_failed', 'parse_failed', 'no_sections'));

-- Drives the "which papers still need a fetch attempt" query in the notebook.
CREATE INDEX IF NOT EXISTS idx_papers_fulltext_status ON papers (fulltext_status);

-- ---------------------------------------------------------------------------
-- paper_embeddings — which part of the paper a chunk came from.
-- ---------------------------------------------------------------------------
-- NULL = the abstract. Chosen over a literal 'abstract' string so that every
-- embedding row written before Phase 2 is already correct without a backfill.
ALTER TABLE paper_embeddings
    ADD COLUMN IF NOT EXISTS section_name TEXT;

-- The ingestion delta asks "does an embedding exist for this (paper, section)?".
-- Without this index that question is a sequential scan on every run.
CREATE INDEX IF NOT EXISTS idx_paper_embeddings_paper_section
    ON paper_embeddings (paper_id, section_name);

COMMIT;

-- Verify:
--   SELECT fulltext_status, count(*) FROM papers GROUP BY 1 ORDER BY 2 DESC;
--   SELECT section_name, count(*) FROM paper_sections GROUP BY 1;
--   SELECT coalesce(section_name, 'abstract') AS part, count(*)
--     FROM paper_embeddings GROUP BY 1 ORDER BY 2 DESC;
