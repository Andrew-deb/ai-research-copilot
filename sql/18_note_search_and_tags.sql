-- =============================================================================
-- 18_note_search_and_tags.sql — finding a note again
-- =============================================================================
-- Six notes fit on a screen. Sixty do not, and the list stops being a way to
-- find anything. Two ways through it:
--
--   tags    the cut a researcher actually makes — "methods to try", "for the
--           lit review" — which runs ACROSS papers and so cannot be expressed
--           by the paper grouping the page already has.
--
--   search  the one they make when they remember a phrase and nothing else.
--
-- **Full text, not semantic, and the choice is deliberate.**
--
-- `note_embeddings` has existed since Phase 1 and holds ZERO rows: nothing ever
-- populated it, and the paper page's placeholder promising notes are "embedded
-- for cross-note semantic search" has been describing a feature that was never
-- built. Wiring it up would mean an embedding call on every save — latency and
-- HF quota on the one action that must feel instant.
--
-- And it would be the wrong tool. Semantic search finds notes LIKE the query;
-- what someone hunting their own note wants is the note containing the words
-- they remember writing. Their own phrasing is the strongest signal available
-- and an embedding deliberately blurs it. Semantic retrieval over notes stays
-- the agent's job, where "find me things related to this" is the actual
-- question.
--
-- No extension needed: tsvector and GIN are core Postgres. pg_trgm is not
-- installed on this instance, which also rules out trigram matching.
-- =============================================================================

-- --- Tags --------------------------------------------------------------------
-- An array rather than a join table. A tag here is a label, not an entity: it
-- has no attributes of its own, nobody renames one across a library, and every
-- query wants "the notes carrying this" — which is one GIN lookup rather than a
-- join. The tag list itself comes from `unnest`, so nothing is lost.
ALTER TABLE notes
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_notes_tags ON notes USING GIN (tags);

-- --- Search ------------------------------------------------------------------
-- A stored generated column, so the index is maintained by the database rather
-- than by remembering to update it on every write. An expression index would
-- work too; this way the vector is visible and can be inspected when a search
-- returns something surprising.
--
-- The title is weighted 'A' and the body 'B': someone who named a note "Retriever
-- ablations" and then wrote four paragraphs mentioning retrieval throughout
-- means the title as the stronger claim about what the note is.
ALTER TABLE notes
    ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(note_text, '')), 'B')
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_notes_search ON notes USING GIN (search_tsv);

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT column_name, data_type, is_generated
--   FROM information_schema.columns
--  WHERE table_name = 'notes' AND column_name IN ('tags', 'search_tsv');
--
-- Expect: tags ARRAY NEVER, search_tsv tsvector ALWAYS.
--
-- The vector fills itself for every existing row, generated columns being
-- backfilled by the ALTER:
--
-- SELECT count(*) FILTER (WHERE search_tsv IS NOT NULL) AS indexed, count(*)
--   FROM notes;
--
-- And a search, once there is something to find:
--
-- SELECT title, ts_rank(search_tsv, websearch_to_tsquery('english', 'retriever'))
--   FROM notes
--  WHERE search_tsv @@ websearch_to_tsquery('english', 'retriever')
--  ORDER BY 2 DESC;
