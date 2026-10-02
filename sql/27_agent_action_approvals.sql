-- Apply before deploying Wick approvals. Checkpoints contain private conversation data.
BEGIN;
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_state_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_state_check
 CHECK (state IN ('running','awaiting_approval','stop_requested','stopped','completed','failed'));
CREATE TABLE IF NOT EXISTS agent_action_approvals (
 approval_id UUID PRIMARY KEY,
 run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
 proposal JSONB NOT NULL, job JSONB NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','claimed')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '15 minutes'
);
CREATE INDEX IF NOT EXISTS idx_action_approvals_pending ON agent_action_approvals(user_id,run_id,state);
CREATE TABLE IF NOT EXISTS agent_action_grants (
 grant_id UUID PRIMARY KEY,
 user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
 scope_key TEXT NOT NULL, label TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(user_id,scope_key)
);
COMMIT;
