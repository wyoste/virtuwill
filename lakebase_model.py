"""Relational journal and health model in Lakebase.

  journal.entries        one journal entry per calendar date
  journal.entry_tags     tags on an entry, in the order entered
  journal.habit_logs     daily habit check-offs (run, lift, read, ...)
  journal.meals          meals by date and slot, from the journal or the Health tracker
  journal.workouts       workout sessions by date, from the Health tracker or logged directly
  health.body_measurements  weight (and other metrics) by date
  health.goals           targets the dashboard measures against

Views for the dashboard, recomputed on read:

  health.daily_activity           one row per date: workout minutes, qualifying
                                  day, dog walks, meals, calories, weight, journal
  health.weekly_workout_progress  qualifying workout days per week against the goal
  health.weight_trend             weight with a 7-day rolling average
  health.goal_progress            each goal's current value and whether it is met

The Health tracker (the embedded standalone app) keeps its own document as the
editing format. Every save projects its workouts, meals and weights into the
tables above, replacing the rows it produced before, so the journal and the
dashboard read one set of base tables.
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
    description TEXT NOT NULL DEFAULT '',
    calories NUMERIC,
    protein_g NUMERIC,
    carbs_g NUMERIC,
    fat_g NUMERIC,
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
    metric TEXT NOT NULL DEFAULT 'weight',
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL DEFAULT 'lb',
    source TEXT NOT NULL,
    source_ref TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS measurements_by_date ON health.body_measurements (metric, measured_on);
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
    ('qualifying_workout_minutes', 45, 'minutes', 'day', 'at_least')
ON CONFLICT (metric) DO NOTHING;

CREATE OR REPLACE VIEW health.daily_activity AS
WITH days AS (
    SELECT workout_date AS day FROM journal.workouts
    UNION SELECT meal_date FROM journal.meals
    UNION SELECT measured_on FROM health.body_measurements WHERE metric = 'weight'
    UNION SELECT entry_date FROM journal.entries
), workouts AS (
    SELECT workout_date AS day,
           COALESCE(SUM(minutes) FILTER (WHERE NOT is_dog_walk), 0) AS workout_minutes,
           COUNT(*) FILTER (WHERE NOT is_dog_walk) AS workout_sessions,
           COALESCE(SUM(minutes) FILTER (WHERE is_dog_walk), 0) AS dog_walk_minutes
    FROM journal.workouts GROUP BY workout_date
), meals AS (
    SELECT meal_date AS day, COUNT(*) AS meals_logged, SUM(calories) AS calories, SUM(protein_g) AS protein_g
    FROM journal.meals GROUP BY meal_date
), weights AS (
    SELECT DISTINCT ON (measured_on) measured_on AS day, value AS weight, unit AS weight_unit
    FROM health.body_measurements WHERE metric = 'weight'
    ORDER BY measured_on, created_at DESC, measurement_id DESC
)
SELECT d.day,
       COALESCE(w.workout_minutes, 0) AS workout_minutes,
       COALESCE(w.workout_sessions, 0) AS workout_sessions,
       COALESCE(w.dog_walk_minutes, 0) AS dog_walk_minutes,
       COALESCE(w.workout_minutes, 0) >= COALESCE(
           (SELECT target FROM health.goals WHERE metric = 'qualifying_workout_minutes'), 45) AS qualifying_workout_day,
       COALESCE(m.meals_logged, 0) AS meals_logged,
       m.calories,
       m.protein_g,
       b.weight,
       b.weight_unit,
       e.entry_date IS NOT NULL AS has_journal_entry
FROM days d
LEFT JOIN workouts w USING (day)
LEFT JOIN meals m USING (day)
LEFT JOIN weights b USING (day)
LEFT JOIN journal.entries e ON e.entry_date = d.day;

CREATE OR REPLACE VIEW health.weekly_workout_progress AS
SELECT date_trunc('week', day)::date AS week_start,
       COUNT(*) FILTER (WHERE qualifying_workout_day) AS qualifying_days,
       SUM(workout_minutes) AS workout_minutes,
       SUM(dog_walk_minutes) AS dog_walk_minutes,
       (SELECT target FROM health.goals WHERE metric = 'workout_days_per_week') AS target_days,
       COUNT(*) FILTER (WHERE qualifying_workout_day)
           >= COALESCE((SELECT target FROM health.goals WHERE metric = 'workout_days_per_week'), 5) AS goal_met
FROM health.daily_activity
GROUP BY 1;

CREATE OR REPLACE VIEW health.weight_trend AS
SELECT day, weight, weight_unit,
       ROUND(AVG(weight) OVER (ORDER BY day RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW), 1) AS avg_7d
FROM health.daily_activity
WHERE weight IS NOT NULL;

CREATE OR REPLACE VIEW health.goal_progress AS
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
            SELECT avg_7d FROM health.weight_trend ORDER BY day DESC LIMIT 1)
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


def _first(record, keys):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
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
       a.workout_minutes, a.qualifying_workout_day, a.weight
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
# The standalone tracker's record fields are read tolerantly; each row keeps the
# original record in `details`, and a sync report lists what was skipped.

DATE_KEYS = ("date", "day", "d", "when", "ts", "time", "at", "created", "createdAt")
MINUTE_KEYS = ("minutes", "mins", "min", "duration", "dur", "length")
ACTIVITY_KEYS = ("type", "activity", "kind", "workout", "category", "name", "title")
NOTE_KEYS = ("note", "notes", "desc", "description", "comment")
MEAL_SLOT_KEYS = ("meal", "slot", "type", "time", "category")
MEAL_TEXT_KEYS = ("name", "food", "title", "item", "desc", "description", "text", "note")
CAL_KEYS = ("calories", "kcal", "cal", "cals", "energy")


def _record_date(record):
    return parse_date(_first(record, DATE_KEYS))


def _ref(record, index):
    ref = record.get("id")
    return str(ref) if ref not in (None, "") else f"#{index}"


def project_health(db, state):
    """Replace the tracker's rows in the shared tables; returns a sync report."""
    report = {"workouts": 0, "meals": 0, "weights": 0, "skipped": {}, "fields": {}, "syncedAt": datetime.now(timezone.utc).isoformat()}

    def skip(kind):
        report["skipped"][kind] = report["skipped"].get(kind, 0) + 1

    def note_fields(kind, record):
        report["fields"].setdefault(kind, set()).update(record.keys())

    settings = state.get("settings") or {}
    unit = str(_first(settings, ("unit", "units", "weightUnit")) or "lb")[:8]

    for table, source_filter in (("journal.workouts", "source"), ("journal.meals", "source"), ("health.body_measurements", "source")):
        db.execute(f"DELETE FROM {table} WHERE {source_filter} = %s", (TRACKER,))

    for index, record in enumerate(state.get("workouts") or []):
        if not isinstance(record, dict):
            skip("workouts"); continue
        note_fields("workouts", record)
        day = _record_date(record)
        if not day:
            skip("workouts"); continue
        activity = str(_first(record, ACTIVITY_KEYS) or "")
        note = str(_first(record, NOTE_KEYS) or "")
        minutes = _number(_first(record, MINUTE_KEYS))
        dog = bool(record.get("dog") or record.get("dogWalk")) or "dog" in f"{activity} {note}".lower()
        db.execute(
            """INSERT INTO journal.workouts (workout_date, activity, minutes, note, is_dog_walk, source, source_ref, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (day, activity, minutes if minutes is None or minutes >= 0 else None, note, dog, TRACKER, _ref(record, index), Jsonb(record)))
        report["workouts"] += 1

    for index, record in enumerate(state.get("meals") or []):
        if not isinstance(record, dict):
            skip("meals"); continue
        note_fields("meals", record)
        day = _record_date(record)
        if not day:
            skip("meals"); continue
        slot = str(_first(record, MEAL_SLOT_KEYS) or "meal")
        slot = SLOTS.get(slot, slot).lower()
        db.execute(
            """INSERT INTO journal.meals (meal_date, slot, description, calories, protein_g, carbs_g, fat_g, source, source_ref, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (day, slot, str(_first(record, MEAL_TEXT_KEYS) or ""), _number(_first(record, CAL_KEYS)),
             _number(_first(record, ("protein", "protein_g"))), _number(_first(record, ("carbs", "carbs_g", "carbohydrates"))),
             _number(_first(record, ("fat", "fat_g"))), TRACKER, _ref(record, index), Jsonb(record)))
        report["meals"] += 1

    for index, record in enumerate(state.get("weights") or []):
        if not isinstance(record, dict):
            skip("weights"); continue
        note_fields("weights", record)
        day = _record_date(record)
        value = _number(_first(record, ("value", "weight", "lbs", "kg")))
        if not day or value is None:
            skip("weights"); continue
        db.execute(
            """INSERT INTO health.body_measurements (measured_on, metric, value, unit, source, source_ref, details)
               VALUES (%s, 'weight', %s, %s, %s, %s, %s)""",
            (day, value, str(record.get("unit") or unit)[:8], TRACKER, _ref(record, index), Jsonb(record)))
        report["weights"] += 1

    goal_weight = _number(_first(settings, ("goalWeight", "targetWeight", "goal_weight", "target_weight", "goal", "target")))
    if goal_weight:
        db.execute(
            """INSERT INTO health.goals (metric, target, unit, period, direction) VALUES ('weight', %s, %s, 'day', 'at_most')
               ON CONFLICT (metric) DO UPDATE SET target = EXCLUDED.target, unit = EXCLUDED.unit, updated_at = now()""",
            (goal_weight, unit))
    report["fields"] = {k: sorted(v) for k, v in report["fields"].items()}
    return report


# ── Workouts logged directly and dashboard reads ──────────────────────────────

def add_workout(db, day, activity, minutes, note, dog_walk):
    row = db.execute(
        """INSERT INTO journal.workouts (workout_date, activity, minutes, note, is_dog_walk, source)
           VALUES (%s, %s, %s, %s, %s, 'manual') RETURNING workout_id""",
        (day, activity, minutes, note, dog_walk)).fetchone()
    return row["workout_id"]


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
    }
