-- =============================================================================
-- 19_note_pins.sql — the note you keep coming back to
-- =============================================================================
-- Search answers "where is the note about X". Pinning answers a different
-- question — "the one I am working from this week" — and no amount of search
-- helps with it, because the thing you want is not distinguished by its words.
--
-- Per note rather than per paper: what you return to is a particular thought,
-- not everything you ever wrote about one study.
--
-- Ordering rather than a separate section. A pinned note sorts first, which
-- floats its paper's group to the top of the page and puts the note at the head
-- of that group — so the page keeps one structure instead of growing a second
-- list above it that the grouping does not apply to. The filter menu offers
-- "Pinned" for the times you want only those.
--
-- Mirrors conversations.pinned from sql/13, deliberately: two things a person
-- pins in the same product should behave the same way, and the index is the
-- same shape for the same reason — the sort comes off the index rather than
-- from sorting the result.
-- =============================================================================

ALTER TABLE notes
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;

-- Pinned first, then newest, which is the order every listing reads them in.
DROP INDEX IF EXISTS idx_notes_user_recent;
CREATE INDEX IF NOT EXISTS idx_notes_user_recent
    ON notes (user_id, pinned DESC, created_at DESC);

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT count(*) FILTER (WHERE pinned) AS pinned, count(*) FROM notes;
--
-- And that the ordering comes off the index rather than a sort:
--
-- EXPLAIN SELECT note_id FROM notes
--  WHERE user_id = '<your-user-id>'
--  ORDER BY pinned DESC, created_at DESC LIMIT 20;
