-- =============================================================================
-- 02_create_embedding_tables.sql — pgvector tables for semantic search
-- =============================================================================
-- Requires: 01_create_tables.sql must have run first (FK to papers, notes).
-- VECTOR(768) dimension is fixed to nomic-ai/modernbert-embed-base output size.
-- Changing the embedding model requires rebuilding these tables and the index.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ---------------------------------------------------------------------------
-- paper_embeddings — Chunked + embedded paper abstracts.
-- Text is split into overlapping 4000-char chunks before embedding. The model
-- reads 8192 tokens (~32k chars), so a whole abstract fits in a single chunk.
-- chunk_index preserves the original order within a paper.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_embeddings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id    UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_text  TEXT NOT NULL,
    embedding   VECTOR(768) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW index: O(log N) cosine similarity search (vs. O(N) brute force)
CREATE INDEX IF NOT EXISTS idx_paper_embeddings_embedding
    ON paper_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_paper_embeddings_paper ON paper_embeddings (paper_id);


-- ---------------------------------------------------------------------------
-- note_embeddings — Embedded user notes for cross-note semantic search.
-- Allows the agent to answer: "What did I write about attention mechanisms?"
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS note_embeddings (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id    UUID NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    chunk_text TEXT NOT NULL,
    embedding  VECTOR(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_note_embeddings_embedding
    ON note_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_note_embeddings_note ON note_embeddings (note_id);
