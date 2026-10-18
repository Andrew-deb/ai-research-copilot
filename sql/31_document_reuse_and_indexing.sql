-- Apply after 30_retained_documents.sql. Private documents never enter papers.
CREATE TABLE IF NOT EXISTS private_upload_references (
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES private_uploads(document_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    PRIMARY KEY(conversation_id, document_id)
);
CREATE TABLE IF NOT EXISTS collection_documents (
    collection_id UUID NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES private_uploads(document_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(collection_id, document_id)
);
CREATE TABLE IF NOT EXISTS document_index_jobs (
    document_id UUID PRIMARY KEY REFERENCES private_uploads(document_id) ON DELETE CASCADE,
    contract TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    error_code TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The vector type is dimension-free so future model changes need not rewrite
-- this table. Contract+dimension checks prevent comparing different spaces.
CREATE TABLE IF NOT EXISTS document_chunks (
    document_id UUID NOT NULL REFERENCES private_uploads(document_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page INTEGER NOT NULL,
    page_offset INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector NOT NULL,
    contract TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    PRIMARY KEY(document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_document_chunks_owner ON document_chunks(user_id,document_id);
CREATE INDEX IF NOT EXISTS idx_document_index_jobs_status ON document_index_jobs(status,updated_at);
ALTER TABLE private_uploads ADD COLUMN IF NOT EXISTS index_bytes BIGINT NOT NULL DEFAULT 0 CHECK(index_bytes>=0);
