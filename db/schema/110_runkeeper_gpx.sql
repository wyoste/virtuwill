-- VirtuWill data model · RunKeeper tracks, loaded from their own table
-- The GPX tracks land in prod.bronze.raw_runkeeper_gpx (the runkeeper_gpx_to_bronze
-- notebook), apart from the activity log, and reach this database as the synced
-- table bronze.runkeeper_gpx. virtuwill/runkeeper_synced.py reads it with a cursor
-- of its own, so a track that lands after its activity still finds its workout.

ALTER TABLE journal.runkeeper_bronze_load ADD COLUMN IF NOT EXISTS gpx_through_at TIMESTAMPTZ;
ALTER TABLE journal.runkeeper_bronze_load ADD COLUMN IF NOT EXISTS gpx_through_id TEXT;
