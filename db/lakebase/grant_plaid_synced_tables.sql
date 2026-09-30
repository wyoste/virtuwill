-- Let the VirtuWill app read the Plaid synced tables.
--
-- Run once in the Lakebase SQL editor, connected to the **virtuwill** database
-- (project virtuwill · branch production · database virtuwill; pick it with the
-- database button at the top right of the editor), signed in as the synced
-- tables' owner. Safe to run again, e.g. after a synced table is deleted and
-- made again. The app only reads these tables: it never writes them.
-- See docs/plaid-lakebase.md.
--
-- ba4a0a98-addf-4df3-ba28-be282ae95155 is the app's service principal (its
-- client ID, on the app's Authorization tab, and the app's PGUSER).

-- 1. Connected to the right database: it holds both the synced tables and the
--    app's own finance tables (every row should say true). A false here means
--    the editor is on another database, or the app is attached to another one.
SELECT current_database() AS database, t AS object, to_regclass(t) IS NOT NULL AS present
FROM unnest(ARRAY['bronze.plaid_balance', 'bronze.plaid_transaction', 'finance.transactions']) AS t;

-- 2. Read access for the app.
GRANT USAGE  ON SCHEMA bronze TO "ba4a0a98-addf-4df3-ba28-be282ae95155";
GRANT SELECT ON bronze.plaid_balance, bronze.plaid_transaction TO "ba4a0a98-addf-4df3-ba28-be282ae95155";

-- 3. Check: SELECT on both tables, and the schema is usable.
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'bronze' AND grantee = 'ba4a0a98-addf-4df3-ba28-be282ae95155'
ORDER BY table_name;

SELECT has_schema_privilege('ba4a0a98-addf-4df3-ba28-be282ae95155', 'bronze', 'USAGE') AS schema_usage,
       has_table_privilege('ba4a0a98-addf-4df3-ba28-be282ae95155', 'bronze.plaid_balance', 'SELECT') AS balances,
       has_table_privilege('ba4a0a98-addf-4df3-ba28-be282ae95155', 'bronze.plaid_transaction', 'SELECT') AS transactions;
