-- Plaid serving tables · daily refresh, after the notebook's Auto Loader lands the pull in bronze
--
-- Transactions: the current state of each transaction_id. Bronze keeps every
-- sync (_sync_op = added / modified / removed). The latest one wins, a removed
-- transaction is deleted, and a row is only rewritten when bronze has a newer
-- pull of it, so the synced table sees real changes only.

MERGE INTO prod.silver.plaid_transactions AS t
USING (
  SELECT transaction_id, account_id, item_label,
         TRY_CAST(NULLIF(`date`, 'None') AS DATE)            AS `date`,
         TRY_CAST(NULLIF(authorized_date, 'None') AS DATE)   AS authorized_date,
         name, merchant_name, CAST(amount AS DECIMAL(14,2))  AS amount,
         iso_currency_code, category, pending, _sync_op,
         CAST(_pulled_at AS TIMESTAMP)                       AS synced_at
  FROM prod.bronze.raw_plaid_transactions
  WHERE transaction_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY transaction_id
                             ORDER BY CAST(_pulled_at AS TIMESTAMP) DESC, _ingested_at DESC) = 1
) AS s
ON t.transaction_id = s.transaction_id
WHEN MATCHED AND s._sync_op = 'removed' THEN DELETE
WHEN MATCHED AND s.synced_at > t.synced_at THEN UPDATE SET
  account_id = s.account_id, item_label = s.item_label, `date` = s.`date`, authorized_date = s.authorized_date,
  name = s.name, merchant_name = s.merchant_name, amount = s.amount, iso_currency_code = s.iso_currency_code,
  category = s.category, pending = s.pending, synced_at = s.synced_at
WHEN NOT MATCHED AND s._sync_op <> 'removed' THEN INSERT
  (transaction_id, account_id, item_label, `date`, authorized_date, name, merchant_name, amount,
   iso_currency_code, category, pending, synced_at)
  VALUES (s.transaction_id, s.account_id, s.item_label, s.`date`, s.authorized_date, s.name, s.merchant_name, s.amount,
          s.iso_currency_code, s.category, s.pending, s.synced_at);

-- Balances: every pull, kept for history. A pull already served is left alone.

MERGE INTO prod.silver.plaid_balances AS t
USING (
  SELECT account_id, item_label, institution_id, account_name, official_name, mask,
         NULLIF(`type`, 'None') AS `type`, NULLIF(subtype, 'None') AS subtype,
         CAST(`current` AS DECIMAL(14,2)) AS `current`, CAST(available AS DECIMAL(14,2)) AS available,
         CAST(limit_amt AS DECIMAL(14,2)) AS limit_amt, iso_currency_code,
         CAST(_pulled_at AS TIMESTAMP) AS as_of
  FROM prod.bronze.raw_plaid_balances
  WHERE account_id IS NOT NULL AND _pulled_at IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY account_id, CAST(_pulled_at AS TIMESTAMP) ORDER BY _ingested_at DESC) = 1
) AS s
ON t.account_id = s.account_id AND t.as_of = s.as_of
WHEN NOT MATCHED THEN INSERT *;
