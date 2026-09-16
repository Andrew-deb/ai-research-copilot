-- =============================================================================
-- 07_fulltext_status_taxonomy.sql — Phase 2.6, from the first real pipeline run
-- =============================================================================
-- Run ONCE against an existing database. Additive plus one deliberate reset.
--
-- The first real run (152 papers) exposed two classification errors:
--
--   1. Every HTTP failure was recorded as `fetch_failed`, which the retry policy
--      treats as transient. Of 35 such rows, 32 were 403 or 404 - a publisher
--      declining automated download, or a dead URL. Neither improves with time,
--      so the policy queued 64 pointless requests across future runs.
--
--   2. A 200 response whose Content-Type was not PDF was recorded as
--      `parse_failed`. Nothing was ever parsed: `open_access_url` had pointed at
--      an HTML landing page. All 42 `parse_failed` rows were this case, which
--      hid the real finding - OpenAlex knows a direct `pdf_url` we never asked
--      for.
--
-- New taxonomy, split by whether retrying could ever help:
--
--   RETRYABLE     fetch_failed   408 / 429 / 5xx / timeout / connection reset
--   PERMANENT     access_denied  401 / 403 - the publisher blocks robots
--                 not_found      404 / 410 - the URL is wrong or dead
--                 not_pdf        200, but every candidate URL served HTML
--                 too_large      exceeded the size ceiling
--                 parse_failed   a real PDF that pypdf could not read
--                 no_sections    parsed, but no recognisable headings
--                 no_url         nothing to try
--   DONE          ok
-- =============================================================================

BEGIN;

ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_fulltext_status_check;
ALTER TABLE papers ADD CONSTRAINT papers_fulltext_status_check
    CHECK (fulltext_status IS NULL OR fulltext_status IN (
        'ok',
        'no_url',
        'not_pdf',
        'access_denied',
        'not_found',
        'too_large',
        'fetch_failed',
        'parse_failed',
        'no_sections'
    ));

-- One-time reset. Rows carrying the two mis-assigned statuses are returned to
-- "never attempted" so the next run re-tries them with the repository-first URL
-- list and records a status that means what it says.
--
-- This costs one extra attempt for the ~32 papers that will turn out to be
-- access_denied or not_found. That is the price of converting a permanently
-- wrong classification into a correct one, and it is paid exactly once: those
-- statuses are never retried again.
--
-- `ok` and `no_sections` are deliberately NOT reset. Their PDFs parsed fine;
-- re-downloading them would be pure waste.
UPDATE papers
   SET fulltext_status = NULL,
       fulltext_attempts = 0,
       fulltext_checked_at = NULL
 WHERE fulltext_status IN ('fetch_failed', 'parse_failed');

COMMIT;

-- Verify:
--   SELECT coalesce(fulltext_status, 'not_attempted') AS status, count(*)
--     FROM papers GROUP BY 1 ORDER BY 2 DESC;
--   -- expect the previous fetch_failed + parse_failed counts to appear as
--   -- 'not_attempted', ready for the next run.
