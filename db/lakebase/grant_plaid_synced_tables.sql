-- Let the VirtuWill app read the Plaid synced tables.
--
-- Run once in the Lakebase SQL editor, connected to the **virtuwill** database
-- (project virtuwill · branch production; pick the database at the top of the
-- editor), signed in as the synced tables' owner. Safe to run again, e.g. after
-- a synced table is deleted and made again. The app only reads these tables:
-- it never writes them. See docs/plaid-lakebase.md.
--
-- The app reads Lakebase as its service principal (App authorization on the
-- app's Authorization tab: "app-… virtuwill"), whose Postgres role is named by
-- that principal's application ID. That role made the app's finance tables, so
-- the grant goes to whoever owns finance.transactions: no ID to copy.

-- 1. The synced tables and the app's tables are both in this database, and who the app is.
SELECT current_database()                                             AS database,
       to_regclass('bronze.plaid_balance') IS NOT NULL                 AS plaid_balance,
       to_regclass('bronze.plaid_transaction') IS NOT NULL             AS plaid_transaction,
       (SELECT tableowner FROM pg_tables
        WHERE schemaname = 'finance' AND tablename = 'transactions')   AS app_role;

-- 2. Read access for the app.
DO $$
DECLARE
    app text;
BEGIN
    SELECT tableowner INTO app FROM pg_tables WHERE schemaname = 'finance' AND tablename = 'transactions';
    IF app IS NULL THEN
        RAISE EXCEPTION 'finance.transactions is not in database %: switch the editor to the database the app is attached to',
            current_database();
    END IF;
    EXECUTE format('GRANT USAGE ON SCHEMA bronze TO %I', app);
    EXECUTE format('GRANT SELECT ON bronze.plaid_balance, bronze.plaid_transaction TO %I', app);
    RAISE NOTICE 'Granted read on bronze.plaid_balance and bronze.plaid_transaction to %', app;
END $$;

-- 3. Check: all three should be true.
SELECT r.app_role,
       has_schema_privilege(r.app_role, 'bronze', 'USAGE')                          AS schema_usage,
       has_table_privilege(r.app_role, 'bronze.plaid_balance', 'SELECT')            AS balances,
       has_table_privilege(r.app_role, 'bronze.plaid_transaction', 'SELECT')        AS transactions
FROM (SELECT tableowner AS app_role FROM pg_tables WHERE schemaname = 'finance' AND tablename = 'transactions') r;
