"""Fitness: every workout in one base table, with a fact table per kind (db/schema/112_fitness.sql).

    fitness.workouts           one row per session: date, activity type, minutes, title, note, source
    fitness.workout_cardio     distance, climbing, calories, heart rate      (cardio: run, walk, dog walk, bike …)
    fitness.workout_routes     the GPS line, under a cardio row
    fitness.workout_strength   the lifts and sets                            (lifting)
    fitness.workout_hiit       the circuit                                   (HIIT)
    fitness.workout_sessions   the view: a workout with all of its facts; everything reads this

The API stays at /api/v1/health/workouts. A workout is given by its activity_type (GET
/api/v1/health/activity-types lists them); the older {workout_type, activity} still works and
is read by fitness.activity_type_for, as the move from journal.workouts and the importers do.
"""
import hashlib
import os

from flask import Blueprint, jsonify, request

from . import db, media, records, routes
from .auth import admin_required
from .util import number, plain

bp = Blueprint("fitness", __name__)

SESSIONS = "SELECT * FROM fitness.workout_sessions"
# The older workout types, still accepted as workout_type and from the Health tracker.
WORKOUT_TYPES = {"Strength", "Cardio", "HIIT", "Mobility / recovery", "Dog walk", "Other"}
CIRCUIT = (("rounds", 1, 100), ("exercises_per_round", 1, 50), ("work_seconds", 1, 3600),
           ("exercise_rest_seconds", 0, 3600), ("round_rest_seconds", 0, 3600))
# Cardio facts: (field, low, high).
CARDIO = (("distance", 0, 1000), ("calories", 0, 20000), ("avg_heart_rate", 20, 250), ("elevation_gain_m", 0, 20000))


def activity_type_for(conn, kind=None, what=None):
    """The activity type for a workout described the older way: a type (Cardio, Strength …) and/or words
    ("Running", RunKeeper's "Strength Training"). The rules are fitness.activity_type_for, in the schema."""
    return conn.execute("SELECT fitness.activity_type_for(%s, %s) AS t", (kind, what or "")).fetchone()["t"]


def session(conn, workout_id):
    row = conn.execute(SESSIONS + " WHERE workout_id = %s", (workout_id,)).fetchone()
    return plain(row) if row else None


# ── The facts of each kind ───────────────────────────────────────────────────

def _lifts(conn, value):
    """[{lift, sets}] from a body's lifts: known lifts keep their catalogue spelling, new ones are added."""
    if not isinstance(value, list) or len(value) > 40:
        raise records.Invalid("lifts must be a list of up to 40 {lift, sets}")
    known = {r["lift"].lower(): r["lift"] for r in conn.execute("SELECT lift FROM fitness.lifts")}
    out, seen = [], set()
    for item in value:
        name = " ".join(str((item.get("lift") if isinstance(item, dict) else item) or "").split())
        sets = number(item.get("sets")) if isinstance(item, dict) else None
        if not name or len(name) > 60:
            raise records.Invalid("Each lift needs a name of up to 60 characters")
        if sets is None or sets != int(sets) or not 1 <= sets <= 50:
            raise records.Invalid(f"{name}: sets must be a whole number from 1 to 50")
        reps = number(item.get("reps")) if item.get("reps") not in (None, "") else None
        if reps is not None and (reps != int(reps) or not 1 <= reps <= 1000):
            raise records.Invalid(f"{name}: reps must be a whole number from 1 to 1000")
        weight = number(item.get("weight_lb")) if item.get("weight_lb") not in (None, "") else None
        if weight is not None and not 0 <= weight <= 2000:
            raise records.Invalid(f"{name}: weight must be from 0 to 2000 lb")
        name = known.get(name.lower(), name)
        if name.lower() in seen:
            raise records.Invalid(f"{name} is listed twice")
        seen.add(name.lower())
        out.append({"lift": name, "sets": int(sets), "reps": int(reps) if reps is not None else None, "weight_lb": weight})
    return out


def _circuit(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise records.Invalid("circuit must be an object")
    out = {}
    for key, low, high in CIRCUIT:
        n = number(value.get(key)) if value.get(key) not in (None, "") else (0 if low == 0 else None)
        if n is None or n != int(n) or not low <= n <= high:
            raise records.Invalid(f"{key.replace('_', ' ')} must be a whole number from {low} to {high}")
        out[key] = int(n)
    return out


def _cardio(body):
    """The cardio facts a body gives (only those it mentions)."""
    out = {}
    for key, low, high in CARDIO:
        if key in body:
            value = body[key]
            n = number(value) if value not in (None, "") else None
            if value not in (None, "") and (n is None or not low <= n <= high):
                raise records.Invalid(f"{key.replace('_', ' ')} must be a number between {low} and {high}")
            out[key] = n
    if "distance_unit" in body and body["distance_unit"] not in (None, ""):
        if body["distance_unit"] not in ("mi", "km"):
            raise records.Invalid("distance_unit must be one of: km, mi")
        out["distance_unit"] = body["distance_unit"]
    return out


def write_cardio(conn, workout_id, facts):
    """Make sure a cardio workout has its cardio row, and set the facts given."""
    conn.execute("INSERT INTO fitness.workout_cardio (workout_id) VALUES (%s) ON CONFLICT (workout_id) DO NOTHING", (workout_id,))
    if facts:
        conn.execute(f"UPDATE fitness.workout_cardio SET {', '.join(f'{k} = %s' for k in facts)} WHERE workout_id = %s",
                     [*facts.values(), workout_id])


def _prepare(conn, values, body):
    """Settle the activity type, validate each kind's facts, and write them once the workout row exists."""
    if values.get("activity_type"):
        if not conn.execute("SELECT 1 FROM fitness.activity_types WHERE activity_type = %s", (values["activity_type"],)).fetchone():
            raise records.Invalid(f"Unknown activity_type: {values['activity_type']} (see /api/v1/health/activity-types)")
    elif "workout_type" in body or "activity" in body:
        kind = body.get("workout_type")
        if kind not in (None, "") and kind not in WORKOUT_TYPES:
            raise records.Invalid(f"workout_type must be one of: {', '.join(sorted(WORKOUT_TYPES))}")
        values["activity_type"] = activity_type_for(conn, kind or None, body.get("activity"))
        if "title" not in body and body.get("activity"):
            values["title"] = str(body["activity"]).strip()[:100]
    elif request.method == "POST":
        values["activity_type"] = "other"
    else:
        values.pop("activity_type", None)          # a PUT that doesn't change it
    cardio = _cardio(body)
    lifts = _lifts(conn, body["lifts"]) if "lifts" in body else None
    circuit = _circuit(body["circuit"]) if "circuit" in body else None

    def write(conn, workout_id):
        w = conn.execute("SELECT category, minutes FROM fitness.workouts WHERE workout_id = %s", (workout_id,)).fetchone()
        kind = w["category"]
        if kind == "cardio":
            write_cardio(conn, workout_id, cardio)
        if kind == "strength" and lifts is not None:
            conn.execute("DELETE FROM fitness.workout_strength WHERE workout_id = %s", (workout_id,))
            for position, x in enumerate(lifts):
                conn.execute("INSERT INTO fitness.lifts (lift) VALUES (%s) ON CONFLICT (lift) DO NOTHING", (x["lift"],))
                conn.execute("""INSERT INTO fitness.workout_strength (workout_id, position, lift, sets, reps, weight_lb)
                                VALUES (%s, %s, %s, %s, %s, %s)""",
                             (workout_id, position, x["lift"], x["sets"], x["reps"], x["weight_lb"]))
        if kind == "hiit" and "circuit" in body:
            if circuit is None:
                conn.execute("DELETE FROM fitness.workout_hiit WHERE workout_id = %s", (workout_id,))
            else:
                conn.execute("""INSERT INTO fitness.workout_hiit (workout_id, rounds, exercises_per_round, work_seconds,
                                                                  exercise_rest_seconds, round_rest_seconds)
                                VALUES (%(id)s, %(rounds)s, %(exercises_per_round)s, %(work_seconds)s,
                                        %(exercise_rest_seconds)s, %(round_rest_seconds)s)
                                ON CONFLICT (workout_id) DO UPDATE SET rounds = EXCLUDED.rounds,
                                    exercises_per_round = EXCLUDED.exercises_per_round, work_seconds = EXCLUDED.work_seconds,
                                    exercise_rest_seconds = EXCLUDED.exercise_rest_seconds,
                                    round_rest_seconds = EXCLUDED.round_rest_seconds""", circuit | {"id": workout_id})
                if w["minutes"] is None:          # a blank time is the circuit's length
                    conn.execute("""UPDATE fitness.workouts w SET minutes = round(h.total_seconds / 60.0, 1)
                                    FROM fitness.workout_hiit h WHERE h.workout_id = w.workout_id AND w.workout_id = %s""",
                                 (workout_id,))
    return values, write


F = records.Field
records.Resource(bp, "/api/v1/health/workouts", "fitness.workouts", "workout_id", [
    F("workout_date", "date", required=True), F("activity_type", max_length=40, nullable=True),
    F("title", max_length=100), F("minutes", "number", low=0, high=1440), F("note", max_length=2000),
    F("started_at", "time")],
    date_column="workout_date", order="workout_date DESC, started_at DESC NULLS LAST, workout_id DESC",
    defaults={"source": "manual"}, prepare=_prepare, select=SESSIONS)


@bp.route("/api/v1/health/activity-types")
@admin_required
def activity_types_v1():
    """What a workout can be, grouped by kind for the picker."""
    with db.tx() as conn:
        return jsonify([plain(r) for r in conn.execute(
            """SELECT t.activity_type, t.label, t.category, k.label AS category_label, t.counts_toward_goal
               FROM fitness.activity_types t JOIN fitness.categories k USING (category)
               ORDER BY k.position, t.position, t.label""")])


@bp.route("/api/v1/health/lifts")
@admin_required
def lifts_v1():
    """The lifts to pick from, grouped for the picker; lifts added by hand come last."""
    with db.tx() as conn:
        return jsonify([plain(r) for r in conn.execute(
            "SELECT lift, muscle_group FROM fitness.lifts ORDER BY muscle_group = '', muscle_group, position, lift")])


# ── Routes: a GPX or TCX file for a run, ride or walk ────────────────────────

def save_route(conn, workout_id, summary, file_name, asset=None):
    """Store a cardio workout's route (never over one uploaded in the app, unless this is an upload too).
    Fills in what the workout left blank: distance, minutes and start. Returns True if stored."""
    write_cardio(conn, workout_id, {})
    stored = conn.execute(
        """INSERT INTO fitness.workout_routes (workout_id, points, distance_km, elevation_gain_m, started_at, ended_at,
                                               file_name, file_asset_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (workout_id) DO UPDATE SET points = EXCLUDED.points, distance_km = EXCLUDED.distance_km,
               elevation_gain_m = EXCLUDED.elevation_gain_m, started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at,
               file_name = EXCLUDED.file_name, file_asset_id = EXCLUDED.file_asset_id, created_at = now()
           WHERE fitness.workout_routes.file_asset_id IS NULL OR EXCLUDED.file_asset_id IS NOT NULL""",
        (workout_id, db.jsonb(summary["points"]), summary["distance_km"], summary["elevation_gain_m"],
         summary["started_at"], summary["ended_at"], (file_name or "")[:200], asset)).rowcount
    if not stored:
        return False
    if summary["distance_km"]:
        conn.execute("""UPDATE fitness.workout_cardio
                        SET distance = round(CASE distance_unit WHEN 'mi' THEN %(km)s::numeric / 1.609344 ELSE %(km)s::numeric END, 2)
                        WHERE workout_id = %(id)s AND distance IS NULL""", {"km": summary["distance_km"], "id": workout_id})
    if summary["started_at"] and summary["ended_at"]:
        minutes = (summary["ended_at"] - summary["started_at"]).total_seconds() / 60
        if 0 < minutes <= 1440:
            conn.execute("UPDATE fitness.workouts SET minutes = %s WHERE workout_id = %s AND minutes IS NULL",
                         (round(minutes, 1), workout_id))
    if summary["started_at"]:
        conn.execute("UPDATE fitness.workouts SET started_at = %s WHERE workout_id = %s AND started_at IS NULL",
                     (summary["started_at"], workout_id))
    return True


@bp.route("/api/v1/health/workouts/<int:workout_id>/route", methods=["GET", "POST", "DELETE"])
@admin_required
def workout_route_v1(workout_id):
    """GET the route to draw; POST a GPX/TCX file as 'file' (it fills in distance and minutes when they're blank); DELETE it."""
    with db.tx() as conn:
        workout = conn.execute("SELECT category FROM fitness.workouts WHERE workout_id = %s", (workout_id,)).fetchone()
        if not workout:
            return jsonify({"error": "Not found"}), 404
        if request.method == "GET":
            row = conn.execute("SELECT * FROM fitness.workout_routes WHERE workout_id = %s", (workout_id,)).fetchone()
            return jsonify(plain(row)) if row else (jsonify({"error": "This workout has no route"}), 404)
        if request.method == "DELETE":
            conn.execute("DELETE FROM fitness.workout_routes WHERE workout_id = %s", (workout_id,))
            return jsonify({"ok": True})
        if workout["category"] != "cardio":
            return jsonify({"error": "Only cardio workouts (runs, rides, walks …) carry a route"}), 400
        f = request.files.get("file")
        content = f.read(routes.MAX_BYTES + 1) if f else b""
        if not content:
            return jsonify({"error": "Choose a GPX or TCX file"}), 400
        try:
            summary = routes.summarise(routes.parse(content))
        except routes.RouteError as e:
            return jsonify({"error": str(e)}), 400
        name = os.path.basename(f.filename or "route.gpx")[:200]
        ext = ".tcx" if name.lower().endswith(".tcx") else ".gpx"
        asset = media.register(conn, f"private/health/routes/{hashlib.sha256(content).hexdigest()[:16]}{ext}", content,
                               visibility="private")
        save_route(conn, workout_id, summary, name, asset)
        row = conn.execute("SELECT * FROM fitness.workout_routes WHERE workout_id = %s", (workout_id,)).fetchone()
        return jsonify(plain(row)), 201
