-- Retain an existing private object without copying bytes or changing its ID.
-- Keep expires_at as the original temporary deadline; retained_at overrides
-- temporary expiry only. Owner deletion and explicit deletion still clean up.
ALTER TABLE private_uploads ADD COLUMN IF NOT EXISTS retained_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_uploads_retained_owner
    ON private_uploads(user_id, retained_at DESC, document_id)
    WHERE retained_at IS NOT NULL AND status NOT IN ('deleted','deleting');
