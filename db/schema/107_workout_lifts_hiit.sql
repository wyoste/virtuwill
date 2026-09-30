-- VirtuWill data model · lifts for strength workouts, circuits for HIIT
-- A strength workout lists the lifts done and the sets of each. A HIIT workout
-- describes its circuit: rounds, exercises per round, work time per exercise,
-- rest between exercises and rest between rounds. Distance and routes stay
-- with cardio and dog walks (enforced by the app, which clears them otherwise).

INSERT INTO journal.workout_types VALUES ('HIIT', true) ON CONFLICT (workout_type) DO NOTHING;

-- The lifts to pick from. Picking a name that isn't here adds it.
CREATE TABLE IF NOT EXISTS journal.lifts (
    lift TEXT PRIMARY KEY CHECK (length(lift) BETWEEN 1 AND 60),
    muscle_group TEXT NOT NULL DEFAULT '',          -- for grouping the picker; '' for lifts added by hand
    position INT NOT NULL DEFAULT 1000              -- the picker's order within a group
);
INSERT INTO journal.lifts (lift, muscle_group, position) VALUES
    ('Back squat', 'Legs', 10), ('Front squat', 'Legs', 20), ('Deadlift', 'Legs', 30), ('Romanian deadlift', 'Legs', 40),
    ('Leg press', 'Legs', 50), ('Lunge', 'Legs', 60), ('Bulgarian split squat', 'Legs', 70), ('Hip thrust', 'Legs', 80),
    ('Leg curl', 'Legs', 90), ('Leg extension', 'Legs', 100), ('Calf raise', 'Legs', 110),
    ('Bench press', 'Chest', 10), ('Incline bench press', 'Chest', 20), ('Dumbbell press', 'Chest', 30),
    ('Push-up', 'Chest', 40), ('Chest fly', 'Chest', 50), ('Dip', 'Chest', 60),
    ('Pull-up', 'Back', 10), ('Chin-up', 'Back', 20), ('Lat pulldown', 'Back', 30), ('Barbell row', 'Back', 40),
    ('Dumbbell row', 'Back', 50), ('Seated cable row', 'Back', 60), ('Face pull', 'Back', 70),
    ('Overhead press', 'Shoulders', 10), ('Dumbbell shoulder press', 'Shoulders', 20), ('Lateral raise', 'Shoulders', 30),
    ('Rear delt fly', 'Shoulders', 40), ('Shrug', 'Shoulders', 50),
    ('Biceps curl', 'Arms', 10), ('Hammer curl', 'Arms', 20), ('Triceps pushdown', 'Arms', 30),
    ('Skull crusher', 'Arms', 40), ('Overhead triceps extension', 'Arms', 50),
    ('Plank', 'Core', 10), ('Hanging leg raise', 'Core', 20), ('Cable crunch', 'Core', 30), ('Russian twist', 'Core', 40),
    ('Kettlebell swing', 'Full body', 10), ('Clean', 'Full body', 20), ('Farmer''s carry', 'Full body', 30)
ON CONFLICT (lift) DO NOTHING;

-- The lifts in one strength workout, in the order picked, with the sets of each.
CREATE TABLE IF NOT EXISTS journal.workout_lifts (
    workout_id BIGINT NOT NULL REFERENCES journal.workouts ON DELETE CASCADE,
    position INT NOT NULL,
    lift TEXT NOT NULL REFERENCES journal.lifts ON UPDATE CASCADE,
    sets INT NOT NULL CHECK (sets BETWEEN 1 AND 50),
    PRIMARY KEY (workout_id, position),
    UNIQUE (workout_id, lift)
);

-- One HIIT workout's circuit. Its length is
--   rounds × (exercises × work + (exercises − 1) × exercise rest) + (rounds − 1) × round rest.
CREATE TABLE IF NOT EXISTS journal.workout_circuits (
    workout_id BIGINT PRIMARY KEY REFERENCES journal.workouts ON DELETE CASCADE,
    rounds INT NOT NULL CHECK (rounds BETWEEN 1 AND 100),
    exercises_per_round INT NOT NULL CHECK (exercises_per_round BETWEEN 1 AND 50),
    work_seconds INT NOT NULL CHECK (work_seconds BETWEEN 1 AND 3600),
    exercise_rest_seconds INT NOT NULL DEFAULT 0 CHECK (exercise_rest_seconds BETWEEN 0 AND 3600),
    round_rest_seconds INT NOT NULL DEFAULT 0 CHECK (round_rest_seconds BETWEEN 0 AND 3600),
    total_seconds INT GENERATED ALWAYS AS (
        rounds * (exercises_per_round * work_seconds + (exercises_per_round - 1) * exercise_rest_seconds)
        + (rounds - 1) * round_rest_seconds) STORED
);
