-- VirtuWill data model · cross-domain
-- One row per day with anything recorded, across journal, health, finance,
-- garden and content: the calendar date is the conformed key.

DROP VIEW IF EXISTS core.daily_summary CASCADE;

CREATE VIEW core.daily_summary AS
WITH active_days AS (
    SELECT day FROM health.daily_activity
    UNION SELECT day FROM finance.spending
    UNION SELECT posted_on FROM finance.transactions
    UNION SELECT observed_on FROM garden.plant_observations
    UNION SELECT post_date FROM content.blog_posts
    UNION SELECT received_on FROM content.messages
), habits AS (
    SELECT entry_date AS day,
           COUNT(*) FILTER (WHERE h.done AND hb.polarity = 'build') AS habits_built,
           COUNT(*) FILTER (WHERE h.done AND hb.polarity = 'limit') AS habits_to_limit
    FROM journal.habit_logs h JOIN journal.habits hb USING (habit)
    GROUP BY entry_date
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
LEFT JOIN habits h ON h.day = c.day
LEFT JOIN health.daily_activity a ON a.day = c.day
LEFT JOIN spend s ON s.day = c.day
LEFT JOIN income i ON i.day = c.day;
