-- Store explicit context references only; resolve current content per turn.
-- NULL represents an older conversation with no context configuration.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS wick_context JSONB;
