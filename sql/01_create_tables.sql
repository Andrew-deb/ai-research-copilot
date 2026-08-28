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
    synced_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial index — only DOIs that exist (many preprints/theses have no DOI)
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers (source_api);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers (publication_year);


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
