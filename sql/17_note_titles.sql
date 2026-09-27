-- =============================================================================
-- 17_note_titles.sql — a note you can find again by name
-- =============================================================================
-- Notes were body text and nothing else. That is fine while you have four of
-- them and every one is visible at once; it stops being fine at forty, where a
-- list of prose excerpts all look alike and the only way to find the one you
-- want is to read them all.
--
-- Nullable, and deliberately so. A note jotted in three words needs no name,
-- and demanding one would make the quick capture the notepad exists for into a
-- two-field form. Untitled notes fall back to their first line in the UI, which
-- is what the reader was going to skim anyway.
--
-- No backfill for the same reason: deriving a title from existing bodies would
-- invent names their authors never chose, and an invented name is worse than
-- none because it looks deliberate.
-- =============================================================================

ALTER TABLE notes
    ADD COLUMN IF NOT EXISTS title TEXT;

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT count(*) FILTER (WHERE title IS NOT NULL) AS titled,
--        count(*) FILTER (WHERE title IS NULL)     AS untitled
--   FROM notes;
--
-- Expect every existing row untitled — nothing is backfilled.
