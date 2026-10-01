-- VirtuWill data model · RunKeeper, loaded from the lakehouse
-- prod.bronze.raw_runkeeper_activities (landed by jobs/runkeeper_to_bronze.py) is
-- mirrored into this database by a Lakebase synced table; virtuwill/runkeeper_synced.py
-- loads it into journal.workouts (source 'runkeeper', source_ref = RunKeeper's
-- Activity Id) and journal.workout_routes. This row remembers how far it has read:
-- the last (_ingested_at, activity_id) loaded, since one run of the job stamps
-- every row it writes with the same time.

ALTER TABLE journal.workouts DROP CONSTRAINT IF EXISTS workouts_source_check;
ALTER TABLE journal.workouts ADD CONSTRAINT workouts_source_check
    CHECK (source IN ('journal', 'health_tracker', 'manual', 'runkeeper'));
CREATE UNIQUE INDEX IF NOT EXISTS workouts_by_runkeeper_ref ON journal.workouts (source_ref) WHERE source = 'runkeeper';

CREATE TABLE IF NOT EXISTS journal.runkeeper_bronze_load (
    id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    through_at TIMESTAMPTZ,
    through_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
