-- =============================================================================
-- 22_user_sessions.sql — one row per signed-in browser
-- =============================================================================
-- Sessions are Flask signed cookies: entirely client-side, and until now the
-- server kept no record that any of them existed. That is fine for knowing WHO
-- is asking, and useless for two things people expect of an account:
--
--   "which devices am I signed in on?"     nothing to list
--   "sign me out everywhere else"          nothing to revoke
--
-- A signed cookie cannot be withdrawn. It is valid until it expires, wherever it
-- is, including on a laptop somebody left on a train. This table is the server
-- side of the session: the cookie carries a random token, the row decides whether
-- that token still counts, and revoking is an UPDATE.
--
-- **The token is stored hashed.** The cookie is signed, not encrypted, so its
-- contents are readable by whoever holds it — but a dump of this table should not
-- hand anybody a working session. SHA-256 is right here where it would be wrong
-- for a password: the input is 32 bytes of `secrets` output, not something
-- guessable, so there is nothing for a brute-force to be slowed down against.
--
-- `ON DELETE CASCADE` because a deleted account's sessions are meaningless, and
-- leaving them would keep a revocable handle on a user row that no longer exists.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_sessions (
    session_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- SHA-256 hex of the token in the cookie. Unique because the token is the
    -- lookup key on every request, and two rows sharing one would make "which
    -- session is this?" ambiguous at exactly the wrong moment.
    session_hash TEXT NOT NULL UNIQUE,

    -- What to show in the device list. Parsed for display only; nothing is
    -- decided on the strength of a user agent, which is a string a client
    -- chooses and can therefore lie about.
    user_agent   TEXT,
    ip_address   TEXT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- NULL means live. A timestamp rather than a boolean: "signed out at 14:02"
    -- answers a question somebody asks after losing a laptop, and `true` does not.
    revoked_at   TIMESTAMPTZ
);

-- The device list: newest activity first, one user at a time.
CREATE INDEX IF NOT EXISTS idx_user_sessions_user
    ON user_sessions (user_id, last_seen_at DESC);

-- Partial index for the per-request lookup, which only ever asks about live
-- sessions. Revoked rows are kept for the audit answer above but never matched.
CREATE INDEX IF NOT EXISTS idx_user_sessions_live
    ON user_sessions (session_hash) WHERE revoked_at IS NULL;

-- =============================================================================
-- Housekeeping
-- =============================================================================
-- Rows outlive the cookies they track: the cookie expires on its own after
-- SESSION_LIFETIME_DAYS, and nothing here notices. Sweep periodically so the
-- device list does not accumulate browsers nobody has used since March.
--
-- DELETE FROM user_sessions
--  WHERE last_seen_at < now() - interval '90 days';

-- =============================================================================
-- Verification
-- =============================================================================
-- SELECT u.email, s.user_agent, s.ip_address, s.last_seen_at, s.revoked_at
--   FROM user_sessions s JOIN users u USING (user_id)
--  ORDER BY s.last_seen_at DESC;
--
-- Sign in from two browsers, revoke one from Settings, and expect the revoked
-- row to keep its history rather than disappear:
--
-- SELECT count(*) FILTER (WHERE revoked_at IS NULL)  AS live,
--        count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked
--   FROM user_sessions WHERE user_id = '<your-user-id>';
