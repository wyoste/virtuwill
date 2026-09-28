-- VirtuWill data model · Plaid, served from the lakehouse
-- The lakehouse's Plaid tables (prod.silver.plaid_*) are synced into this
-- database as read-only tables. virtuwill/plaid_mirror.py loads what changed in
-- them into the finance tables; this row remembers how far it has read, so each
-- run loads only rows synced since the last one.

CREATE TABLE IF NOT EXISTS finance.plaid_mirror (
    id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    transactions_through TIMESTAMPTZ,       -- newest synced_at already loaded
    balances_through TIMESTAMPTZ,           -- newest as_of already loaded
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
