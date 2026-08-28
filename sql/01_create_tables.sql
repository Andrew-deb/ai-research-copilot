-- =============================================================================
-- 01_create_tables.sql — Core Relational Schema for AI Research & Learning Copilot
-- =============================================================================
--
-- This script creates the 10 relational tables that form the backbone of the
-- application. All tables follow consistent conventions:
--
--   • UUID primary keys via gen_random_uuid()  — globally unique, no collisions
--     across distributed systems, safe for cross-table references without a
--     central sequence coordinator.
--
--   • TIMESTAMPTZ (not TIMESTAMP) for all time columns — stores the instant in
--     UTC internally while allowing timezone-aware display. Critical for a
--     multi-timezone user base.
--
--   • Foreign keys with ON DELETE CASCADE — when a parent row is deleted,
--     all dependent rows are automatically removed. Prevents orphaned data.
--
--   • CHECK constraints — enforce domain rules at the database level so invalid
--     data can never be written, regardless of which application layer calls it.
--
-- Run this ONCE via setup_db.py to initialize the Lakebase schema.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. users — Every person who uses the application
-- ---------------------------------------------------------------------------
-- The anchor table for all user-scoped data. Email is the natural identifier
-- (Databricks Apps inject it via X-Forwarded-Email), but we use a synthetic
-- UUID as the primary key so joins are fast and the schema is decoupled from
-- any external identity provider.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index on email for fast lookups during authentication middleware resolution.
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);


-- ---------------------------------------------------------------------------
-- 2. learning_goals — What a user wants to learn
-- ---------------------------------------------------------------------------
-- A learning goal is the starting point of the user journey. The agent uses
-- goal descriptions to drive semantic paper search (via vector similarity
-- against embedded abstracts).
--
-- Status lifecycle:  active → completed | archived
--   • active    — user is currently pursuing this goal
--   • completed — user has finished the learning objective
--   • archived  — goal is no longer relevant but kept for history
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
-- 3. papers — Unified paper store from all three API sources
-- ---------------------------------------------------------------------------
-- This is the standardized destination for papers fetched from OpenAlex,
-- Semantic Scholar, and enriched with Wikipedia topic context. Each API returns
-- different JSON shapes; the Spark pipeline and brokers normalize them into
-- this common schema.
--
-- Key design decisions:
--   • openalex_id and semantic_scholar_id are NULLABLE because a paper may
--     exist in one source but not the other. The UNIQUE constraints allow
--     efficient upsert-by-source-id (INSERT ON CONFLICT).
--   • doi is also NULLABLE — not all papers have DOIs (preprints, theses).
--   • payload JSONB stores the raw API response for auditability and for any
--     fields we haven't yet promoted to top-level columns.
--   • tldr and influence_score come from Semantic Scholar enrichment — they
--     are NULL until the S2 enrichment step runs.
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
    synced_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial index on DOI (only non-null values) for cross-source deduplication.
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers (source_api);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers (publication_year);


-- ---------------------------------------------------------------------------
-- 4. authors — Researcher profiles from OpenAlex and Semantic Scholar
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
-- 5. paper_authors — Many-to-many: which authors wrote which papers
-- ---------------------------------------------------------------------------
-- Position tracks authorship order (first author = 0, second = 1, etc.).
-- This matters for citation conventions and for the agent to correctly
-- attribute "lead author" vs. "contributing author."
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id  UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    author_id UUID NOT NULL REFERENCES authors(author_id) ON DELETE CASCADE,
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (paper_id, author_id)
);


-- ---------------------------------------------------------------------------
-- 6. collections — User-curated groups of papers (e.g., "NLP Fundamentals")
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
-- 7. collection_papers — Many-to-many: papers in a collection
-- ---------------------------------------------------------------------------
-- sequence_order allows users to drag-reorder papers into a reading sequence.
-- The agent's "generate reading plan" tool writes this order based on
-- citation dependency analysis (introductory papers first, advanced last).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id  UUID NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
    paper_id       UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sequence_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, paper_id)
);


-- ---------------------------------------------------------------------------
-- 8. reading_progress — Per-user, per-paper reading status
-- ---------------------------------------------------------------------------
-- Status lifecycle:  not_started → reading → completed
--                                         ↘ skipped
--
-- UNIQUE(user_id, paper_id) ensures one progress record per user-paper pair,
-- which enables ON CONFLICT upserts when the agent calls mark_paper_status.
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
-- 9. notes — Free-text annotations on papers
-- ---------------------------------------------------------------------------
-- Unlike reading_progress (one per user-paper), a user can write MULTIPLE
-- notes on the same paper over time. The agent's save_note tool appends here.
-- Notes are also embedded into note_embeddings for semantic retrieval.
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
-- 10. topic_context — Wikipedia summaries for prerequisite knowledge
-- ---------------------------------------------------------------------------
-- When the agent's explain_topic tool is called, it fetches a Wikipedia
-- summary and caches it here. This provides accessible prerequisite context
-- that helps the agent generate better study plans for beginners.
-- topic_name is UNIQUE to prevent duplicate fetches for the same topic.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS topic_context (
    topic_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_name        TEXT NOT NULL UNIQUE,
    wikipedia_summary TEXT,
    wiki_url          TEXT,
    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_topic_context_name ON topic_context (topic_name);
