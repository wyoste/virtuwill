-- VirtuWill data model · music
-- Songs (the composition) are separate from recordings (the audio), so one song
-- can have several demos or masters and a recording can sit on an album.

CREATE SCHEMA IF NOT EXISTS music;

CREATE TABLE IF NOT EXISTS music.albums (
    album_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT 'Will Yoste',
    release_year SMALLINT,
    genre TEXT NOT NULL DEFAULT '',
    art_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    published BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS music.songs (
    song_id TEXT PRIMARY KEY,               -- e.g. 't1776835806482'
    title TEXT NOT NULL,
    year_written SMALLINT,
    written_at TEXT NOT NULL DEFAULT '',    -- place, e.g. 'Oxford, MS'
    story TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    musical_key TEXT NOT NULL DEFAULT '',
    bpm NUMERIC CHECK (bpm IS NULL OR bpm > 0),
    published BOOLEAN NOT NULL DEFAULT false,
    position INTEGER,                       -- the owner's order in the catalog
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS music.song_sections (
    song_id TEXT NOT NULL REFERENCES music.songs ON DELETE CASCADE,
    position INTEGER NOT NULL,
    section_type TEXT NOT NULL CHECK (section_type IN
        ('intro', 'verse', 'pre-chorus', 'chorus', 'bridge', 'solo', 'outro', 'coda', 'full')),
    label TEXT NOT NULL DEFAULT '',
    chords TEXT NOT NULL DEFAULT '',
    lyrics TEXT NOT NULL DEFAULT '',
    tabs TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (song_id, position)
);

CREATE TABLE IF NOT EXISTS music.recordings (
    recording_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    song_id TEXT REFERENCES music.songs ON DELETE SET NULL,
    album_id TEXT REFERENCES music.albums ON DELETE SET NULL,
    track_number SMALLINT,
    title TEXT NOT NULL,
    version_label TEXT NOT NULL DEFAULT '', -- e.g. '2025 version', 'demo'
    audio_asset_id BIGINT NOT NULL REFERENCES core.media_assets,
    art_asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    duration_seconds NUMERIC,
    recorded_on DATE REFERENCES core.calendar (day),
    published BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (album_id, track_number)
);

CREATE TABLE IF NOT EXISTS music.gallery_photos (
    asset_id BIGINT PRIMARY KEY REFERENCES core.media_assets ON DELETE CASCADE,
    caption TEXT NOT NULL DEFAULT '',
    position INTEGER
);

DROP VIEW IF EXISTS music.public_catalog CASCADE;

-- What visitors may see: published songs, and only their published recordings.
CREATE VIEW music.public_catalog AS
SELECT s.song_id, s.title, s.year_written, s.written_at, s.story, s.genre, s.musical_key, s.bpm,
       r.recording_id, r.version_label, a.title AS album, r.track_number, m.path AS audio_path, r.duration_seconds
FROM music.songs s
LEFT JOIN music.recordings r ON r.song_id = s.song_id AND r.published
LEFT JOIN music.albums a ON a.album_id = r.album_id
LEFT JOIN core.media_assets m ON m.asset_id = r.audio_asset_id
WHERE s.published;
