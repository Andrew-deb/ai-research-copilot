-- =============================================================================
-- 12_conversations.sql — research chat history (Phase 3.4)
-- =============================================================================
-- Deferred deliberately in 3.3: "inventing a message schema to satisfy a
-- sidebar would almost certainly get it wrong, because the shape of a stored
-- turn depends on what tool calls and citations an agent actually produces."
-- Those now exist and have settled, so the reason to wait is gone.
--
-- The envelope is stored as JSONB rather than flattened into columns. A turn
-- carries citations, sources consulted, the tool calls it made and its usage
-- counts; normalising those into tables would mean a migration every time the
-- envelope grows, and every read would have to reassemble something the
-- application already had in one piece. Stored whole, a reopened conversation
-- replays through the same renderer as a live one — which is the only way the
-- replay can be trusted to match.
--
-- Anonymous visitors are not represented here at all. user_id is NOT NULL and
-- that is the enforcement: the demo tier promises nothing is kept, and a
-- nullable owner is how that promise quietly stops being true.
-- =============================================================================

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    pinned          BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ON DELETE CASCADE, unlike ai_operations which survives its user.
-- The distinction is what the row is *for*: a cost record is operational history
-- that must outlive an account, a conversation is the person's own writing and
-- must not.

-- The sidebar's only query: this user's conversations, pinned first and then
-- most recently used. The index carries `pinned` so the ordering is read
-- straight off it rather than sorted afterwards.
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations (user_id, pinned DESC, updated_at DESC);


CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT,

    -- The envelope, kept whole. Defaults rather than NULL so a reader never has
    -- to distinguish "no citations" from "not recorded".
    citations       JSONB NOT NULL DEFAULT '[]'::jsonb,
    sources         JSONB NOT NULL DEFAULT '[]'::jsonb,
    tool_calls      JSONB NOT NULL DEFAULT '[]'::jsonb,
    usage           JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Ordering is by seq, not by created_at: a question and its answer are
    -- written within the same second, and a timestamp tie would let them
    -- reorder and put the answer above the question.
    UNIQUE (conversation_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_thread
    ON conversation_messages (conversation_id, seq);


-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT c.title,
--        count(m.*) FILTER (WHERE m.role = 'user')      AS questions,
--        count(m.*) FILTER (WHERE m.role = 'assistant') AS answers,
--        jsonb_array_length(
--            coalesce((SELECT m2.citations FROM conversation_messages m2
--                       WHERE m2.conversation_id = c.conversation_id
--                         AND m2.role = 'assistant'
--                       ORDER BY m2.seq DESC LIMIT 1), '[]'::jsonb)) AS last_citations,
--        c.updated_at
--   FROM conversations c
--   LEFT JOIN conversation_messages m ON m.conversation_id = c.conversation_id
--  GROUP BY c.conversation_id
--  ORDER BY c.updated_at DESC;
