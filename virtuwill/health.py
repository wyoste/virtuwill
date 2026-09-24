"""Health: the goals dashboard, workouts and weigh-ins logged directly, and the
Health tracker's data projected into the journal and health schemas.

Until the Health screens replace it, the embedded Health tracker
(yoste-health-v1) is the editing format. Every save copies its workouts,
meals, weigh-ins, drinks, logged days, foods, recipes and settings here.
Each tracker record keeps one row, and one id, across saves (see _ref).
Rows logged on the dashboard (source 'manual') are never touched by that copy.

Ownership: goals derived from the tracker's settings (weight, BMI, daily
calories) are edited in the tracker; the dashboard edits the others.
"""
import collections
import hashlib
import json
import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from . import db
from .auth import admin_required
from .util import in_calendar, moment, number, parse_date, plain

bp = Blueprint("health", __name__)
SOURCE = "health_tracker"
LB_PER_KG, M_PER_IN = 0.45359237, 0.0254
TRACKER_TABLES = ("journal.workouts", "journal.meals", "health.body_measurements", "health.alcohol")


def _text(value):
    return "" if value is None else str(value)


def _ref(kind, record, seen):
    """A stable reference for a tracker record, so its row keeps its id across saves.

    The tracker's records have no ids, so the reference is a digest of the
    record itself (plus an occurrence number for exact duplicates). Editing a
    record in the tracker therefore replaces that one row; unchanged records
    keep theirs.
    """
    if record.get("id") not in (None, ""):
        return f"{kind}:{record['id']}"
    digest = hashlib.sha1(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()[:16]
    seen[digest] += 1
    return f"{kind}:{digest}:{seen[digest]}"


def _upsert(conn, table, row, kept):
    """Insert or update one tracker row by its source_ref; remember the ref."""
    columns = list(row) + ["source", "source_ref"]
    values = list(row.values()) + [SOURCE, kept[-1]]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in row)
    conn.execute(f"""INSERT INTO {table} ({", ".join(columns)}) VALUES ({", ".join(["%s"] * len(columns))})
                     ON CONFLICT (source_ref) WHERE source = '{SOURCE}' DO UPDATE SET {updates}""", values)


def _iso_weekdays(js_days):
    """The tracker's weekend setting ('5,6,0', JavaScript getDay) as ISO weekdays 1–7."""
    days = {int(p) or 7 for p in _text(js_days).split(",") if p.strip().isdigit() and 0 <= int(p) <= 6}
    return sorted(days) or [5, 6, 7]


def _seed(document):
    """The tracker's built-in data (foods, recipes) from its HTML document."""
    match = re.search(r'<script id="seed" type="application/json">(.*?)</script>', document or "", re.S)
    try:
        return json.loads(match.group(1)) if match else {}
    except ValueError:
        return {}


def _workout_type(conn, name):
    name = _text(name).strip() or "Other"
    known = {r["workout_type"] for r in conn.execute("SELECT workout_type FROM journal.workout_types")}
    return name if name in known else "Other"


def project(conn, state, document=None):
    """Bring the tracker's rows in the shared tables in line with its state; returns a sync report.

    Unchanged records keep their rows and ids; new ones are added and ones
    deleted in the tracker are removed. Rows logged elsewhere are untouched.
    """
    counts = collections.Counter({k: 0 for k in ("workouts", "meals", "weigh-ins", "drinks", "foods", "recipes", "logged days")})
    report = {"skipped": collections.Counter(), "removed": {}, "fields": {}, "syncedAt": datetime.now(timezone.utc).isoformat()}

    refs = {table: [] for table in TRACKER_TABLES}

    def records(kind, table):
        seen = collections.Counter()
        for record in state.get(kind) or []:
            if not isinstance(record, dict):
                report["skipped"][kind] += 1
                continue
            report["fields"].setdefault(kind, set()).update(record.keys())
            day = parse_date(record.get("date"))
            if not in_calendar(day):
                report["skipped"][kind] += 1
                continue
            refs[table].append(_ref(kind, record, seen))
            yield record, day

    for r, day in records("workouts", "journal.workouts"):
        workout_type = _workout_type(conn, r.get("type"))
        minutes = number(r.get("minutes"))
        _upsert(conn, "journal.workouts", {
            "workout_date": day, "workout_type": workout_type,
            "activity": "" if workout_type == _text(r.get("type")) else _text(r.get("type")),
            "minutes": minutes if minutes is not None and 0 <= minutes <= 1440 else None,
            "note": _text(r.get("note")), "details": db.jsonb(r)}, refs["journal.workouts"])
        counts["workouts"] += 1

    conn.execute("DELETE FROM health.recipes WHERE source = %s", (SOURCE,))
    foods = {f.get("id"): f for f in (state.get("foods") or []) if isinstance(f, dict) and f.get("id")}
    seed = _seed(document)
    for f in seed.get("foods") or []:
        foods.setdefault(f.get("id"), f)
    for food_id, f in foods.items():
        conn.execute(
            """INSERT INTO health.foods (food_id, name, unit, calories, protein_g, carbs_g, fat_g, fiber_g, reference_note, url, source, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (food_id) DO UPDATE SET name = EXCLUDED.name, unit = EXCLUDED.unit, calories = EXCLUDED.calories,
                   protein_g = EXCLUDED.protein_g, carbs_g = EXCLUDED.carbs_g, fat_g = EXCLUDED.fat_g, fiber_g = EXCLUDED.fiber_g,
                   reference_note = EXCLUDED.reference_note, url = EXCLUDED.url, details = EXCLUDED.details, updated_at = now()""",
            (food_id, _text(f.get("name")) or food_id, _text(f.get("unit")), number(f.get("k")), number(f.get("p")),
             number(f.get("c")), number(f.get("fa")), number(f.get("fi")), _text(f.get("status")), _text(f.get("url")),
             SOURCE, db.jsonb(f)))
        counts["foods"] += 1
    for recipe in seed.get("recipes") or []:
        parts = [(food, number(qty)) for food, qty in recipe.get("parts") or [] if food in foods and number(qty)]
        if not recipe.get("name") or not parts:
            report["skipped"]["recipes"] += 1
            continue
        recipe_id = conn.execute("""INSERT INTO health.recipes (name, source) VALUES (%s, %s)
                                    ON CONFLICT (name) DO UPDATE SET source = EXCLUDED.source RETURNING recipe_id""",
                                 (recipe["name"], SOURCE)).fetchone()["recipe_id"]
        conn.execute("DELETE FROM health.recipe_ingredients WHERE recipe_id = %s", (recipe_id,))
        for position, (food, qty) in enumerate(parts):
            conn.execute("INSERT INTO health.recipe_ingredients VALUES (%s, %s, %s, %s)", (recipe_id, position, food, qty))
        counts["recipes"] += 1

    for r, day in records("meals", "journal.meals"):
        slot = _text(r.get("slot")).lower()
        _upsert(conn, "journal.meals", {
            "meal_date": day, "slot": slot if slot in ("breakfast", "lunch", "dinner", "snack") else "meal",
            "status": "planned" if _text(r.get("status")).lower() == "planned" else "eaten",
            "description": _text(r.get("name")), "note": _text(r.get("note")), "calories": number(r.get("k")),
            "protein_g": number(r.get("p")), "carbs_g": number(r.get("c")), "fat_g": number(r.get("fa")),
            "fiber_g": number(r.get("fi")), "details": db.jsonb(r)}, refs["journal.meals"])
        counts["meals"] += 1

    for r, day in records("weights", "health.body_measurements"):
        value = number(r.get("value"))
        if not value or value <= 0:
            refs["health.body_measurements"].pop()
            report["skipped"]["weights"] += 1
            continue
        _upsert(conn, "health.body_measurements", {
            "measured_on": day, "measured_at": moment(r.get("time") or r.get("at")), "metric": "weight", "value": value,
            "unit": "lb", "is_morning": r.get("morning") if isinstance(r.get("morning"), bool) else None,
            "note": _text(r.get("note")), "details": db.jsonb(r)}, refs["health.body_measurements"])
        counts["weigh-ins"] += 1

    for r, day in records("beers", "health.alcohol"):
        abv, oz = number(r.get("abv")), number(r.get("oz"))
        _upsert(conn, "health.alcohol", {
            "drink_date": day, "name": _text(r.get("name")), "containers": max(number(r.get("count")) or 0, 0),
            "oz_per_container": oz if oz and oz > 0 else None, "abv_pct": abv if abv is not None and 0 <= abv <= 100 else None,
            "calories": number(r.get("k")), "standard_drinks": number(r.get("std")), "details": db.jsonb(r)},
            refs["health.alcohol"])
        counts["drinks"] += 1

    # Records removed in the tracker are removed here; the rest kept their ids.
    for table, kept in refs.items():
        report["removed"][table] = conn.execute(f"DELETE FROM {table} WHERE source = %s AND NOT (source_ref = ANY(%s))",
                                                (SOURCE, kept)).rowcount

    logged = []
    for value in dict.fromkeys(state.get("complete") or []):
        day = parse_date(value)
        if not in_calendar(day):
            report["skipped"]["complete"] += 1
            continue
        conn.execute("""INSERT INTO health.daily_logs (log_date, nutrition_complete, source) VALUES (%s, true, %s)
                        ON CONFLICT (log_date) DO NOTHING""", (day, SOURCE))
        logged.append(day)
        counts["logged days"] += 1
    conn.execute("DELETE FROM health.daily_logs WHERE source = %s AND NOT (log_date = ANY(%s))", (SOURCE, logged))

    settings = state.get("settings") or {}
    if isinstance(settings, dict) and settings:
        height, bmi_goal = number(settings.get("height")), number(settings.get("goal"))
        calorie_target, age = number(settings.get("target")), number(settings.get("age"))
        mode = _text(settings.get("mode")) if _text(settings.get("mode")) in ("loss", "maintain", "gain") else None
        previous = conn.execute("SELECT to_jsonb(p) - 'updated_at' AS profile FROM health.profile p").fetchone()
        conn.execute(
            """INSERT INTO health.profile (profile_id, height_in, age, mode, bmi_goal, calorie_target, alcohol_days, details)
               VALUES (1, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (profile_id) DO UPDATE SET height_in = EXCLUDED.height_in, age = EXCLUDED.age, mode = EXCLUDED.mode,
                   bmi_goal = EXCLUDED.bmi_goal, calorie_target = EXCLUDED.calorie_target, alcohol_days = EXCLUDED.alcohol_days,
                   details = EXCLUDED.details, updated_at = now()""",
            (height if height and height > 0 else None, int(age) if age else None, mode, bmi_goal, calorie_target,
             _iso_weekdays(settings.get("weekend")), db.jsonb(settings)))
        current = conn.execute("SELECT to_jsonb(p) - 'updated_at' AS profile FROM health.profile p").fetchone()
        if previous and previous["profile"] != current["profile"]:
            conn.execute("INSERT INTO health.profile_history (profile) VALUES (%s)", (db.jsonb(previous["profile"]),))
        goals = []
        if bmi_goal:
            goals.append(("bmi", round(bmi_goal, 1), "BMI", "day", "at_most", "profile.bmi_goal"))
            if height:
                goals.append(("weight", round(bmi_goal * (height * M_PER_IN) ** 2 / LB_PER_KG, 1), "lb", "day", "at_most",
                              "profile.bmi_goal × height"))
        if calorie_target:
            goals.append(("daily_calories", calorie_target, "kcal", "day", "at_most", "profile.calorie_target"))
        for goal in goals:
            conn.execute("""INSERT INTO health.goals (metric, target, unit, period, direction, derived_from) VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (metric) DO UPDATE SET target = EXCLUDED.target, unit = EXCLUDED.unit,
                                derived_from = EXCLUDED.derived_from, updated_at = now()""", goal)

    report.update(counts)
    report["skipped"] = dict(report["skipped"])
    report["fields"] = {k: sorted(v) for k, v in report["fields"].items()}
    return report


# ── Dashboard and direct logging ─────────────────────────────────────────────

def dashboard(conn, days=14, weeks=8, weights=60):
    q = lambda sql, *args: [plain(r) for r in conn.execute(sql, args).fetchall()]
    sync = conn.execute("SELECT report FROM virtuwill.sync_reports WHERE source = %s", (SOURCE,)).fetchone()
    return {
        "available": True,
        "goals": q("""SELECT p.*, g.derived_from, g.derived_from IS NULL AS editable
                      FROM health.goal_progress p JOIN health.goals g USING (metric) ORDER BY metric"""),
        "weeks": q("SELECT * FROM health.weekly_workout_progress ORDER BY week_start DESC LIMIT %s", weeks),
        "days": q("SELECT * FROM health.daily_activity WHERE day > current_date - %s ORDER BY day DESC", days),
        "weights": q("SELECT * FROM health.weight_trend ORDER BY day DESC LIMIT %s", weights),
        "workouts": q("""SELECT w.workout_id, w.workout_date, COALESCE(NULLIF(w.activity, ''), w.workout_type) AS activity,
                                w.workout_type, w.minutes, w.note, NOT t.counts_toward_goal AS is_dog_walk, w.source
                         FROM journal.workouts w JOIN journal.workout_types t USING (workout_type)
                         ORDER BY w.workout_date DESC, w.workout_id DESC LIMIT 20"""),
        "weighIns": q("""SELECT measurement_id, measured_on, measured_at, value, unit, is_morning, note, source
                         FROM health.body_measurements WHERE metric = 'weight'
                         ORDER BY measured_on DESC, measured_at DESC NULLS LAST, measurement_id DESC LIMIT 20"""),
        "sync": sync["report"] if sync else None,
    }


@bp.route("/api/health/dashboard")
@admin_required
def dashboard_route():
    with db.tx() as conn:
        return jsonify(dashboard(conn))


@bp.route("/api/health/workouts", methods=["POST"])
@admin_required
def add_workout_route():
    data = request.get_json(silent=True) or {}
    day = parse_date(data.get("date"))
    minutes = number(data.get("minutes"))
    if not in_calendar(day) or minutes is None:
        return jsonify({"error": "date (YYYY-MM-DD) and minutes are required"}), 400
    if not 0 <= minutes <= 1440:
        return jsonify({"error": "minutes must be between 0 and 1440"}), 400
    with db.tx() as conn:
        workout_type = "Dog walk" if data.get("dogWalk") else _workout_type(conn, data.get("type"))
        workout_id = conn.execute(
            """INSERT INTO journal.workouts (workout_date, workout_type, activity, minutes, note, source)
               VALUES (%s, %s, %s, %s, %s, 'manual') RETURNING workout_id""",
            (day, workout_type, _text(data.get("activity"))[:100], minutes, _text(data.get("note"))[:2000])).fetchone()["workout_id"]
    return jsonify({"ok": True, "id": workout_id}), 201


@bp.route("/api/health/workouts/<int:workout_id>", methods=["DELETE"])
@admin_required
def delete_workout_route(workout_id):
    with db.tx() as conn:
        if not conn.execute("DELETE FROM journal.workouts WHERE workout_id = %s AND source = 'manual'", (workout_id,)).rowcount:
            return jsonify({"error": "Only workouts logged here can be deleted here"}), 404
    return jsonify({"ok": True})


@bp.route("/api/health/weigh-ins", methods=["POST"])
@admin_required
def add_weigh_in_route():
    """Log a weigh-in; a date can have any number of them."""
    data = request.get_json(silent=True) or {}
    day, value = parse_date(data.get("date")), number(data.get("value"))
    at = moment(data.get("at")) if data.get("at") else None
    if not in_calendar(day) or value is None or (data.get("at") and not at):
        return jsonify({"error": "date (YYYY-MM-DD), a weight, and an optional ISO time are required"}), 400
    if not 50 <= value <= 1000:
        return jsonify({"error": "weight must be between 50 and 1000 lb"}), 400
    with db.tx() as conn:
        measurement_id = conn.execute(
            """INSERT INTO health.body_measurements (measured_on, measured_at, metric, value, unit, is_morning, note, source)
               VALUES (%s, %s, 'weight', %s, 'lb', %s, %s, 'manual') RETURNING measurement_id""",
            (day, at, value, bool(data.get("morning")), _text(data.get("note"))[:2000])).fetchone()["measurement_id"]
    return jsonify({"ok": True, "id": measurement_id}), 201


@bp.route("/api/health/weigh-ins/<int:measurement_id>", methods=["DELETE"])
@admin_required
def delete_weigh_in_route(measurement_id):
    with db.tx() as conn:
        if not conn.execute("DELETE FROM health.body_measurements WHERE measurement_id = %s AND source = 'manual'",
                            (measurement_id,)).rowcount:
            return jsonify({"error": "Only weigh-ins logged here can be deleted here"}), 404
    return jsonify({"ok": True})


@bp.route("/api/health/goals/<metric>", methods=["PUT"])
@admin_required
def set_goal_route(metric):
    target = number((request.get_json(silent=True) or {}).get("target"))
    if target is None:
        return jsonify({"error": "A numeric target is required"}), 400
    with db.tx() as conn:
        goal = conn.execute("SELECT derived_from FROM health.goals WHERE metric = %s", (metric,)).fetchone()
        if not goal:
            return jsonify({"error": "Unknown goal"}), 404
        if goal["derived_from"]:
            # A dashboard edit would be overwritten by the next tracker save.
            return jsonify({"error": "This goal comes from the Health tracker's settings; change it there."}), 409
        conn.execute("UPDATE health.goals SET target = %s, updated_at = now() WHERE metric = %s", (target, metric))
    return jsonify({"ok": True})
