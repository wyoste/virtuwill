-- Plaid serving tables · daily refresh, after the Plaid pull lands in bronze
--
-- Transactions: the current state of each transaction_id. Bronze keeps every
-- sync (_sync_op = added / modified / removed). The latest one wins, a removed
-- transaction is deleted, and a row is only rewritten when bronze has a newer
-- pull of it, so the synced table sees real changes only.

MERGE INTO prod.silver.plaid_transactions AS t
USING (
  SELECT * FROM prod.bronze.raw_plaid_transactions
  QUALIFY ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY _pulled_at DESC) = 1
) AS s
ON t.transaction_id = s.transaction_id
WHEN MATCHED AND s._sync_op = 'removed' THEN DELETE
WHEN MATCHED AND s._pulled_at > t.synced_at THEN UPDATE SET
  account_id = s.account_id, item_label = s.item_label, `date` = s.`date`, authorized_date = s.authorized_date,
  name = s.name, merchant_name = s.merchant_name, amount = s.amount, iso_currency_code = s.iso_currency_code,
  category = s.category, pending = s.pending, synced_at = s._pulled_at
WHEN NOT MATCHED AND s._sync_op <> 'removed' THEN INSERT
  (transaction_id, account_id, item_label, `date`, authorized_date, name, merchant_name, amount,
   iso_currency_code, category, pending, synced_at)
  VALUES (s.transaction_id, s.account_id, s.item_label, s.`date`, s.authorized_date, s.name, s.merchant_name, s.amount,
          s.iso_currency_code, s.category, s.pending, s._pulled_at);

-- Balances: every pull, kept for history. A pull already served is left alone.

MERGE INTO prod.silver.plaid_balances AS t
USING (
  SELECT * FROM prod.bronze.raw_plaid_balances
  WHERE account_id IS NOT NULL AND _pulled_at IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY account_id, _pulled_at ORDER BY _pulled_at) = 1
) AS s
ON t.account_id = s.account_id AND t.as_of = s._pulled_at
WHEN NOT MATCHED THEN INSERT
  (account_id, item_label, institution_id, account_name, official_name, mask, `type`, subtype,
   `current`, available, limit_amt, iso_currency_code, as_of)
  VALUES (s.account_id, s.item_label, s.institution_id, s.account_name, s.official_name, s.mask, s.`type`, s.subtype,
          s.`current`, s.available, s.limit_amt, s.iso_currency_code, s._pulled_at);
