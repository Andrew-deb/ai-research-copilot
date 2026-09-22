-- =============================================================================
-- 13_conversation_pin.sql — pinning a conversation (Phase 3.4)
-- =============================================================================
-- Separate from 12 because `CREATE TABLE IF NOT EXISTS` does not alter a table
-- that already exists: anyone who ran 12 before this column was added to it
-- would never get the column from re-running 12. Both files are idempotent, so
-- running 12 and then 13 is correct whether or not 12 was applied earlier.
--
-- Pinning is per conversation rather than a separate table: it is one boolean
-- owned by the row it describes, and a join to discover it would cost more than
-- the fact is worth.
-- =============================================================================

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;

-- Pinned first, then most recently used — the order the sidebar reads them in,
-- so the ordering comes off the index instead of a sort.
DROP INDEX IF EXISTS idx_conversations_user;
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations (user_id, pinned DESC, updated_at DESC);

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT title, pinned, updated_at
--   FROM conversations
--  WHERE user_id = '<your-user-id>'
--  ORDER BY pinned DESC, updated_at DESC;
