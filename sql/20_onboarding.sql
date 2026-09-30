-- =============================================================================
-- 20_onboarding.sql — what a new account tells us about itself (Phase 3.7)
-- =============================================================================
-- Two tables. They are separate because they answer questions with different
-- lifetimes: a profile is a handful of settled facts about a person, while
-- interests accumulate, decay and arrive from several directions.
--
-- Deliberately NOT built here: the "For You" surface that reads any of this.
-- §8g of the design marks that documentation-only for Phase 3, so onboarding
-- collects and stores; nothing consumes it yet. Collecting first is the right
-- order — an empty interest table makes personalisation impossible to build,
-- and a personalisation surface over no data is impossible to judge.
-- =============================================================================

-- --- The profile -------------------------------------------------------------
-- One row per person, created when they first answer anything.
--
-- `onboarding_step` is the count of steps COMPLETED, so a refresh resumes where
-- they stopped rather than starting over. Five steps, each skippable, and a
-- skipped step still advances the counter: the question was put to them and
-- they answered it by declining, which is an answer.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id                 UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    onboarding_step         SMALLINT NOT NULL DEFAULT 0,   -- 0 = not started, 5 = done
    onboarding_completed_at TIMESTAMPTZ,
    researcher_type         TEXT,        -- "which best describes you"
    primary_goal            TEXT,        -- "what brings you here"
    help_tasks              JSONB,       -- "what should the copilot help with"
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- Interests ---------------------------------------------------------------
-- ONE ROW PER (user, kind, value, source) — not one per interest.
--
-- That is the whole design. Explicit choices and behavioural evidence
-- accumulate side by side, and the profile is a rollup ACROSS sources. Collapse
-- them into one row per interest and a weak later signal overwrites a
-- deliberate answer: somebody who said "I research immunology" and then read
-- three machine-learning papers would silently stop being an immunologist.
--
-- It is also what lets the two be told apart at read time. "You told us this"
-- and "we inferred this" deserve different treatment in an interface, and a
-- schema that cannot distinguish them forecloses that.
CREATE TABLE IF NOT EXISTS user_research_interests (
    interest_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('field', 'topic')),
    value          TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN (
                       'explicit_onboarding', 'repeated_search',
                       'saved_paper', 'reading_activity', 'note')),
    confidence     REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    evidence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, kind, value, source)
);

-- Every read is "this person's interests, strongest first".
CREATE INDEX IF NOT EXISTS idx_interests_user
    ON user_research_interests (user_id, confidence DESC);

-- And the rollup the field tabs will need: one kind, across sources.
CREATE INDEX IF NOT EXISTS idx_interests_user_kind
    ON user_research_interests (user_id, kind, confidence DESC);

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT onboarding_step, onboarding_completed_at IS NOT NULL AS done
--   FROM user_profiles WHERE user_id = '<your-user-id>';
--
-- The interests, with where each came from:
--
-- SELECT kind, value, source, confidence
--   FROM user_research_interests
--  WHERE user_id = '<your-user-id>'
--  ORDER BY confidence DESC, value;
--
-- Expect every row from onboarding to read source='explicit_onboarding' and
-- confidence=0.9. Phase 3 writes nothing else.
