-- Plaid serving tables · run once (jobs/plaid_lakebase_refresh.py runs it when the tables are missing)
--
-- prod.bronze.raw_plaid_* is what the ingestion_plaid_financials notebook lands:
-- append-only, every column as the API gave it (timestamps and dates as text,
-- a missing date as the text 'None'). These two tables are what the app reads,
-- through Lakebase synced tables: keyed, typed, one row per thing, lowercase
-- names. Triggered sync needs a primary key and the Change Data Feed on its
-- source, so both are set here, once. The daily refresh
-- (plaid_serving_refresh.sql) then MERGEs into them rather than replacing them,
-- so each sync carries only what changed.
--
-- Delta primary keys are informational (not enforced): the refresh's dedup is
-- what keeps them unique.

CREATE TABLE IF NOT EXISTS prod.silver.plaid_transactions (
  transaction_id    STRING NOT NULL,
  account_id        STRING,
  item_label        STRING,          -- the institution: USAA, Chase, Fidelity, Amex
  `date`            DATE,            -- posted
  authorized_date   DATE,
  name              STRING,
  merchant_name     STRING,
  amount            DECIMAL(14,2),   -- positive = money out
  iso_currency_code STRING,
  category          STRING,          -- Plaid's detailed category code, or its older 'A > B' path
  pending           BOOLEAN,
  synced_at         TIMESTAMP,       -- the pull this row came from
  CONSTRAINT pk_plaid_txn PRIMARY KEY (transaction_id)
) TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS prod.silver.plaid_balances (
  account_id        STRING NOT NULL,
  item_label        STRING,
  institution_id    STRING,
  account_name      STRING,
  official_name     STRING,
  mask              STRING,
  `type`            STRING,
  subtype           STRING,
  `current`         DECIMAL(14,2),   -- for a card or loan, what's owed
  available         DECIMAL(14,2),
  limit_amt         DECIMAL(14,2),
  iso_currency_code STRING,
  as_of             TIMESTAMP NOT NULL,
  CONSTRAINT pk_plaid_bal PRIMARY KEY (account_id, as_of)
) TBLPROPERTIES (delta.enableChangeDataFeed = true);
