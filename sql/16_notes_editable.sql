-- =============================================================================
-- 16_notes_editable.sql — notes you can revise, and notes about nothing
-- =============================================================================
-- `notes` was written for a feature that only ever appended: save text against a
-- paper, never change it, never remove it, never read it back anywhere but that
-- paper's own page. Two columns encode that assumption and both have to go.
--
-- 1. No `updated_at`. A note that has been revised looked identical to one
--    written in a single sitting, and "when did I last think about this" is a
--    question people ask of their own notes constantly.
--
-- 2. `paper_id NOT NULL`. Every thought had to belong to a paper. Plenty do not
--    — a question to chase, a summary of three papers at once, a reminder about
--    the method rather than the study. Forcing those onto an arbitrary paper
--    files them where their author will not look.
--
-- Both changes are additive to existing rows: `updated_at` backfills from
-- `created_at`, which is true (an unedited note was last changed when it was
-- written), and dropping NOT NULL invalidates nothing already stored.
-- =============================================================================

ALTER TABLE notes
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- Backfilled rather than defaulted, so existing notes get their real history
-- instead of today's date. A note written in March was last changed in March.
UPDATE notes SET updated_at = created_at WHERE updated_at IS NULL;

ALTER TABLE notes
    ALTER COLUMN updated_at SET DEFAULT now();

ALTER TABLE notes
    ALTER COLUMN updated_at SET NOT NULL;

-- A note need not be about a paper.
ALTER TABLE notes
    ALTER COLUMN paper_id DROP NOT NULL;

-- The notes page reads every note by one person, newest first, and the existing
-- index is (user_id, paper_id) — which cannot serve that ordering.
CREATE INDEX IF NOT EXISTS idx_notes_user_recent
    ON notes (user_id, created_at DESC);

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT column_name, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_name = 'notes'
--    AND column_name IN ('paper_id', 'updated_at');
--
-- Expect: paper_id YES (nullable), updated_at NO with a now() default.
--
-- And that nothing lost its history:
--
-- SELECT count(*) FILTER (WHERE updated_at = created_at) AS untouched,
--        count(*) FILTER (WHERE updated_at > created_at) AS revised,
--        count(*) FILTER (WHERE paper_id IS NULL)        AS standalone
--   FROM notes;
