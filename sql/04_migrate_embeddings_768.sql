-- =============================================================================
-- 04_migrate_embeddings_768.sql — 384-dim (all-MiniLM-L6-v2)
--                              -> 768-dim (nomic-ai/modernbert-embed-base)
-- =============================================================================
-- Run ONCE, then re-run the ingestion notebook to repopulate.
--
-- pgvector cannot change a column's dimension while data is present, and the
-- existing 384-dim vectors are meaningless under the new model anyway - they
-- were produced in a different vector space. So: truncate, alter, rebuild.
--
-- DESTROYS: paper_embeddings, note_embeddings  (regenerable by the pipeline)
-- PRESERVES: papers, authors, collections, notes, reading_progress,
--            learning_goals, topic_context, mcp_traces
--
-- Between this script and the next pipeline run, semantic search returns
-- nothing. Keyword search, collections and notes are unaffected.
-- =============================================================================

BEGIN;

TRUNCATE paper_embeddings, note_embeddings;

DROP INDEX IF EXISTS idx_paper_embeddings_embedding;
DROP INDEX IF EXISTS idx_note_embeddings_embedding;

ALTER TABLE paper_embeddings ALTER COLUMN embedding TYPE vector(768);
ALTER TABLE note_embeddings  ALTER COLUMN embedding TYPE vector(768);

-- 768 is well under pgvector's 2000-dimension index ceiling, so HNSW still applies.
CREATE INDEX idx_paper_embeddings_embedding
    ON paper_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_note_embeddings_embedding
    ON note_embeddings  USING hnsw (embedding vector_cosine_ops);

COMMIT;

-- Verify:
--   SELECT atttypmod FROM pg_attribute
--    WHERE attrelid = 'paper_embeddings'::regclass AND attname = 'embedding';  -- 768
--   SELECT count(*) FROM paper_embeddings;                                     -- 0 until reingest
