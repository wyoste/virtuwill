-- VirtuWill data model · habits, one row per day
-- A day's habits are one row: a column per habit (run, lift, read, …). A column is
-- NULL until the owner sets it, so the day follows what its records show (a run
-- logged, a drink); true or false is the owner's own answer and wins.
--
-- This replaces journal.habit_logs (a row per day and habit, which could only exist
-- on a day with a journal entry): habits no longer need an entry. journal.habits
-- stays as the list the screens draw, and names each habit's column, label,
-- build/limit polarity and the record that ticks it.

CREATE TABLE IF NOT EXISTS journal.daily_habits (
    day DATE PRIMARY KEY REFERENCES core.calendar (day),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE journal.habits ADD COLUMN IF NOT EXISTS position INTEGER NOT NULL DEFAULT 100;
UPDATE journal.habits SET position = p FROM (VALUES ('run', 10), ('lift', 20), ('read', 30), ('guitar', 40),
                                                     ('sexual', 50), ('drink', 60), ('smoke', 70)) v (h, p)
WHERE habit = v.h;

-- A column for every habit there is (the seven built in, and any an older release added), then the old rows.
DO $$
DECLARE
    h text;
    cols text;
BEGIN
    FOR h IN SELECT habit FROM journal.habits ORDER BY position, habit LOOP
        EXECUTE format('ALTER TABLE journal.daily_habits ADD COLUMN IF NOT EXISTS %I BOOLEAN', h);
    END LOOP;
    IF to_regclass('journal.habit_logs') IS NOT NULL THEN
        SELECT string_agg(format('bool_or(done) FILTER (WHERE habit = %L) AS %I', habit, habit), ', ' ORDER BY habit),
               string_agg(format('%I', habit), ', ' ORDER BY habit)
        INTO h, cols FROM journal.habits;
        EXECUTE format('INSERT INTO journal.daily_habits (day, %s) SELECT entry_date, %s FROM journal.habit_logs
                        GROUP BY entry_date ON CONFLICT (day) DO NOTHING', cols, h);
    END IF;
END $$;

DROP VIEW IF EXISTS core.daily_summary, journal.day_habits, journal.derived_habits CASCADE;
DROP TABLE IF EXISTS journal.habit_logs CASCADE;

-- What the day's records say, for the habits that tick themselves (NULL: no record, or no rule).
CREATE VIEW journal.derived_habits AS
SELECT c.day, h.habit,
       CASE h.derived_from
           WHEN 'strength_workout' THEN EXISTS (SELECT 1 FROM journal.workouts w
                                                WHERE w.workout_date = c.day AND w.workout_type = 'Strength')
           -- A run, by what was done: not any note that happens to contain "run" (brunch).
           WHEN 'run_workout' THEN EXISTS (SELECT 1 FROM journal.workouts w
                                           WHERE w.workout_date = c.day AND w.workout_type = 'Cardio'
                                             AND w.activity ~* '\m(run|jog)')
           WHEN 'alcohol' THEN EXISTS (SELECT 1 FROM health.alcohol a WHERE a.drink_date = c.day AND a.containers > 0)
       END AS done
FROM core.calendar c CROSS JOIN journal.habits h
WHERE h.derived_from IS NOT NULL;

-- Each day's habits, a row per habit for the screens: what the owner set, else what the day's records show.
CREATE VIEW journal.day_habits AS
SELECT c.day, h.habit, h.label, h.polarity, h.derived_from, h.position,
       COALESCE((to_jsonb(m) ->> h.habit)::boolean, d.done, false) AS done,
       CASE WHEN to_jsonb(m) ->> h.habit IS NOT NULL THEN 'manual' WHEN d.done THEN 'derived' ELSE 'none' END AS origin
FROM core.calendar c
CROSS JOIN journal.habits h
LEFT JOIN journal.daily_habits m ON m.day = c.day
LEFT JOIN journal.derived_habits d ON d.day = c.day AND d.habit = h.habit;

-- The same, a column per habit: every day with a habit set or ticked. Made from journal.habits, so a habit
-- added there (with its column) appears once journal.make_habit_days() runs again.
CREATE OR REPLACE FUNCTION journal.make_habit_days() RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    cols text;
BEGIN
    SELECT string_agg(format('bool_or(done) FILTER (WHERE habit = %L) AS %I', habit, habit), ', ' ORDER BY position, habit)
    INTO cols FROM journal.habits;
    EXECUTE 'DROP VIEW IF EXISTS journal.habit_days';
    EXECUTE format($v$CREATE VIEW journal.habit_days AS
                      SELECT day, %s,
                             COUNT(*) FILTER (WHERE done AND polarity = 'build') AS habits_built,
                             COUNT(*) FILTER (WHERE done AND polarity = 'limit') AS habits_to_limit
                      FROM journal.day_habits
                      WHERE day IN (SELECT day FROM journal.daily_habits
                                    UNION SELECT workout_date FROM journal.workouts
                                    UNION SELECT drink_date FROM health.alcohol)
                      GROUP BY day$v$, cols);
END $$;
SELECT journal.make_habit_days();

-- The cross-domain day, as in 90_daily_summary.sql, with habits from the day's own row and its records.
CREATE VIEW core.daily_summary AS
WITH active_days AS (
    SELECT day FROM health.daily_activity
    UNION SELECT day FROM journal.daily_habits
    UNION SELECT day FROM finance.spending
    UNION SELECT posted_on FROM finance.transactions
    UNION SELECT observed_on FROM garden.plant_observations
    UNION SELECT post_date FROM content.blog_posts
    UNION SELECT received_on FROM content.messages
), spend AS (
    SELECT day, SUM(amount) AS spending FROM finance.spending GROUP BY day
), income AS (
    SELECT t.posted_on AS day, SUM(-t.amount) AS income_received
    FROM finance.transactions t JOIN finance.movement_types mt USING (movement_type)
    WHERE mt.flow = 'income' GROUP BY t.posted_on
)
SELECT c.day, c.iso_weekday, c.week_start, c.month_start,
       e.entry_id IS NOT NULL AS has_journal_entry,
       COALESCE(h.habits_built, 0) AS habits_built,
       COALESCE(h.habits_to_limit, 0) AS habits_to_limit,
       COALESCE(a.workout_minutes, 0) AS workout_minutes,
       COALESCE(a.qualifying_workout_day, false) AS qualifying_workout_day,
       a.total_calories,
       a.calorie_target,
       COALESCE(a.beers, 0) AS beers,
       a.weight,
       COALESCE(s.spending, 0) AS spending,
       COALESCE(i.income_received, 0) AS income_received,
       (SELECT COUNT(*) FROM garden.plant_observations o WHERE o.observed_on = c.day) AS garden_observations,
       (SELECT COUNT(*) FROM content.blog_posts p WHERE p.post_date = c.day AND p.published) AS posts_published
FROM active_days d
JOIN core.calendar c ON c.day = d.day
LEFT JOIN journal.entries e ON e.entry_date = c.day
LEFT JOIN LATERAL (SELECT COUNT(*) FILTER (WHERE dh.done AND dh.polarity = 'build') AS habits_built,
                          COUNT(*) FILTER (WHERE dh.done AND dh.polarity = 'limit') AS habits_to_limit
                   FROM journal.day_habits dh WHERE dh.day = c.day) h ON true
LEFT JOIN health.daily_activity a ON a.day = c.day
LEFT JOIN spend s ON s.day = c.day
LEFT JOIN income i ON i.day = c.day;
