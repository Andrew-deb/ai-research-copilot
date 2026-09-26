-- Apply after 15_agent_runs.sql, before deploying conversation version controls.
-- Existing threads are backfilled as one selected linear path.
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'agent'
        CHECK (origin IN ('agent', 'assistant')),
    ADD COLUMN IF NOT EXISTS origin_context TEXT,
    ADD COLUMN IF NOT EXISTS selected_message_id UUID;

ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS parent_message_id UUID;

DO $$ BEGIN
    ALTER TABLE conversation_messages
        ADD CONSTRAINT conversation_message_parent_fk
        FOREIGN KEY (parent_message_id) REFERENCES conversation_messages(message_id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE conversations
        ADD CONSTRAINT conversation_selected_message_fk
        FOREIGN KEY (selected_message_id) REFERENCES conversation_messages(message_id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

WITH predecessors AS (
    SELECT m.message_id, lag(m.message_id) OVER
        (PARTITION BY m.conversation_id ORDER BY m.seq) AS preceding
      FROM conversation_messages AS m JOIN conversations AS c
        ON c.conversation_id = m.conversation_id
     WHERE c.selected_message_id IS NULL
)
UPDATE conversation_messages AS m SET parent_message_id = p.preceding
  FROM predecessors AS p
 WHERE m.message_id = p.message_id AND m.parent_message_id IS NULL;

WITH latest AS (
    SELECT DISTINCT ON (conversation_id) conversation_id, message_id
      FROM conversation_messages ORDER BY conversation_id, seq DESC
)
UPDATE conversations AS c SET selected_message_id = latest.message_id
  FROM latest
 WHERE c.conversation_id = latest.conversation_id AND c.selected_message_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_message_parent
    ON conversation_messages (conversation_id, parent_message_id, seq);
CREATE INDEX IF NOT EXISTS idx_conversations_origin
    ON conversations (user_id, origin, pinned DESC, updated_at DESC);

-- Search entries are actual submitted corpus queries, never fabricated chat
-- messages. The global paper corpus stays shared; this stores only the query.
CREATE TABLE IF NOT EXISTS user_search_history (
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('keyword', 'semantic')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, query, mode)
);
CREATE INDEX IF NOT EXISTS idx_user_search_history_recent
    ON user_search_history (user_id, updated_at DESC);
