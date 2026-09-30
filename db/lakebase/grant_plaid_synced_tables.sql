-- Let the VirtuWill app read the Plaid synced tables.
--
-- Run once in the Lakebase SQL editor (database databricks_postgres), signed in
-- as the synced tables' owner. Safe to run again, e.g. after a synced table is
-- deleted and made again. The app only reads these tables: it never writes
-- them. See docs/plaid-lakebase.md.
--
-- ba4a0a98-addf-4df3-ba28-be282ae95155 is the app's service principal (its
-- client ID, on the app's Authorization tab, and the app's PGUSER).

-- 1. The synced tables are where the app expects them (both rows should say true).
SELECT t AS synced_table, to_regclass(t) IS NOT NULL AS present
FROM unnest(ARRAY['bronze.raw_plaid_balances', 'bronze.raw_plaid_transactions']) AS t;

-- 2. Read access for the app.
GRANT USAGE  ON SCHEMA bronze TO "ba4a0a98-addf-4df3-ba28-be282ae95155";
GRANT SELECT ON bronze.raw_plaid_balances, bronze.raw_plaid_transactions TO "ba4a0a98-addf-4df3-ba28-be282ae95155";

-- 3. Check: SELECT on both tables, and the schema is usable.
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'bronze' AND grantee = 'ba4a0a98-addf-4df3-ba28-be282ae95155'
ORDER BY table_name;

SELECT has_schema_privilege('ba4a0a98-addf-4df3-ba28-be282ae95155', 'bronze', 'USAGE') AS schema_usage,
       has_table_privilege('ba4a0a98-addf-4df3-ba28-be282ae95155', 'bronze.raw_plaid_balances', 'SELECT') AS balances,
       has_table_privilege('ba4a0a98-addf-4df3-ba28-be282ae95155', 'bronze.raw_plaid_transactions', 'SELECT') AS transactions;
