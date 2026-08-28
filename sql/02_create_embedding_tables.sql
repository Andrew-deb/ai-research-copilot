-- =============================================================================
-- 02_create_embedding_tables.sql — Vector Tables for Semantic Search
-- =============================================================================
--
-- This script creates the pgvector infrastructure that powers semantic
-- (meaning-based) retrieval in the AI Research & Learning Copilot.
--
-- THEORETICAL FOUNDATIONS:
--
-- 1. VECTOR EMBEDDINGS
--    An embedding is a dense array of floating-point numbers that captures the
--    semantic meaning of text. The model we use (all-MiniLM-L6-v2) maps any
--    input string into exactly 384 numbers. Texts with similar meaning produce
--    vectors that are geometrically close in 384-dimensional space.
--
--    Example: "neural network training" and "deep learning optimization"
--    would produce vectors with a high cosine similarity (~0.85), even though
--    they share zero exact words.
--
-- 2. VECTOR(384) — DIMENSION MUST MATCH THE MODEL
--    The column type VECTOR(384) is a strict contract with pgvector. If the
--    Spark pipeline uses all-MiniLM-L6-v2 (384-dim) but the column says
--    VECTOR(768), Postgres will REJECT every INSERT with a dimension mismatch
--    error. This is a compile-time safety net, not a runtime hint.
--
-- 3. COSINE DISTANCE vs. COSINE SIMILARITY
--    • Cosine Similarity:  1.0 = identical meaning, 0.0 = unrelated
--    • Cosine Distance:    0.0 = identical meaning, 2.0 = opposite
--    • pgvector's <=> operator computes COSINE DISTANCE
--    • To get similarity: SELECT 1 - (embedding <=> query_vector) AS similarity
--    • We ORDER BY embedding <=> query_vector ASC (smallest distance = best match)
--
-- 4. HNSW INDEX (Hierarchical Navigable Small World)
--    Without an index, finding the top-5 closest vectors requires computing
--    the distance to EVERY row — O(N) brute-force scan. For 100K papers,
--    that's 100K distance calculations per query.
--
--    HNSW builds a multi-layer graph connecting nearby vectors. Search starts
--    at the top sparse layer and navigates down, achieving O(log N) query time.
--    For 100K papers, that's ~17 distance calculations instead of 100K.
--
--    Trade-off: HNSW indexes are slower to BUILD (the graph must be
--    constructed), but dramatically faster to QUERY. For our use case
--    (infrequent batch inserts via Spark, frequent real-time queries via
--    the agent and dashboard), this trade-off is ideal.
--
--    The vector_cosine_ops operator class tells HNSW to optimize for cosine
--    distance (not L2/Euclidean or inner product).
--
-- =============================================================================


-- Enable pgvector extension (required before any VECTOR column can be created).
-- This is idempotent — safe to run multiple times.
CREATE EXTENSION IF NOT EXISTS vector;


-- ---------------------------------------------------------------------------
-- 1. paper_embeddings — Chunked and embedded paper abstracts
-- ---------------------------------------------------------------------------
-- Each paper's abstract is split into overlapping chunks (800 chars with 100
-- char overlap) by the Spark pipeline, and each chunk gets its own embedding.
--
-- Why chunk instead of embedding the whole abstract?
--   a) Model token limits: MiniLM handles ~256-512 tokens; longer text is
--      silently truncated, losing information.
--   b) Semantic granularity: A 2000-char abstract discusses methods, results,
--      AND conclusions. Chunking lets vector search retrieve the specific
--      passage that matches the query, not a diluted average.
--
-- chunk_index preserves the original order of chunks within a paper.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_embeddings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id    UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_text  TEXT NOT NULL,
    embedding   VECTOR(384) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW index for fast cosine similarity search on paper embeddings.
-- This is the critical index that makes semantic search sub-second.
CREATE INDEX IF NOT EXISTS idx_paper_embeddings_embedding
    ON paper_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- B-tree index for filtering by paper_id (e.g., "get all chunks for paper X").
CREATE INDEX IF NOT EXISTS idx_paper_embeddings_paper
    ON paper_embeddings (paper_id);


-- ---------------------------------------------------------------------------
-- 2. note_embeddings — Embedded user notes for cross-note search
-- ---------------------------------------------------------------------------
-- When a user saves a note on a paper, the Spark pipeline embeds it so the
-- agent can semantically search across ALL of a user's notes.
--
-- Example query the agent can answer:
--   "What did I write about attention mechanisms?"
--   → Vector search across note_embeddings → retrieve relevant note chunks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS note_embeddings (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id    UUID NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    chunk_text TEXT NOT NULL,
    embedding  VECTOR(384) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW index for fast cosine similarity search on note embeddings.
CREATE INDEX IF NOT EXISTS idx_note_embeddings_embedding
    ON note_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- B-tree index for filtering by note_id.
CREATE INDEX IF NOT EXISTS idx_note_embeddings_note
    ON note_embeddings (note_id);
