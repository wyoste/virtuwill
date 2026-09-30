-- VirtuWill data model · Plaid, loaded from the lakehouse
-- The Plaid pulls in prod.bronze are mirrored into this database by Lakebase
-- synced tables; virtuwill/plaid_synced.py loads what lands in them into the
-- finance tables. This row remembers how far it has read (by Auto Loader's
-- _ingested_at), so each load takes only what landed since the last one.

CREATE TABLE IF NOT EXISTS finance.plaid_bronze_load (
    id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    balances_through TIMESTAMPTZ,
    transactions_through TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
