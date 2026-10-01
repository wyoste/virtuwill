-- Let the VirtuWill app read the RunKeeper synced tables (the activity log and the GPX tracks).
--
-- Run once in the Lakebase SQL editor, connected to the **virtuwill** database
-- (project virtuwill · branch production; pick the database at the top of the
-- editor), signed in as the synced tables' owner. Safe to run again, e.g. after
-- a synced table is deleted and made again. The app only reads these tables:
-- it never writes them. See docs/runkeeper-lakebase.md.
--
-- As for Plaid, the grant goes to the role that owns the app's tables
-- (journal.workouts here): no application ID to copy.

-- 1. The synced tables and the app's tables are all in this database, and who the app is.
SELECT current_database()                                            AS database,
       to_regclass('bronze.runkeeper_activity') IS NOT NULL           AS runkeeper_activity,
       to_regclass('bronze.runkeeper_gpx') IS NOT NULL                AS runkeeper_gpx,
       (SELECT tableowner FROM pg_tables
        WHERE schemaname = 'journal' AND tablename = 'workouts')      AS app_role;

-- 2. Read access for the app.
DO $$
DECLARE
    app text;
BEGIN
    SELECT tableowner INTO app FROM pg_tables WHERE schemaname = 'journal' AND tablename = 'workouts';
    IF app IS NULL THEN
        RAISE EXCEPTION 'journal.workouts is not in database %: switch the editor to the database the app is attached to',
            current_database();
    END IF;
    EXECUTE format('GRANT USAGE ON SCHEMA bronze TO %I', app);
    EXECUTE format('GRANT SELECT ON bronze.runkeeper_activity, bronze.runkeeper_gpx TO %I', app);
    RAISE NOTICE 'Granted read on bronze.runkeeper_activity and bronze.runkeeper_gpx to %', app;
END $$;

-- 3. Check: all three should be true.
SELECT r.app_role,
       has_schema_privilege(r.app_role, 'bronze', 'USAGE')                      AS schema_usage,
       has_table_privilege(r.app_role, 'bronze.runkeeper_activity', 'SELECT')   AS runkeeper_activity,
       has_table_privilege(r.app_role, 'bronze.runkeeper_gpx', 'SELECT')        AS runkeeper_gpx
FROM (SELECT tableowner AS app_role FROM pg_tables WHERE schemaname = 'journal' AND tablename = 'workouts') r;
