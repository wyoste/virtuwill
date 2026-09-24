"""Relational journal and health model in Lakebase.

journal schema (the daily record):
  journal.entries        one journal entry per calendar date
  journal.entry_tags     tags on an entry, in the order entered
  journal.habit_logs     daily habit check-offs (run, lift, read, ...)
  journal.meals          eaten or planned meals by date and slot, from the
                         journal page or the Health tracker, with nutrition
  journal.workouts       workout sessions by date (Strength, Cardio, Mobility /
                         recovery, Dog walk, ...), from the tracker or logged directly

health schema (measurements, reference data and targets):
  health.body_measurements  weigh-ins: many per calendar date, each with an
                            optional time and a morning/reference flag
  health.alcohol            drinks by date: containers, size, ABV, calories, standard drinks
  health.daily_logs         days marked "entire day logged" (nutrition complete)
  health.foods              food reference: nutrition per unit
  health.profile            height, age, mode, BMI goal, calorie target, alcohol days
  health.goals              targets the views measure against

Views, recomputed on read, matching the Health tracker's own calculations:
  health.daily_activity           one row per date
  health.weekly_workout_progress  one row per Monday-start week
  health.weight_trend             morning 7-day average and BMI per weigh-in date
  health.goal_progress            each goal's current value and whether it is met

The Health tracker keeps its own document as the editing format. Every save
projects it into the tables above, replacing the rows it produced before, so
the journal and the dashboard read one set of base tables.
"""
import logging
from datetime import date, datetime, timezone

from psycopg.types.json import Jsonb

from storage import JournalDateTaken

log = logging.getLogger(__name__)

TRACKER = "health_tracker"
SLOTS = {"B": "breakfast", "L": "lunch", "D": "dinner"}

DDL = """
CREATE SCHEMA IF NOT EXISTS journal;
CREATE SCHEMA IF NOT EXISTS health;

CREATE TABLE IF NOT EXISTS journal.entries (
    entry_date DATE PRIMARY KEY,
    entry_id TEXT NOT NULL UNIQUE,
    quote TEXT NOT NULL DEFAULT '',
    quote_author TEXT NOT NULL DEFAULT '',
    free_write TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    account_snapshots JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS journal.entry_tags (
    entry_date DATE NOT NULL REFERENCES journal.entries ON UPDATE CASCADE ON DELETE CASCADE,
    tag TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (entry_date, tag)
);
CREATE TABLE IF NOT EXISTS journal.habit_logs (
    entry_date DATE NOT NULL REFERENCES journal.entries ON UPDATE CASCADE ON DELETE CASCADE,
    habit TEXT NOT NULL,
    done BOOLEAN NOT NULL,
    PRIMARY KEY (entry_date, habit)
);
CREATE TABLE IF NOT EXISTS journal.meals (
    meal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meal_date DATE NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'eaten' CHECK (status IN ('eaten', 'planned')),
    description TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    calories NUMERIC,
    protein_g NUMERIC,
    carbs_g NUMERIC,
    fat_g NUMERIC,
    fiber_g NUMERIC,
    source TEXT NOT NULL,
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meals_by_date ON journal.meals (meal_date);
CREATE INDEX IF NOT EXISTS meals_by_source ON journal.meals (source, meal_date);
CREATE TABLE IF NOT EXISTS journal.workouts (
    workout_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workout_date DATE NOT NULL,
    activity TEXT NOT NULL DEFAULT '',
    minutes NUMERIC CHECK (minutes IS NULL OR minutes >= 0),
    note TEXT NOT NULL DEFAULT '',
    is_dog_walk BOOLEAN NOT NULL DEFAULT false,
    source TEXT NOT NULL,
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workouts_by_date ON journal.workouts (workout_date);
CREATE INDEX IF NOT EXISTS workouts_by_source ON journal.workouts (source, workout_date);

CREATE TABLE IF NOT EXISTS health.body_measurements (
    measurement_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measured_on DATE NOT NULL,
    measured_at TIMESTAMPTZ,
    metric TEXT NOT NULL DEFAULT 'weight',
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL DEFAULT 'lb',
    is_morning BOOLEAN,
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS measurements_by_date ON health.body_measurements (metric, measured_on, measured_at);
CREATE TABLE IF NOT EXISTS health.alcohol (
    drink_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drink_date DATE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    containers NUMERIC NOT NULL DEFAULT 0 CHECK (containers >= 0),
    oz_per_container NUMERIC,
    abv_pct NUMERIC,
    calories NUMERIC,
    standard_drinks NUMERIC,
    source TEXT NOT NULL,
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alcohol_by_date ON health.alcohol (drink_date);
CREATE TABLE IF NOT EXISTS health.daily_logs (
    log_date DATE PRIMARY KEY,
    nutrition_complete BOOLEAN NOT NULL,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS health.foods (
    food_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS health.profile (
    profile_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (profile_id = 1),
    height_in NUMERIC,
    age INTEGER,
    mode TEXT,
    bmi_goal NUMERIC,
    calorie_target NUMERIC,
    alcohol_days SMALLINT[] NOT NULL DEFAULT '{5,6,7}',
    details JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS health.goals (
    metric TEXT PRIMARY KEY,
    target NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    period TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('at_least', 'at_most')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO health.goals (metric, target, unit, period, direction) VALUES
    ('workout_days_per_week', 5, 'days', 'week', 'at_least'),
    ('qualifying_workout_minutes', 45, 'minutes', 'day', 'at_least'),
    ('beers_per_day', 2, 'beers', 'day', 'at_most')
ON CONFLICT (metric) DO NOTHING;

-- Views are rebuilt on start so their columns always match this file.
DROP VIEW IF EXISTS health.goal_progress, health.weight_trend, health.weekly_workout_progress, health.daily_activity;

CREATE VIEW health.daily_activity AS
WITH days AS (
    SELECT workout_date AS day FROM journal.workouts
    UNION SELECT meal_date FROM journal.meals
    UNION SELECT measured_on FROM health.body_measurements WHERE metric = 'weight'
    UNION SELECT drink_date FROM health.alcohol
    UNION SELECT log_date FROM health.daily_logs
    UNION SELECT entry_date FROM journal.entries
), workouts AS (
    SELECT workout_date AS day,
           COALESCE(SUM(minutes) FILTER (WHERE NOT is_dog_walk), 0) AS workout_minutes,
           COUNT(*) FILTER (WHERE NOT is_dog_walk) AS workout_sessions,
           COALESCE(SUM(minutes) FILTER (WHERE is_dog_walk), 0) AS dog_walk_minutes
    FROM journal.workouts GROUP BY workout_date
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
    -- Many weigh-ins per date; order within a day by time, then entry order.
    SELECT measured_on AS day,
           COUNT(*) AS weigh_ins,
           COUNT(*) FILTER (WHERE is_morning) AS morning_weigh_ins,
           (ARRAY_AGG(value ORDER BY measured_at NULLS FIRST, measurement_id))[1] AS first_weight,
           (ARRAY_AGG(value ORDER BY measured_at DESC NULLS LAST, measurement_id DESC))[1] AS weight,
           (ARRAY_AGG(unit ORDER BY measured_at DESC NULLS LAST, measurement_id DESC))[1] AS weight_unit,
           (ARRAY_AGG(is_morning ORDER BY measured_at DESC NULLS LAST, measurement_id DESC))[1] AS weight_is_morning,
           MIN(value) AS min_weight,
           MAX(value) AS max_weight,
           AVG(value) FILTER (WHERE is_morning) AS morning_weight
    FROM health.body_measurements WHERE metric = 'weight'
    GROUP BY measured_on
)
SELECT d.day,
       EXTRACT(ISODOW FROM d.day)::int AS iso_weekday,
       COALESCE(w.workout_minutes, 0) AS workout_minutes,
       COALESCE(w.workout_sessions, 0) AS workout_sessions,
       COALESCE(w.dog_walk_minutes, 0) AS dog_walk_minutes,
       COALESCE(w.workout_minutes, 0) >= COALESCE(
           (SELECT target FROM health.goals WHERE metric = 'qualifying_workout_minutes'), 45) AS qualifying_workout_day,
       COALESCE(m.meals_eaten, 0) AS meals_eaten,
       COALESCE(m.meals_planned, 0) AS meals_planned,
       m.meal_calories,
       m.planned_calories,
       m.protein_g, m.carbs_g, m.fat_g, m.fiber_g,
       COALESCE(dr.beers, 0) AS beers,
       COALESCE(dr.standard_drinks, 0) AS standard_drinks,
       dr.alcohol_calories,
       CASE WHEN m.meal_calories IS NULL AND dr.alcohol_calories IS NULL THEN NULL
            ELSE COALESCE(m.meal_calories, 0) + COALESCE(dr.alcohol_calories, 0) END AS total_calories,
       (SELECT calorie_target FROM health.profile) AS calorie_target,
       COALESCE(l.nutrition_complete, false) AS nutrition_complete,
       COALESCE(dr.beers, 0) = 0
           OR (dr.beers < 3 AND EXTRACT(ISODOW FROM d.day)::int = ANY (
               COALESCE((SELECT alcohol_days FROM health.profile), '{5,6,7}'::smallint[]))) AS alcohol_within_rules,
       COALESCE(wi.weigh_ins, 0) AS weigh_ins,
       COALESCE(wi.morning_weigh_ins, 0) AS morning_weigh_ins,
       wi.first_weight,
       wi.weight,
       wi.weight_unit,
       wi.weight_is_morning,
       wi.min_weight,
       wi.max_weight,
       wi.morning_weight,
       e.entry_date IS NOT NULL AS has_journal_entry
FROM days d
LEFT JOIN workouts w USING (day)
LEFT JOIN meals m USING (day)
LEFT JOIN drinks dr USING (day)
LEFT JOIN weigh_ins wi USING (day)
LEFT JOIN health.daily_logs l ON l.log_date = d.day
LEFT JOIN journal.entries e ON e.entry_date = d.day;

CREATE VIEW health.weekly_workout_progress AS
SELECT date_trunc('week', day)::date AS week_start,
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
GROUP BY 1;

-- The trend uses morning weigh-ins only, as the Health tracker does; other
-- readings stay visible as references.
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
"""


# ── Shared parsing ────────────────────────────────────────────────────────────

def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_date(value):
    """A calendar date from ISO text or epoch milliseconds/seconds, else None."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return timestamp(text).date() if "T" in text else None
        except ValueError:
            return None


def _moment(value):
    """A full timestamp when the source recorded one, else None (date only)."""
    if not value or "T" not in str(value):
        return None
    try:
        return timestamp(value)
    except ValueError:
        return None


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


# ── Journal ───────────────────────────────────────────────────────────────────

def _entry_date(entry):
    return parse_date(entry.get("date")) or timestamp(entry.get("createdAt")).date()


def journal_write(db, entry):
    """Create or update one entry; returns True if it was new."""
    entry_date = _entry_date(entry)
    entry_id = str(entry["id"])
    fields = {
        "entry_id": entry_id, "entry_date": entry_date,
        "quote": entry.get("quote") or "", "quote_author": entry.get("quoteAuthor") or "",
        "free_write": entry.get("freeWrite") or "", "source": entry.get("source") or "manual",
        "accounts": Jsonb(entry.get("accounts") or []), "created_at": timestamp(entry.get("createdAt")),
    }
    current = db.execute("SELECT entry_date FROM journal.entries WHERE entry_id = %s", (entry_id,)).fetchone()
    if current:
        if current["entry_date"] != entry_date:
            if db.execute("SELECT 1 FROM journal.entries WHERE entry_date = %s", (entry_date,)).fetchone():
                raise JournalDateTaken(entry_date.isoformat())
            db.execute("UPDATE journal.meals SET meal_date = %s WHERE source = 'journal' AND meal_date = %s",
                       (entry_date, current["entry_date"]))
        db.execute(
            """UPDATE journal.entries SET entry_date = %(entry_date)s, quote = %(quote)s, quote_author = %(quote_author)s,
               free_write = %(free_write)s, source = %(source)s, account_snapshots = %(accounts)s,
               created_at = %(created_at)s, updated_at = now() WHERE entry_id = %(entry_id)s""", fields)
        was_new = False
    else:
        inserted = db.execute(
            """INSERT INTO journal.entries (entry_date, entry_id, quote, quote_author, free_write, source, account_snapshots, created_at)
               VALUES (%(entry_date)s, %(entry_id)s, %(quote)s, %(quote_author)s, %(free_write)s, %(source)s, %(accounts)s, %(created_at)s)
               ON CONFLICT (entry_date) DO NOTHING""", fields)
        if inserted.rowcount != 1:
            raise JournalDateTaken(entry_date.isoformat())
        was_new = True

    db.execute("DELETE FROM journal.entry_tags WHERE entry_date = %s", (entry_date,))
    tags = list(dict.fromkeys(str(t).strip() for t in entry.get("tags") or [] if str(t).strip()))
    for position, tag in enumerate(tags):
        db.execute("INSERT INTO journal.entry_tags (entry_date, tag, position) VALUES (%s, %s, %s)", (entry_date, tag, position))

    db.execute("DELETE FROM journal.habit_logs WHERE entry_date = %s", (entry_date,))
    for habit, done in (entry.get("habits") or {}).items():
        db.execute("INSERT INTO journal.habit_logs (entry_date, habit, done) VALUES (%s, %s, %s)", (entry_date, str(habit), bool(done)))

    db.execute("DELETE FROM journal.meals WHERE source = 'journal' AND meal_date = %s", (entry_date,))
    for key, slot in SLOTS.items():
        text = ((entry.get("meals") or {}).get(key) or "").strip()
        if text:
            db.execute("INSERT INTO journal.meals (meal_date, slot, description, source, source_ref) VALUES (%s, %s, %s, 'journal', %s)",
                       (entry_date, slot, text, entry_id))
    return was_new


JOURNAL_SELECT = """
SELECT e.entry_id, e.entry_date, e.quote, e.quote_author, e.free_write, e.source, e.account_snapshots, e.created_at,
       COALESCE((SELECT jsonb_agg(t.tag ORDER BY t.position) FROM journal.entry_tags t WHERE t.entry_date = e.entry_date), '[]') AS tags,
       COALESCE((SELECT jsonb_object_agg(h.habit, h.done) FROM journal.habit_logs h WHERE h.entry_date = e.entry_date), '{}') AS habits,
       COALESCE((SELECT jsonb_object_agg(m.slot, m.description) FROM journal.meals m
                 WHERE m.meal_date = e.entry_date AND m.source = 'journal'), '{}') AS meals,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('activity', w.activity, 'minutes', w.minutes, 'note', w.note,
                                                    'dogWalk', w.is_dog_walk, 'source', w.source) ORDER BY w.workout_id)
                 FROM journal.workouts w WHERE w.workout_date = e.entry_date), '[]') AS workouts,
       a.workout_minutes, a.qualifying_workout_day, a.weight, a.total_calories
FROM journal.entries e
LEFT JOIN health.daily_activity a ON a.day = e.entry_date
"""


def journal_entry(row):
    meals = row["meals"] or {}
    return {
        "id": row["entry_id"],
        "date": row["entry_date"].isoformat(),
        "quote": row["quote"],
        "quoteAuthor": row["quote_author"],
        "meals": {key: meals.get(slot, "") for key, slot in SLOTS.items()},
        "freeWrite": row["free_write"],
        "habits": row["habits"],
        "tags": row["tags"],
        "accounts": row["account_snapshots"],
        "source": row["source"],
        "createdAt": row["created_at"].astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        # Read-only: that day's health data from the shared tables.
        "health": {
            "workouts": [{**w, "minutes": _number(w["minutes"])} for w in row["workouts"]],
            "workoutMinutes": _number(row["workout_minutes"]) or 0,
            "qualifyingWorkoutDay": bool(row["qualifying_workout_day"]),
            "weight": _number(row["weight"]),
            "totalCalories": _number(row["total_calories"]),
        },
    }


def journal_list(db):
    return [journal_entry(r) for r in db.execute(JOURNAL_SELECT + " ORDER BY e.entry_date DESC").fetchall()]


def journal_delete(db, entry_id):
    row = db.execute("DELETE FROM journal.entries WHERE entry_id = %s RETURNING entry_date", (entry_id,)).fetchone()
    if not row:
        return False
    db.execute("DELETE FROM journal.meals WHERE source = 'journal' AND meal_date = %s", (row["entry_date"],))
    return True


def journal_date_exists(db, day):
    day = parse_date(day)
    return bool(day and db.execute("SELECT 1 FROM journal.entries WHERE entry_date = %s", (day,)).fetchone())


def import_journal(db, entries):
    """Copy legacy entries; a second entry on a date is kept aside, not dropped."""
    conflicts = []
    for entry in entries or []:
        if not entry.get("id"):
            continue
        try:
            with db.transaction():
                journal_write(db, entry)
        except JournalDateTaken:
            conflicts.append(entry)
    return conflicts


# ── Health tracker projection ─────────────────────────────────────────────────
# Field names follow the Yoste Health tracker (yoste-health-v1). Each row keeps
# the original record in `details`, and a sync report lists what was skipped.

TRACKER_TABLES = ("journal.workouts", "journal.meals", "health.body_measurements", "health.alcohol",
                  "health.daily_logs", "health.foods")
LB_PER_KG = 0.45359237
M_PER_IN = 0.0254


def _text(value):
    return "" if value is None else str(value)


def _ref(record, index):
    ref = record.get("id")
    return str(ref) if ref not in (None, "") else f"#{index}"


def _iso_weekdays(js_days):
    """Tracker weekend setting ('5,6,0', JavaScript getDay) to ISO weekdays 1-7."""
    days = set()
    for part in _text(js_days).split(","):
        if part.strip().isdigit() and 0 <= int(part) <= 6:
            days.add(int(part) or 7)
    return sorted(days) or [5, 6, 7]


def project_health(db, state):
    """Replace the tracker's rows in the shared tables; returns a sync report."""
    counts = {"workouts": 0, "meals": 0, "weights": 0, "drinks": 0, "completeDays": 0, "foods": 0}
    report = {"skipped": {}, "fields": {}, "syncedAt": datetime.now(timezone.utc).isoformat()}

    def records(kind):
        for index, record in enumerate(state.get(kind) or []):
            if not isinstance(record, dict):
                report["skipped"][kind] = report["skipped"].get(kind, 0) + 1
                continue
            report["fields"].setdefault(kind, set()).update(record.keys())
            day = parse_date(record.get("date"))
            if kind != "foods" and not day:
                report["skipped"][kind] = report["skipped"].get(kind, 0) + 1
                continue
            yield index, record, day

    for table in TRACKER_TABLES:
        db.execute(f"DELETE FROM {table} WHERE source = %s", (TRACKER,))

    for index, r, day in records("workouts"):
        activity = _text(r.get("type"))
        minutes = _number(r.get("minutes"))
        db.execute(
            """INSERT INTO journal.workouts (workout_date, activity, minutes, note, is_dog_walk, source, source_ref, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (day, activity, minutes if minutes is None or minutes >= 0 else None, _text(r.get("note")),
             activity.strip().lower() == "dog walk", TRACKER, _ref(r, index), Jsonb(r)))
        counts["workouts"] += 1

    for index, r, day in records("meals"):
        db.execute(
            """INSERT INTO journal.meals (meal_date, slot, status, description, note, calories, protein_g, carbs_g,
                                          fat_g, fiber_g, source, source_ref, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (day, (_text(r.get("slot")) or "meal").lower(),
             "planned" if _text(r.get("status")).lower() == "planned" else "eaten",
             _text(r.get("name")), _text(r.get("note")), _number(r.get("k")), _number(r.get("p")),
             _number(r.get("c")), _number(r.get("fa")), _number(r.get("fi")), TRACKER, _ref(r, index), Jsonb(r)))
        counts["meals"] += 1

    for index, r, day in records("weights"):
        value = _number(r.get("value"))
        if value is None:
            report["skipped"]["weights"] = report["skipped"].get("weights", 0) + 1
            continue
        morning = r.get("morning") if isinstance(r.get("morning"), bool) else None
        db.execute(
            """INSERT INTO health.body_measurements (measured_on, measured_at, metric, value, unit, is_morning, note,
                                                     source, source_ref, details)
               VALUES (%s, %s, 'weight', %s, 'lb', %s, %s, %s, %s, %s)""",
            (day, _moment(r.get("time") or r.get("at")), value, morning, _text(r.get("note")), TRACKER, _ref(r, index), Jsonb(r)))
        counts["weights"] += 1

    for index, r, day in records("beers"):
        db.execute(
            """INSERT INTO health.alcohol (drink_date, name, containers, oz_per_container, abv_pct, calories,
                                           standard_drinks, source, source_ref, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (day, _text(r.get("name")), max(_number(r.get("count")) or 0, 0), _number(r.get("oz")),
             _number(r.get("abv")), _number(r.get("k")), _number(r.get("std")), TRACKER, _ref(r, index), Jsonb(r)))
        counts["drinks"] += 1

    for value in dict.fromkeys(state.get("complete") or []):
        day = parse_date(value)
        if not day:
            report["skipped"]["complete"] = report["skipped"].get("complete", 0) + 1
            continue
        db.execute("INSERT INTO health.daily_logs (log_date, nutrition_complete, source) VALUES (%s, true, %s)"
                   " ON CONFLICT (log_date) DO NOTHING", (day, TRACKER))
        counts["completeDays"] += 1

    for index, r, _ in records("foods"):
        food_id = _text(r.get("id")) or f"#{index}"
        db.execute(
            """INSERT INTO health.foods (food_id, name, unit, calories, protein_g, carbs_g, fat_g, fiber_g,
                                         reference_note, url, source, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (food_id) DO UPDATE SET name = EXCLUDED.name, unit = EXCLUDED.unit,
                   calories = EXCLUDED.calories, protein_g = EXCLUDED.protein_g, carbs_g = EXCLUDED.carbs_g,
                   fat_g = EXCLUDED.fat_g, fiber_g = EXCLUDED.fiber_g, reference_note = EXCLUDED.reference_note,
                   url = EXCLUDED.url, source = EXCLUDED.source, details = EXCLUDED.details, updated_at = now()""",
            (food_id, _text(r.get("name")), _text(r.get("unit")), _number(r.get("k")), _number(r.get("p")),
             _number(r.get("c")), _number(r.get("fa")), _number(r.get("fi")), _text(r.get("status")),
             _text(r.get("url")), TRACKER, Jsonb(r)))
        counts["foods"] += 1

    settings = state.get("settings") or {}
    if isinstance(settings, dict) and settings:
        height, bmi_goal = _number(settings.get("height")), _number(settings.get("goal"))
        calorie_target = _number(settings.get("target"))
        age = _number(settings.get("age"))
        db.execute(
            """INSERT INTO health.profile (profile_id, height_in, age, mode, bmi_goal, calorie_target, alcohol_days, details)
               VALUES (1, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (profile_id) DO UPDATE SET height_in = EXCLUDED.height_in, age = EXCLUDED.age,
                   mode = EXCLUDED.mode, bmi_goal = EXCLUDED.bmi_goal, calorie_target = EXCLUDED.calorie_target,
                   alcohol_days = EXCLUDED.alcohol_days, details = EXCLUDED.details, updated_at = now()""",
            (height, int(age) if age is not None else None, _text(settings.get("mode")) or None, bmi_goal,
             calorie_target, _iso_weekdays(settings.get("weekend")), Jsonb(settings)))
        goals = []
        if bmi_goal:
            goals.append(("bmi", round(bmi_goal, 1), "BMI", "day", "at_most"))
            if height:
                # The tracker's target weight: the BMI goal at this height, in pounds.
                goals.append(("weight", round(bmi_goal * (height * M_PER_IN) ** 2 / LB_PER_KG, 1), "lb", "day", "at_most"))
        if calorie_target:
            goals.append(("daily_calories", calorie_target, "kcal", "day", "at_most"))
        for goal in goals:
            db.execute(
                """INSERT INTO health.goals (metric, target, unit, period, direction) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (metric) DO UPDATE SET target = EXCLUDED.target, unit = EXCLUDED.unit, updated_at = now()""",
                goal)

    report.update(counts)
    report["fields"] = {k: sorted(v) for k, v in report["fields"].items()}
    return report


# ── Workouts logged directly and dashboard reads ──────────────────────────────

def add_workout(db, day, activity, minutes, note, dog_walk):
    row = db.execute(
        """INSERT INTO journal.workouts (workout_date, activity, minutes, note, is_dog_walk, source)
           VALUES (%s, %s, %s, %s, %s, 'manual') RETURNING workout_id""",
        (day, activity, minutes, note, dog_walk)).fetchone()
    return row["workout_id"]


def add_weigh_in(db, day, value, measured_at, is_morning, note):
    row = db.execute(
        """INSERT INTO health.body_measurements (measured_on, measured_at, metric, value, unit, is_morning, note, source)
           VALUES (%s, %s, 'weight', %s, 'lb', %s, %s, 'manual') RETURNING measurement_id""",
        (day, measured_at, value, is_morning, note)).fetchone()
    return row["measurement_id"]


def delete_weigh_in(db, measurement_id):
    return db.execute("DELETE FROM health.body_measurements WHERE measurement_id = %s AND source = 'manual'",
                      (measurement_id,)).rowcount == 1


def delete_workout(db, workout_id):
    return db.execute("DELETE FROM journal.workouts WHERE workout_id = %s AND source = 'manual'", (workout_id,)).rowcount == 1


def set_goal(db, metric, target):
    return db.execute("UPDATE health.goals SET target = %s, updated_at = now() WHERE metric = %s", (target, metric)).rowcount == 1


def _plain(row):
    out = {}
    for key, value in row.items():
        if isinstance(value, date):
            value = value.isoformat()
        elif hasattr(value, "is_finite"):  # Decimal
            value = float(value)
        out[key] = value
    return out


def dashboard(db, days=14, weeks=8, weights=60):
    q = lambda sql, *args: [_plain(r) for r in db.execute(sql, args).fetchall()]
    return {
        "goals": q("SELECT * FROM health.goal_progress ORDER BY metric"),
        "weeks": q("SELECT * FROM health.weekly_workout_progress ORDER BY week_start DESC LIMIT %s", weeks),
        "days": q("SELECT * FROM health.daily_activity WHERE day > current_date - %s ORDER BY day DESC", days),
        "weights": q("SELECT * FROM health.weight_trend ORDER BY day DESC LIMIT %s", weights),
        "workouts": q("""SELECT workout_id, workout_date, activity, minutes, note, is_dog_walk, source
                         FROM journal.workouts ORDER BY workout_date DESC, workout_id DESC LIMIT 20"""),
        "weighIns": q("""SELECT measurement_id, measured_on, measured_at, value, unit, is_morning, note, source
                         FROM health.body_measurements WHERE metric = 'weight'
                         ORDER BY measured_on DESC, measured_at DESC NULLS LAST, measurement_id DESC LIMIT 20"""),
    }
