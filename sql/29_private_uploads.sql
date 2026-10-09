-- Private attachments: bytes live in object storage, not the global papers table.
-- SET NULL preserves cleanup locators when a user/conversation is deleted.
CREATE TABLE IF NOT EXISTS private_uploads (
    document_id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(conversation_id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    storage_location_id TEXT NOT NULL,
    object_key TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    artifact_hash TEXT,
    original_bytes BIGINT NOT NULL CHECK (original_bytes > 0),
    reserved_bytes BIGINT NOT NULL CHECK (reserved_bytes >= 0),
    artifact_bytes BIGINT NOT NULL DEFAULT 0,
    upload_lease_until TIMESTAMPTZ DEFAULT now()+interval '5 minutes',
    status TEXT NOT NULL CHECK (status IN ('uploading','queued','processing','ready','failed','deleting','deleted')),
    error_code TEXT,
    page_count INTEGER,
    extraction_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_uploads_owner ON private_uploads(user_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_uploads_cleanup ON private_uploads(expires_at) WHERE status <> 'deleted';
CREATE TABLE IF NOT EXISTS private_upload_jobs (
    document_id UUID PRIMARY KEY REFERENCES private_uploads(document_id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('extract','delete')),
    status TEXT NOT NULL CHECK (status IN ('queued','processing','done')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_upload_jobs_queue ON private_upload_jobs(status, updated_at);
