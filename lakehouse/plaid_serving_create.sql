-- Plaid serving tables · run once (jobs/plaid_lakebase_refresh.py runs it when the tables are missing)
--
-- prod.bronze.raw_plaid_* is append-only history. These two tables are what
-- the app reads, through Lakebase synced tables: keyed, one row per thing,
-- lowercase names. Triggered sync needs a primary key and the Change Data Feed
-- on its source, so both are set here, once. The daily refresh
-- (plaid_serving_refresh.sql) then MERGEs into them rather than replacing them,
-- so each sync carries only what changed.
--
-- Delta primary keys are informational (not enforced): the refresh's dedup is
-- what keeps them unique.

CREATE TABLE IF NOT EXISTS prod.silver.plaid_transactions
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS SELECT transaction_id, account_id, item_label, `date`, authorized_date, name,
          merchant_name, amount, iso_currency_code, category, pending,
          _pulled_at AS synced_at
   FROM prod.bronze.raw_plaid_transactions
   WHERE false;

ALTER TABLE prod.silver.plaid_transactions ALTER COLUMN transaction_id SET NOT NULL;
ALTER TABLE prod.silver.plaid_transactions ADD CONSTRAINT pk_plaid_txn PRIMARY KEY (transaction_id);

CREATE TABLE IF NOT EXISTS prod.silver.plaid_balances
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS SELECT account_id, item_label, institution_id, account_name, official_name, mask,
          `type`, subtype, `current`, available, limit_amt, iso_currency_code,
          _pulled_at AS as_of
   FROM prod.bronze.raw_plaid_balances
   WHERE false;

ALTER TABLE prod.silver.plaid_balances ALTER COLUMN account_id SET NOT NULL;
ALTER TABLE prod.silver.plaid_balances ALTER COLUMN as_of SET NOT NULL;
ALTER TABLE prod.silver.plaid_balances ADD CONSTRAINT pk_plaid_bal PRIMARY KEY (account_id, as_of);
