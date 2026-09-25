-- VirtuWill data model · API access for scheduled jobs
-- A job (e.g. a scheduled Claude task) pushes balances and transactions with a
-- token instead of a signed-in browser. Only a hash of each token is kept; the
-- token itself is shown once, when it's made in Settings.

CREATE TABLE IF NOT EXISTS core.api_tokens (
    token_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,                     -- what uses it: 'Claude weekly finance'
    token_hash TEXT NOT NULL UNIQUE,        -- sha256 of the token
    token_prefix TEXT NOT NULL,             -- the first characters, to tell tokens apart
    scopes TEXT[] NOT NULL CHECK (scopes <@ ARRAY['finance:write', 'finance:read']::text[] AND cardinality(scopes) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    use_count INTEGER NOT NULL DEFAULT 0,
    revoked_at TIMESTAMPTZ
);

-- Which token (or the owner, in the workspace) submitted each import.
ALTER TABLE finance.staged_imports ADD COLUMN IF NOT EXISTS submitted_by TEXT NOT NULL DEFAULT 'workspace';
