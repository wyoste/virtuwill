-- VirtuWill data model · one account, however many ways sources name it
-- A source can name an account by text ("Household · 1234", a CSV's account
-- column) or by its last four digits (a statement, Plaid). When two records turn
-- out to be one account (an import without digits, then Plaid with them), they
-- are merged: everything moves to the kept record, and the merged record's names
-- and digits become aliases of it, so later loads land on it. The mapping lives
-- here, in the database, never in code.

-- Aliases now say what kind of name they are, and where they came from.
ALTER TABLE finance.account_aliases ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'name'
    CHECK (kind IN ('name', 'mask'));
ALTER TABLE finance.account_aliases ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'import'
    CHECK (origin IN ('import', 'merge', 'manual'));
ALTER TABLE finance.account_aliases ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE finance.account_aliases DROP CONSTRAINT IF EXISTS account_aliases_pkey;
ALTER TABLE finance.account_aliases ADD PRIMARY KEY (kind, alias);
ALTER TABLE finance.account_aliases DROP CONSTRAINT IF EXISTS account_aliases_mask_digits;
ALTER TABLE finance.account_aliases ADD CONSTRAINT account_aliases_mask_digits CHECK (kind <> 'mask' OR alias ~ '^[0-9]{4}$');

-- Every merge, for the record: what was merged into what, and why.
CREATE TABLE IF NOT EXISTS finance.account_merges (
    merge_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merged_account_id TEXT NOT NULL,            -- the record that no longer exists
    merged_name TEXT NOT NULL,
    merged_mask TEXT,
    into_account_id TEXT NOT NULL REFERENCES finance.accounts ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT '',
    merged_by TEXT NOT NULL CHECK (merged_by IN ('cleanup', 'owner')),
    rows_moved JSONB NOT NULL DEFAULT '{}',      -- table → rows moved, for the record
    merged_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
