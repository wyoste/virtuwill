-- VirtuWill data model · workspace and public site (information architecture v2)
-- Song pages, habits that tick themselves from logged activity, and album
-- visibility for the public Music page.

-- ── Music: every song has a page at /music/<slug> ───────────────────────────
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS slug TEXT;
WITH named AS (
    SELECT song_id,
           COALESCE(NULLIF(trim(BOTH '-' FROM regexp_replace(lower(title), '[^a-z0-9]+', '-', 'g')), ''), 'song') AS base
    FROM music.songs WHERE slug IS NULL
), numbered AS (
    SELECT song_id, base, ROW_NUMBER() OVER (PARTITION BY base ORDER BY song_id) AS n FROM named
)
UPDATE music.songs s SET slug = CASE WHEN n.n = 1 THEN n.base ELSE n.base || '-' || n.n END
FROM numbered n WHERE n.song_id = s.song_id;
CREATE UNIQUE INDEX IF NOT EXISTS songs_by_slug ON music.songs (slug);

-- Albums that already had public recordings stay public.
UPDATE music.albums a SET published = true
WHERE EXISTS (SELECT 1 FROM music.recordings r WHERE r.album_id = a.album_id AND r.published);

-- What visitors may see: published songs with their published recordings on
-- published albums (or singles). Unpublished albums never leak a title.
DROP VIEW IF EXISTS music.public_catalog CASCADE;
CREATE VIEW music.public_catalog AS
SELECT s.song_id, s.slug, s.title, s.year_written, s.written_at, s.story, s.genre, s.musical_key, s.bpm,
       r.recording_id, r.version_label, a.title AS album, r.track_number, m.path AS audio_path, r.duration_seconds
FROM music.songs s
LEFT JOIN music.recordings r ON r.song_id = s.song_id AND r.published
     AND (r.album_id IS NULL OR EXISTS (SELECT 1 FROM music.albums pa WHERE pa.album_id = r.album_id AND pa.published))
LEFT JOIN music.albums a ON a.album_id = r.album_id
LEFT JOIN core.media_assets m ON m.asset_id = r.audio_asset_id
WHERE s.published;

-- ── Habits: some tick themselves from logged activity ───────────────────────
-- A derived habit is done when the day has the matching record. Anything the
-- owner sets by hand (a journal.habit_logs row) wins over the derivation.
ALTER TABLE journal.habits ADD COLUMN IF NOT EXISTS derived_from TEXT
    CHECK (derived_from IS NULL OR derived_from IN ('strength_workout', 'run_workout', 'alcohol'));
UPDATE journal.habits SET derived_from = 'strength_workout' WHERE habit = 'lift';
UPDATE journal.habits SET derived_from = 'run_workout' WHERE habit = 'run';
UPDATE journal.habits SET derived_from = 'alcohol' WHERE habit = 'drink';

CREATE VIEW journal.derived_habits AS
SELECT c.day, h.habit,
       CASE h.derived_from
           WHEN 'strength_workout' THEN EXISTS (SELECT 1 FROM journal.workouts w
                                                WHERE w.workout_date = c.day AND w.workout_type = 'Strength')
           WHEN 'run_workout' THEN EXISTS (SELECT 1 FROM journal.workouts w
                                           WHERE w.workout_date = c.day
                                             AND (w.activity ILIKE '%run%' OR w.note ILIKE '%run%'))
           WHEN 'alcohol' THEN EXISTS (SELECT 1 FROM health.alcohol a WHERE a.drink_date = c.day AND a.containers > 0)
       END AS done
FROM core.calendar c CROSS JOIN journal.habits h
WHERE h.derived_from IS NOT NULL;

-- Each day's habits: what the owner set, else what the day's records show.
CREATE VIEW journal.day_habits AS
SELECT c.day, h.habit, h.label, h.polarity, h.derived_from,
       COALESCE(l.done, d.done, false) AS done,
       CASE WHEN l.habit IS NOT NULL THEN 'manual' WHEN d.done THEN 'derived' ELSE 'none' END AS origin
FROM core.calendar c
CROSS JOIN journal.habits h
LEFT JOIN journal.habit_logs l ON l.entry_date = c.day AND l.habit = h.habit
LEFT JOIN journal.derived_habits d ON d.day = c.day AND d.habit = h.habit;

-- ── Health: the native screens own every record ─────────────────────────────
-- The embedded Health tracker is retired; its rows are edited like any other.
