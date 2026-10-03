-- Approval continuations retain their costs but are not new user questions.
ALTER TABLE ai_operations ADD COLUMN IF NOT EXISTS is_continuation BOOLEAN NOT NULL DEFAULT false;
