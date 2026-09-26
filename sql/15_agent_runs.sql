-- Shared, owner-scoped run coordination across Render workers.
-- Apply after 12_conversations.sql before deploying Stop controls.
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id UUID PRIMARY KEY,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('user', 'anon')),
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'running'
        CHECK (state IN ('running', 'stop_requested', 'stopped', 'completed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_owner
    ON agent_runs (owner_kind, owner_id, created_at DESC);
