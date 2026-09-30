-- =============================================================================
-- 21_display_name.sql — a name you chose, and one Google keeps sending
-- =============================================================================
-- `touch_user_login` refreshes `display_name` from the OAuth profile on every
-- sign-in. That is right for a name nobody has touched — people change their
-- Google name and expect this to follow — and wrong the moment somebody sets
-- their own, because the next sign-in would silently undo it.
--
-- One flag, rather than a second name column. Two columns would mean deciding
-- which to display everywhere it is displayed, and the answer is always "the
-- one they chose, if they chose one". A boolean says exactly that and leaves
-- `display_name` the single field every template already reads.
--
-- Default false, so every existing account keeps following its provider — the
-- behaviour they have today, and nobody's name changes because of a migration.
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS display_name_custom BOOLEAN NOT NULL DEFAULT false;

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT email, display_name, display_name_custom FROM users ORDER BY email;
--
-- Expect every row false until somebody edits their name in settings. After
-- that, their next sign-in must leave it alone:
--
-- SELECT display_name, display_name_custom, last_login_at
--   FROM users WHERE email = '<your-email>';
