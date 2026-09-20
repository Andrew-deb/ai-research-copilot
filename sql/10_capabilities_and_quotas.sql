-- =============================================================================
-- 10_capabilities_and_quotas.sql — Phase 3.2: the anonymous demo tier
-- =============================================================================
-- Run ONCE against an existing database. Additive; deletes nothing.
--
-- Anonymous visitors can browse, search and ask a limited number of questions.
-- They cannot write. Two things make that safe:
--
--   usage_counters        metering, so "limited" is enforced rather than hoped
--   collections.is_curated  read-only demo content that nobody can edit
--
-- Capability (may this tier use the feature at all) and quota (how much) are
-- separate checks. Neither is expressed in SQL - this file only provides the
-- storage they need.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- usage_counters — one table, three scopes.
-- ---------------------------------------------------------------------------
--   anon    per-visitor, keyed on a random session id. Shapes behaviour and
--           prompts sign-in. Bypassable by clearing cookies, and that is fine:
--           it is a UX device, not a cost control.
--   user    per-account.
--   global  the ceiling that actually protects the budget, because no amount of
--           cookie-clearing moves it. Applies to authenticated traffic too -
--           many well-behaved users cost as much as one abusive one.
--
-- `metric` rather than a generic request count: semantic search is one embedding
-- plus one vector query, while an agent run is several LLM turns and several
-- tool calls. A single allowance would either strangle search or leave the
-- expensive path unbounded.
CREATE TABLE IF NOT EXISTS usage_counters (
    scope    TEXT NOT NULL CHECK (scope IN ('anon', 'user', 'global')),
    scope_id TEXT NOT NULL,
    metric   TEXT NOT NULL,
    day      DATE NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, scope_id, metric, day)
);

-- Supports the cleanup below, and any future "usage over time" view.
CREATE INDEX IF NOT EXISTS idx_usage_counters_day ON usage_counters (day);

-- ---------------------------------------------------------------------------
-- collections.is_curated — system-owned demo content.
-- ---------------------------------------------------------------------------
-- Read-only for EVERY tier, signed-in users included. The rule lives in the
-- capability layer rather than in each route, so a route added later cannot
-- forget it.
--
-- These are explicitly NOT the development researcher's library. That account is
-- a local debugging shortcut and does not exist in production; this is content
-- the product ships.
ALTER TABLE collections
    ADD COLUMN IF NOT EXISTS is_curated BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_collections_curated
    ON collections (is_curated) WHERE is_curated;

-- The account that owns curated collections. A real users row, so
-- collections.user_id stays NOT NULL rather than becoming nullable for one case.
-- is_system keeps it out of any future user count or admin listing.
INSERT INTO users (email, display_name, auth_provider, is_system)
VALUES ('system@research-copilot.dev', 'Research Copilot', 'dev', true)
ON CONFLICT (email) DO UPDATE SET is_system = true;

COMMIT;

-- Verify:
--   SELECT email, is_system FROM users WHERE is_system;
--   SELECT scope, metric, day, count FROM usage_counters ORDER BY day DESC LIMIT 10;
--
-- Counters accumulate one row per (scope, scope_id, metric, day) and are never
-- read after their day passes. Old rows are harmless but pointless; prune
-- occasionally if the table grows:
--   DELETE FROM usage_counters WHERE day < CURRENT_DATE - 30;
