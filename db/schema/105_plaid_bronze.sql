-- VirtuWill data model · Plaid, loaded from the lakehouse
-- jobs/plaid_bronze_to_lakebase.py (a Databricks job) reads the Plaid pulls in
-- prod.bronze and loads them into the finance tables. This row remembers how far
-- it has read bronze (by Auto Loader's _ingested_at), so each run loads only
-- what landed since the last one.

CREATE TABLE IF NOT EXISTS finance.plaid_bronze_load (
    id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    balances_through TIMESTAMPTZ,
    transactions_through TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
