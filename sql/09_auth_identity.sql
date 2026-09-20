-- =============================================================================
-- 09_auth_identity.sql — Phase 3.1: the dashboard authenticates its own users
-- =============================================================================
-- Run ONCE against an existing database. Additive; deletes nothing.
--
-- The dashboard moved from Databricks Apps to Render. Databricks sat behind an
-- OAuth proxy that injected the user's email as a request header, so `users`
-- needed nothing beyond an email. Render has no such proxy: the application now
-- runs Google OAuth itself, and needs somewhere to record which external identity
-- an account belongs to.
--
-- Scope note: user_profiles and user_research_interests belong to onboarding
-- (3.7) and usage_counters to the capability layer (3.2). Each migration lands
-- with the code that uses it, so a half-finished phase never leaves tables that
-- nothing reads.
-- =============================================================================

BEGIN;

ALTER TABLE users
    -- Which external identity provider vouched for this account.
    -- 'dev' is the local development identity; it never exists in production,
    -- because config.py refuses to boot with the bypass enabled there.
    ADD COLUMN IF NOT EXISTS auth_provider    TEXT NOT NULL DEFAULT 'dev',

    -- The provider's own immutable id for the user - Google's `sub`.
    -- THIS, not email, is the identity key. A Google account's email address can
    -- be changed or reassigned; `sub` cannot. Matching an account by email alone
    -- would mean that whoever holds an address today inherits the account of
    -- whoever held it before.
    ADD COLUMN IF NOT EXISTS provider_subject TEXT,

    ADD COLUMN IF NOT EXISTS avatar_url       TEXT,

    -- Marks the account that owns curated demo content (3.2). Keeping curated
    -- collections under a real users row preserves the NOT NULL foreign key on
    -- collections.user_id rather than making it nullable for one special case.
    ADD COLUMN IF NOT EXISTS is_system        BOOLEAN NOT NULL DEFAULT false,

    ADD COLUMN IF NOT EXISTS last_login_at    TIMESTAMPTZ;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_auth_provider_check;
ALTER TABLE users ADD CONSTRAINT users_auth_provider_check
    CHECK (auth_provider IN ('dev', 'google'));
-- Extended, not replaced, when a second provider is added. Email/password is
-- deferred; adding 'password' here plus a password_hash column is the whole
-- change, which is why no provider abstraction exists yet.

-- Partial, because rows created before OAuth have no subject and several NULLs
-- must not collide. Unique on the pair rather than on subject alone, so two
-- providers could one day issue the same opaque id without conflicting.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider_subject
    ON users (auth_provider, provider_subject)
    WHERE provider_subject IS NOT NULL;

COMMIT;

-- Verify:
--   \d users
--   SELECT auth_provider, count(*) FROM users GROUP BY 1;
--   -- existing rows report 'dev' with a NULL provider_subject; the first Google
--   -- sign-in with a matching email links that row rather than duplicating it.
