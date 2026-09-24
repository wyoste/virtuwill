-- VirtuWill data model · health
-- Measurements, drinks, logged days, food reference, profile and goals.
-- Meals and workouts are in the journal schema; the views here combine both.

CREATE SCHEMA IF NOT EXISTS health;

-- Weigh-ins: many per calendar date, each with an optional time.
CREATE TABLE IF NOT EXISTS health.body_measurements (
    measurement_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measured_on DATE NOT NULL REFERENCES core.calendar (day),
    measured_at TIMESTAMPTZ,
    metric TEXT NOT NULL DEFAULT 'weight' CHECK (metric IN ('weight')),
    value NUMERIC NOT NULL CHECK (value > 0),
    unit TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),
    -- The canonical unit for every calculation; value/unit keep what was entered.
    value_lb NUMERIC GENERATED ALWAYS AS (CASE unit WHEN 'kg' THEN value / 0.45359237 ELSE value END) STORED,
    is_morning BOOLEAN,                     -- morning readings drive the trend; others are references
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('health_tracker', 'manual')),
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS measurements_by_date ON health.body_measurements (metric, measured_on, measured_at);
-- A tracker record keeps its row (and id) across saves.
CREATE UNIQUE INDEX IF NOT EXISTS measurements_by_tracker_ref ON health.body_measurements (source_ref) WHERE source = 'health_tracker';

CREATE TABLE IF NOT EXISTS health.alcohol (
    drink_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drink_date DATE NOT NULL REFERENCES core.calendar (day),
    name TEXT NOT NULL DEFAULT '',
    containers NUMERIC NOT NULL DEFAULT 0 CHECK (containers >= 0),
    oz_per_container NUMERIC CHECK (oz_per_container IS NULL OR oz_per_container > 0),
    abv_pct NUMERIC CHECK (abv_pct IS NULL OR abv_pct BETWEEN 0 AND 100),
    calories NUMERIC,
    standard_drinks NUMERIC,                -- containers × oz × ABV ÷ 0.6 oz of alcohol
    source TEXT NOT NULL CHECK (source IN ('health_tracker', 'manual')),
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alcohol_by_date ON health.alcohol (drink_date);
CREATE UNIQUE INDEX IF NOT EXISTS alcohol_by_tracker_ref ON health.alcohol (source_ref) WHERE source = 'health_tracker';

-- Days marked "entire day logged": a missing day means unknown intake, not zero.
CREATE TABLE IF NOT EXISTS health.daily_logs (
    log_date DATE PRIMARY KEY REFERENCES core.calendar (day),
    nutrition_complete BOOLEAN NOT NULL,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS health.foods (
    food_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',          -- the serving the nutrition is for
    calories NUMERIC,
    protein_g NUMERIC,
    carbs_g NUMERIC,
    fat_g NUMERIC,
    fiber_g NUMERIC,
    reference_note TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meals_food_fk') THEN
        ALTER TABLE journal.meals ADD CONSTRAINT meals_food_fk FOREIGN KEY (food_id) REFERENCES health.foods ON DELETE SET NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS health.recipes (
    recipe_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health.recipe_ingredients (
    recipe_id BIGINT NOT NULL REFERENCES health.recipes ON DELETE CASCADE,
    position INTEGER NOT NULL,
    food_id TEXT NOT NULL REFERENCES health.foods,
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (recipe_id, position)
);

-- One row: the settings goals are derived from. History is kept in profile_history.
CREATE TABLE IF NOT EXISTS health.profile (
    profile_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (profile_id = 1),
    height_in NUMERIC CHECK (height_in IS NULL OR height_in > 0),
    age INTEGER,
    mode TEXT CHECK (mode IS NULL OR mode IN ('loss', 'maintain', 'gain')),
    bmi_goal NUMERIC,
    calorie_target NUMERIC,
    alcohol_days SMALLINT[] NOT NULL DEFAULT '{5,6,7}',   -- ISO weekdays (5 = Friday)
    drink_boundary NUMERIC NOT NULL DEFAULT 3,             -- a day at or above this many is outside the rules
    details JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS health.profile_history (
    changed_at TIMESTAMPTZ PRIMARY KEY DEFAULT now(),
    profile JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS health.goals (
    metric TEXT PRIMARY KEY,
    target NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    period TEXT NOT NULL CHECK (period IN ('day', 'week')),
    direction TEXT NOT NULL CHECK (direction IN ('at_least', 'at_most', 'below')),
    derived_from TEXT,                      -- e.g. 'profile.bmi_goal × height'
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO health.goals (metric, target, unit, period, direction) VALUES
    ('workout_days_per_week', 5, 'days', 'week', 'at_least'),
    ('qualifying_workout_minutes', 45, 'minutes', 'day', 'at_least'),
    ('beers_per_day', 3, 'beers', 'day', 'below')
ON CONFLICT (metric) DO NOTHING;

-- ── Views ────────────────────────────────────────────────────────────────────
-- Weights are in pounds (value_lb). A later change to a view goes in a new
-- schema file that drops and recreates it.
DROP VIEW IF EXISTS health.goal_progress, health.weight_trend, health.weekly_workout_progress,
                    health.daily_activity CASCADE;

CREATE VIEW health.daily_activity AS
WITH days AS (
    SELECT workout_date AS day FROM journal.workouts
    UNION SELECT meal_date FROM journal.meals
    UNION SELECT measured_on FROM health.body_measurements
    UNION SELECT drink_date FROM health.alcohol
    UNION SELECT log_date FROM health.daily_logs
    UNION SELECT entry_date FROM journal.entries
), workouts AS (
    SELECT w.workout_date AS day,
           COALESCE(SUM(w.minutes) FILTER (WHERE t.counts_toward_goal), 0) AS workout_minutes,
           COUNT(*) FILTER (WHERE t.counts_toward_goal) AS workout_sessions,
           COALESCE(SUM(w.minutes) FILTER (WHERE NOT t.counts_toward_goal), 0) AS dog_walk_minutes
    FROM journal.workouts w JOIN journal.workout_types t USING (workout_type)
    GROUP BY w.workout_date
), meals AS (
    SELECT meal_date AS day,
           COUNT(*) FILTER (WHERE status = 'eaten') AS meals_eaten,
           COUNT(*) FILTER (WHERE status = 'planned') AS meals_planned,
           SUM(calories) FILTER (WHERE status = 'eaten') AS meal_calories,
           SUM(calories) FILTER (WHERE status = 'planned') AS planned_calories,
           SUM(protein_g) FILTER (WHERE status = 'eaten') AS protein_g,
           SUM(carbs_g) FILTER (WHERE status = 'eaten') AS carbs_g,
           SUM(fat_g) FILTER (WHERE status = 'eaten') AS fat_g,
           SUM(fiber_g) FILTER (WHERE status = 'eaten') AS fiber_g
    FROM journal.meals GROUP BY meal_date
), drinks AS (
    SELECT drink_date AS day, SUM(containers) AS beers, SUM(standard_drinks) AS standard_drinks,
           SUM(calories) AS alcohol_calories
    FROM health.alcohol GROUP BY drink_date
), weigh_ins AS (
    SELECT measured_on AS day,
           COUNT(*) AS weigh_ins,
           COUNT(*) FILTER (WHERE is_morning) AS morning_weigh_ins,
           (ARRAY_AGG(value_lb ORDER BY measured_at NULLS FIRST, measurement_id))[1] AS first_weight,
           (ARRAY_AGG(value_lb ORDER BY measured_at DESC NULLS LAST, measurement_id DESC))[1] AS weight,
           'lb'::text AS weight_unit,
           (ARRAY_AGG(is_morning ORDER BY measured_at DESC NULLS LAST, measurement_id DESC))[1] AS weight_is_morning,
           MIN(value_lb) AS min_weight,
           MAX(value_lb) AS max_weight,
           ROUND(AVG(value_lb) FILTER (WHERE is_morning), 1) AS morning_weight
    FROM health.body_measurements WHERE metric = 'weight'
    GROUP BY measured_on
), profile AS (
    SELECT * FROM health.profile WHERE profile_id = 1
)
SELECT d.day,
       c.iso_weekday,
       c.week_start,
       COALESCE(w.workout_minutes, 0) AS workout_minutes,
       COALESCE(w.workout_sessions, 0) AS workout_sessions,
       COALESCE(w.dog_walk_minutes, 0) AS dog_walk_minutes,
       COALESCE(w.workout_minutes, 0) >= COALESCE(
           (SELECT target FROM health.goals WHERE metric = 'qualifying_workout_minutes'), 45) AS qualifying_workout_day,
       COALESCE(m.meals_eaten, 0) AS meals_eaten,
       COALESCE(m.meals_planned, 0) AS meals_planned,
       m.meal_calories, m.planned_calories, m.protein_g, m.carbs_g, m.fat_g, m.fiber_g,
       COALESCE(dr.beers, 0) AS beers,
       COALESCE(dr.standard_drinks, 0) AS standard_drinks,
       dr.alcohol_calories,
       CASE WHEN m.meal_calories IS NULL AND dr.alcohol_calories IS NULL THEN NULL
            ELSE COALESCE(m.meal_calories, 0) + COALESCE(dr.alcohol_calories, 0) END AS total_calories,
       (SELECT calorie_target FROM profile) AS calorie_target,
       COALESCE(l.nutrition_complete, false) AS nutrition_complete,
       COALESCE(dr.beers, 0) = 0
           OR (dr.beers < COALESCE((SELECT drink_boundary FROM profile), 3)
               AND c.iso_weekday = ANY (COALESCE((SELECT alcohol_days FROM profile), '{5,6,7}'::smallint[]))) AS alcohol_within_rules,
       COALESCE(wi.weigh_ins, 0) AS weigh_ins,
       COALESCE(wi.morning_weigh_ins, 0) AS morning_weigh_ins,
       wi.first_weight, wi.weight, wi.weight_unit, wi.weight_is_morning,
       wi.min_weight, wi.max_weight, wi.morning_weight,
       e.entry_date IS NOT NULL AS has_journal_entry
FROM days d
JOIN core.calendar c ON c.day = d.day
LEFT JOIN workouts w ON w.day = d.day
LEFT JOIN meals m ON m.day = d.day
LEFT JOIN drinks dr ON dr.day = d.day
LEFT JOIN weigh_ins wi ON wi.day = d.day
LEFT JOIN health.daily_logs l ON l.log_date = d.day
LEFT JOIN journal.entries e ON e.entry_date = d.day;

CREATE VIEW health.weekly_workout_progress AS
SELECT week_start,
       COUNT(*) FILTER (WHERE qualifying_workout_day) AS qualifying_days,
       SUM(workout_minutes) AS workout_minutes,
       SUM(dog_walk_minutes) AS dog_walk_minutes,
       SUM(beers) AS beers,
       SUM(standard_drinks) AS standard_drinks,
       COUNT(*) FILTER (WHERE NOT alcohol_within_rules) AS days_outside_alcohol_rules,
       (SELECT target FROM health.goals WHERE metric = 'workout_days_per_week') AS target_days,
       COUNT(*) FILTER (WHERE qualifying_workout_day)
           >= COALESCE((SELECT target FROM health.goals WHERE metric = 'workout_days_per_week'), 5) AS goal_met
FROM health.daily_activity
GROUP BY week_start;

-- Morning weigh-ins drive the trend, as in the Health tracker; others stay as references.
CREATE VIEW health.weight_trend AS
SELECT day, weigh_ins, weight, weight_unit, weight_is_morning, min_weight, max_weight, morning_weight,
       ROUND(AVG(morning_weight) OVER seven_days, 1) AS morning_avg_7d,
       ROUND(COALESCE(AVG(morning_weight) OVER seven_days, weight) * 0.45359237
             / NULLIF(((SELECT height_in FROM health.profile) * 0.0254) ^ 2, 0), 1) AS bmi
FROM health.daily_activity
WHERE weight IS NOT NULL
WINDOW seven_days AS (ORDER BY day RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW);

CREATE VIEW health.goal_progress AS
SELECT g.metric, g.target, g.unit, g.period, g.direction, c.current_value,
       CASE WHEN c.current_value IS NULL THEN NULL
            WHEN g.direction = 'at_least' THEN c.current_value >= g.target
            WHEN g.direction = 'below' THEN c.current_value < g.target
            ELSE c.current_value <= g.target END AS met
FROM health.goals g
LEFT JOIN LATERAL (
    SELECT CASE g.metric
        WHEN 'workout_days_per_week' THEN (
            SELECT qualifying_days::numeric FROM health.weekly_workout_progress
            WHERE week_start = date_trunc('week', current_date)::date)
        WHEN 'qualifying_workout_minutes' THEN (
            SELECT workout_minutes FROM health.daily_activity WHERE day = current_date)
        WHEN 'weight' THEN (
            SELECT COALESCE(morning_avg_7d, weight) FROM health.weight_trend ORDER BY day DESC LIMIT 1)
        WHEN 'bmi' THEN (
            SELECT bmi FROM health.weight_trend ORDER BY day DESC LIMIT 1)
        WHEN 'daily_calories' THEN (
            SELECT total_calories FROM health.daily_activity WHERE day = current_date)
        WHEN 'beers_per_day' THEN (
            SELECT beers FROM health.daily_activity WHERE day = current_date)
    END AS current_value
) c ON true;
