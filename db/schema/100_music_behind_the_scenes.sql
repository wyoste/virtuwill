-- VirtuWill data model · music, behind the scenes
-- Each song page is somewhere to learn about the song, not only to play it:
-- what it means, how it came to be, how to play it (key, capo, tuning,
-- strumming, chords), notes on particular lyric lines, and what changed
-- between recorded versions.

ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS meaning TEXT NOT NULL DEFAULT '';            -- what it's about
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS themes TEXT[] NOT NULL DEFAULT '{}';         -- 'love', 'leaving home'
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS capo SMALLINT CHECK (capo IS NULL OR capo BETWEEN 0 AND 12);
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS tuning TEXT NOT NULL DEFAULT '';              -- '' = standard
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS time_signature TEXT NOT NULL DEFAULT '';      -- '4/4', '6/8'
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS strumming TEXT NOT NULL DEFAULT '';           -- 'D DU UDU'
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS influences TEXT NOT NULL DEFAULT '';          -- sounds and songs it drew on

-- What's different about each recorded version.
ALTER TABLE music.recordings ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT '';

-- Notes on particular lyric lines. A note is tied to a line by its text within
-- a section, so reordering sections keeps it; a note whose line was rewritten
-- still shows, under the song's other notes.
CREATE TABLE IF NOT EXISTS music.song_notes (
    note_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    song_id TEXT NOT NULL REFERENCES music.songs ON DELETE CASCADE,
    line_text TEXT NOT NULL,                -- the lyric line (or phrase) the note is about
    note TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    CHECK (length(line_text) > 0 AND length(note) > 0)
);
CREATE INDEX IF NOT EXISTS song_notes_by_song ON music.song_notes (song_id, position);
