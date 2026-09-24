-- VirtuWill data model · core
-- Shared dimensions and application plumbing used by every domain schema.
-- Files in db/schema run in name order; each is idempotent.

SELECT pg_advisory_xact_lock(hashtext('virtuwill_schema'));

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS virtuwill;

-- ── Calendar: the conformed date dimension ──────────────────────────────────
-- One row per calendar day, 1900–2100, so historical records (old recordings,
-- statements) are never rejected. Every dated fact in every schema joins here.
-- Days are local dates in the owner's timezone (APP_TIMEZONE).
CREATE TABLE IF NOT EXISTS core.calendar (
    day DATE PRIMARY KEY,
    iso_weekday SMALLINT NOT NULL,          -- 1 = Monday … 7 = Sunday
    day_name TEXT NOT NULL,
    is_weekend BOOLEAN NOT NULL,            -- Saturday or Sunday
    week_start DATE NOT NULL,               -- Monday of the ISO week
    iso_year SMALLINT NOT NULL,
    iso_week SMALLINT NOT NULL,
    month_start DATE NOT NULL,
    month SMALLINT NOT NULL,
    quarter SMALLINT NOT NULL,
    year SMALLINT NOT NULL,
    day_of_year SMALLINT NOT NULL
);
INSERT INTO core.calendar
SELECT d::date,
       EXTRACT(ISODOW FROM d)::smallint,
       trim(to_char(d, 'Day')),
       EXTRACT(ISODOW FROM d) >= 6,
       date_trunc('week', d)::date,
       EXTRACT(ISOYEAR FROM d)::smallint,
       EXTRACT(WEEK FROM d)::smallint,
       date_trunc('month', d)::date,
       EXTRACT(MONTH FROM d)::smallint,
       EXTRACT(QUARTER FROM d)::smallint,
       EXTRACT(YEAR FROM d)::smallint,
       EXTRACT(DOY FROM d)::smallint
FROM generate_series(DATE '1900-01-01', DATE '2100-12-31', INTERVAL '1 day') AS d
ON CONFLICT (day) DO NOTHING;

-- ── Media assets ─────────────────────────────────────────────────────────────
-- Every uploaded or bundled file: audio, photos, thumbnails, portfolio HTML.
-- Domain tables reference asset_id; bytes live here so redeploys lose nothing.
CREATE TABLE IF NOT EXISTS core.media_assets (
    asset_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,              -- served at /static/<path>
    content_type TEXT NOT NULL,
    content BYTEA,                          -- NULL for files shipped in the repository
    byte_size BIGINT,
    sha256 TEXT,
    visibility TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Site settings ────────────────────────────────────────────────────────────
-- Owner-controlled switches, e.g. whether the chat widget is shown.
CREATE TABLE IF NOT EXISTS core.settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO core.settings (key, value) VALUES ('site.chat_enabled', 'false')
ON CONFLICT (key) DO NOTHING;

-- ── Application plumbing ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS virtuwill.migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The embedded Finance and Health trackers keep their own document as the
-- editing format; each save is projected into the relational schemas.
CREATE TABLE IF NOT EXISTS virtuwill.trackers (
    kind TEXT PRIMARY KEY CHECK (kind IN ('finance', 'health')),
    document TEXT NOT NULL,
    state TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outcome of the latest projection per source: counts, skipped records, fields seen.
CREATE TABLE IF NOT EXISTS virtuwill.sync_reports (
    source TEXT PRIMARY KEY,
    report JSONB NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
