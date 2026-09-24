-- VirtuWill data model · journal
-- The daily record: one entry per calendar date, with its tags, habits and
-- meals. Workouts also live here because the journal and the Health tracker
-- both record them; the health schema's views read them.

CREATE SCHEMA IF NOT EXISTS journal;

CREATE TABLE IF NOT EXISTS journal.entries (
    entry_date DATE PRIMARY KEY REFERENCES core.calendar (day),
    entry_id TEXT NOT NULL UNIQUE,          -- id the journal page uses
    quote TEXT NOT NULL DEFAULT '',
    quote_author TEXT NOT NULL DEFAULT '',
    free_write TEXT NOT NULL DEFAULT '',    -- rich text (HTML)
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'photo')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS journal.entry_tags (
    entry_date DATE NOT NULL REFERENCES journal.entries ON UPDATE CASCADE ON DELETE CASCADE,
    tag TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (entry_date, tag)
);

CREATE TABLE IF NOT EXISTS journal.habits (
    habit TEXT PRIMARY KEY,                 -- run, lift, read, guitar, drink, smoke, sexual
    label TEXT NOT NULL,
    polarity TEXT NOT NULL DEFAULT 'build' CHECK (polarity IN ('build', 'limit'))
);
INSERT INTO journal.habits (habit, label, polarity) VALUES
    ('run', 'Run', 'build'), ('lift', 'Lift', 'build'), ('read', 'Read', 'build'),
    ('guitar', 'Guitar', 'build'), ('drink', 'Drink', 'limit'), ('smoke', 'Smoke', 'limit'),
    ('sexual', 'Sexual activity', 'build')
ON CONFLICT (habit) DO NOTHING;

CREATE TABLE IF NOT EXISTS journal.habit_logs (
    entry_date DATE NOT NULL REFERENCES journal.entries ON UPDATE CASCADE ON DELETE CASCADE,
    habit TEXT NOT NULL REFERENCES journal.habits,
    done BOOLEAN NOT NULL,
    PRIMARY KEY (entry_date, habit)
);

-- Meals by date and slot, from the journal page or the Health tracker.
CREATE TABLE IF NOT EXISTS journal.meals (
    meal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meal_date DATE NOT NULL REFERENCES core.calendar (day),
    slot TEXT NOT NULL CHECK (slot IN ('breakfast', 'lunch', 'dinner', 'snack', 'meal')),
    status TEXT NOT NULL DEFAULT 'eaten' CHECK (status IN ('eaten', 'planned')),
    description TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    food_id TEXT,                           -- health.foods, when the meal is one reference food
    quantity NUMERIC,
    calories NUMERIC,
    protein_g NUMERIC,
    carbs_g NUMERIC,
    fat_g NUMERIC,
    fiber_g NUMERIC,
    source TEXT NOT NULL CHECK (source IN ('journal', 'health_tracker', 'manual')),
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meals_by_date ON journal.meals (meal_date);
CREATE INDEX IF NOT EXISTS meals_by_source ON journal.meals (source, meal_date);
-- A tracker record keeps its row (and id) across saves.
CREATE UNIQUE INDEX IF NOT EXISTS meals_by_tracker_ref ON journal.meals (source_ref) WHERE source = 'health_tracker';

CREATE TABLE IF NOT EXISTS journal.workout_types (
    workout_type TEXT PRIMARY KEY,
    counts_toward_goal BOOLEAN NOT NULL     -- dog walks are tracked but never qualify a day
);
INSERT INTO journal.workout_types VALUES
    ('Strength', true), ('Cardio', true), ('Mobility / recovery', true), ('Dog walk', false), ('Other', true)
ON CONFLICT (workout_type) DO NOTHING;

CREATE TABLE IF NOT EXISTS journal.workouts (
    workout_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workout_date DATE NOT NULL REFERENCES core.calendar (day),
    workout_type TEXT NOT NULL DEFAULT 'Other' REFERENCES journal.workout_types,
    activity TEXT NOT NULL DEFAULT '',      -- free text, e.g. "Bike" for a Cardio session
    minutes NUMERIC CHECK (minutes IS NULL OR minutes BETWEEN 0 AND 1440),
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('journal', 'health_tracker', 'manual')),
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workouts_by_date ON journal.workouts (workout_date);
CREATE INDEX IF NOT EXISTS workouts_by_source ON journal.workouts (source, workout_date);
CREATE UNIQUE INDEX IF NOT EXISTS workouts_by_tracker_ref ON journal.workouts (source_ref) WHERE source = 'health_tracker';
