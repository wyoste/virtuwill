-- VirtuWill data model · distance and routes for workouts
-- A run, ride or walk can record how far it went, and carry the route it took
-- (from a GPX or TCX file, as Strava, Garmin and most running apps export).
-- The route is kept simplified, ready to draw; the original file is kept
-- privately so it can be read again later.

ALTER TABLE journal.workouts ADD COLUMN IF NOT EXISTS distance NUMERIC
    CHECK (distance IS NULL OR distance BETWEEN 0 AND 1000);
ALTER TABLE journal.workouts ADD COLUMN IF NOT EXISTS distance_unit TEXT NOT NULL DEFAULT 'mi'
    CHECK (distance_unit IN ('mi', 'km'));

CREATE TABLE IF NOT EXISTS journal.workout_routes (
    workout_id BIGINT PRIMARY KEY REFERENCES journal.workouts ON DELETE CASCADE,
    points JSONB NOT NULL CHECK (jsonb_typeof(points) = 'array'),   -- [[lat, lng], …], simplified for drawing
    distance_km NUMERIC,                                            -- measured along the full track
    elevation_gain_m NUMERIC,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    file_name TEXT NOT NULL DEFAULT '',
    file_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
