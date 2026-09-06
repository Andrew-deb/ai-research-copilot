-- =============================================================================
-- 01_create_tables.sql — Core relational schema
-- =============================================================================
-- Run via setup_db.py. All tables use UUID PKs, TIMESTAMPTZ for all timestamps,
-- and ON DELETE CASCADE so child rows are automatically cleaned up with parents.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- users — Anchor table for all user-scoped data.
-- Email is the natural key (injected by Databricks Apps via X-Forwarded-Email).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);


-- ---------------------------------------------------------------------------
-- learning_goals — What the user wants to learn.
-- Status: active → completed | archived
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS learning_goals (
    goal_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'completed', 'archived')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learning_goals_user ON learning_goals (user_id);


-- ---------------------------------------------------------------------------
-- papers — Unified store for papers from OpenAlex, Semantic Scholar, or manual entry.
-- openalex_id and semantic_scholar_id are nullable (paper may exist in one source only).
-- tldr and influence_score are null until the S2 enrichment step runs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS papers (
    paper_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    openalex_id          TEXT UNIQUE,
    semantic_scholar_id  TEXT UNIQUE,
    doi                  TEXT,
    title                TEXT NOT NULL,
    abstract             TEXT,
    publication_year     INTEGER,
    venue                TEXT,
    citation_count       INTEGER DEFAULT 0,
    tldr                 TEXT,
    influence_score      DOUBLE PRECISION,
    source_api           TEXT NOT NULL DEFAULT 'openalex'
                             CHECK (source_api IN ('openalex', 'semantic_scholar', 'manual')),
    open_access_url      TEXT,
    payload              JSONB,
    -- Outcome of the Phase 2 full-text fetch. NULL = not attempted yet.
    -- Explicit so that "no sections" distinguishes no_url / fetch_failed /
    -- parse_failed / no_sections instead of all looking like a zero count.
    fulltext_status      TEXT CHECK (fulltext_status IS NULL OR fulltext_status IN
                             ('ok', 'no_url', 'fetch_failed', 'parse_failed', 'no_sections')),
    fulltext_checked_at  TIMESTAMPTZ,
    -- Retry budget for transient fetch failures. Without it, a host that was down
    -- for one afternoon is written off permanently; with no ceiling, a dead URL is
    -- retried on every scheduled run forever.
    fulltext_attempts    INTEGER NOT NULL DEFAULT 0,
    synced_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial index — only DOIs that exist (many preprints/theses have no DOI)
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers (source_api);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers (publication_year);
CREATE INDEX IF NOT EXISTS idx_papers_fulltext_status ON papers (fulltext_status);


-- ---------------------------------------------------------------------------
-- paper_sections — body text extracted from open-access PDFs (Phase 2).
-- One row per (paper, section). Stored rather than chunked on the fly so that
-- re-chunking or changing the embedding model never re-downloads a PDF.
-- section_name is free text, not an enum: the wanted-section list is a notebook
-- widget and must be tunable without a schema migration.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_sections (
    section_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id     UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    section_name TEXT NOT NULL,
    section_text TEXT NOT NULL,
    char_count   INTEGER NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (paper_id, section_name)
);

CREATE INDEX IF NOT EXISTS idx_paper_sections_paper ON paper_sections (paper_id);


-- ---------------------------------------------------------------------------
-- pipeline_runs — one row per ingestion run (Phase 2.5).
-- A job cluster's stdout dies with the cluster, so without this table there is no
-- way to answer "did last night's run add anything?" after the fact.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'running'
                           CHECK (status IN ('running', 'ok', 'failed', 'skipped_locked')),
    trigger            TEXT,
    papers_harvested   INTEGER NOT NULL DEFAULT 0,
    papers_inserted    INTEGER NOT NULL DEFAULT 0,
    papers_enriched    INTEGER NOT NULL DEFAULT 0,
    s2_requests        INTEGER NOT NULL DEFAULT 0,
    pdfs_fetched       INTEGER NOT NULL DEFAULT 0,
    sections_extracted INTEGER NOT NULL DEFAULT 0,
    chunks_embedded    INTEGER NOT NULL DEFAULT 0,
    notes_embedded     INTEGER NOT NULL DEFAULT 0,
    duration_seconds   DOUBLE PRECISION,
    error              TEXT,
    notes              JSONB
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs (started_at DESC);


-- ---------------------------------------------------------------------------
-- topic_watermarks — per-topic incremental discovery cursor (Phase 2.5).
-- Per topic rather than global: a seed topic added later must start from the
-- canon, not inherit the others' watermark and skip everything already published.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS topic_watermarks (
    topic             TEXT PRIMARY KEY,
    last_seeded_at    TIMESTAMPTZ,
    watermark_date    DATE,
    last_run_at       TIMESTAMPTZ,
    papers_ingested   INTEGER NOT NULL DEFAULT 0
);


-- ---------------------------------------------------------------------------
-- authors — Researcher profiles from OpenAlex / Semantic Scholar.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS authors (
    author_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    openalex_id  TEXT UNIQUE,
    s2_id        TEXT UNIQUE,
    display_name TEXT NOT NULL,
    institution  TEXT,
    synced_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------------------------
-- paper_authors — Many-to-many join between papers and authors.
-- position = authorship order (0 = first/lead author).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id  UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    author_id UUID NOT NULL REFERENCES authors(author_id) ON DELETE CASCADE,
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (paper_id, author_id)
);


-- ---------------------------------------------------------------------------
-- collections — User-curated groups of papers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collections (
    collection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_collections_user ON collections (user_id);


-- ---------------------------------------------------------------------------
-- collection_papers — Papers within a collection.
-- sequence_order supports drag-reorder and agent-generated reading plans.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id  UUID NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
    paper_id       UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sequence_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, paper_id)
);


-- ---------------------------------------------------------------------------
-- reading_progress — One record per user-paper pair.
-- Status lifecycle: not_started → reading → completed | skipped
-- UNIQUE constraint enables ON CONFLICT upserts from mark_paper_status tool.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reading_progress (
    progress_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    paper_id    UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'not_started'
                    CHECK (status IN ('not_started', 'reading', 'completed', 'skipped')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, paper_id)
);

CREATE INDEX IF NOT EXISTS idx_reading_progress_user ON reading_progress (user_id);
CREATE INDEX IF NOT EXISTS idx_reading_progress_status ON reading_progress (status);


-- ---------------------------------------------------------------------------
-- notes — Free-text annotations on papers (multiple per user-paper pair).
-- Notes are embedded into note_embeddings for semantic cross-note retrieval.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notes (
    note_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    paper_id   UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    note_text  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notes_user_paper ON notes (user_id, paper_id);


-- ---------------------------------------------------------------------------
-- topic_context — Wikipedia summaries for prerequisite topic knowledge.
-- Cached here after the first explain_topic call to avoid repeat API hits.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS topic_context (
    topic_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_name        TEXT NOT NULL UNIQUE,
    wikipedia_summary TEXT,
    wiki_url          TEXT,
    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_topic_context_name ON topic_context (topic_name);
