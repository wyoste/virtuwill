"""RunKeeper, from the Lakebase synced tables into the workouts — automatically.

Two notebooks land the RunKeeper export in the lakehouse:
jobs/runkeeper_activities_to_bronze.ipynb writes the activity log to
prod.bronze.raw_runkeeper_activities, and jobs/runkeeper_gpx_to_bronze.ipynb writes
the GPS tracks to prod.bronze.raw_runkeeper_gpx. Lakebase synced tables mirror both
into this database's bronze schema, read-only (TABLES).

Every RUNKEEPER_SYNC_MINUTES the app reads what was written to them since its last load:

- each activity becomes a workout in journal.workouts (source 'runkeeper',
  source_ref = RunKeeper's Activity Id) on the date it started, with its track
  in journal.workout_routes if the track has landed;
- each track that lands later is added to its activity's workout.

A track belongs to the activity whose GPX File column names it, else to the one whose
start time is in its file name. Today, Health and the journal show the workouts on
their dates like any other.

When an activity is loaded again, the app updates its numbers (time, distance and the
RunKeeper details) and its route. It keeps the type, name, note and date you gave it in
the app. The track table is optional: until it can be read, workouts load without routes.

    RUNKEEPER_SYNC_MINUTES      how often to check (0, the default, turns the loop off)

    GET  /api/v1/health/runkeeper-sync   what the synced tables hold, how far they've been loaded
    POST /api/v1/health/runkeeper-sync   load now ({"dry_run": true} to preview, {"reload": true} to read it all again)
"""
import logging
import os
import re
import threading
import time
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from psycopg import sql
from psycopg.types.json import Jsonb

from . import db, routes
from .auth import admin_required

log = logging.getLogger(__name__)
bp = Blueprint("runkeeper_synced", __name__)

SOURCE = "runkeeper_bronze"               # its row in virtuwill.sync_reports (Settings › Diagnostics)
# The synced copies of prod.bronze.raw_runkeeper_activities and raw_runkeeper_gpx, and each one's key.
TABLES = {"activities": ("bronze", "runkeeper_activity"), "gpx": ("bronze", "runkeeper_gpx")}
KEYS = {"activities": "activity_id", "gpx": "gpx_file"}
BATCH = 200                               # rows per transaction; a track carries its whole GPX file
NUMBERS = ("calories_burned", "average_heart_rate_bpm", "climb", "average_speed")
DISTANCE_TYPES = {"Cardio", "Dog walk"}   # as health.DISTANCE_TYPES: the only workouts with a distance or a route

# RunKeeper's activity types → the app's workout types. Anything not named here is cardio.
TYPES = {
    "strength training": "Strength", "weight training": "Strength",
    "circuit training": "HIIT", "crossfit": "HIIT", "bootcamp": "HIIT", "boxing / mma": "HIIT", "boxing/mma": "HIIT",
    "yoga": "Mobility / recovery", "pilates": "Mobility / recovery", "stretching": "Mobility / recovery",
    "meditation": "Mobility / recovery", "other": "Other",
}


class SyncedTableUnavailable(Exception):
    """The activities' synced table is missing, or this app can't read it."""


# ── RunKeeper's text → the workout model ─────────────────────────────────────

def _number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def start_of(text):
    """When the activity started, as RunKeeper wrote it ('2019-05-04 16:35:09', the time where it happened)."""
    try:
        return datetime.fromisoformat(str(text).strip().replace("T", " "))
    except ValueError:
        return None


def minutes(text):
    """A duration as minutes: '37:12' → 37.2, '1:02:33' → 62.55; None if it isn't one."""
    parts = str(text or "").strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        return None
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return round(seconds / 60, 2)


def workout_type(activity_type):
    return TYPES.get(str(activity_type or "").strip().lower(), "Cardio")


def workout(row):
    """A synced row as journal.workouts values, or None when it has no usable date."""
    start = start_of(row.get("activity_date"))
    if not start or not date(1900, 1, 1) <= start.date() <= date(2100, 12, 31):
        return None
    kind = workout_type(row.get("type"))
    unit = "km" if str(row.get("distance_unit") or "").strip().lower() == "km" else "mi"
    distance = _number(row.get("distance"))
    mins = minutes(row.get("duration"))
    details = {"runkeeper": {k: v for k, v in {
        "activity_id": row.get("activity_id"), "type": row.get("type"), "route_name": row.get("route_name"),
        "started_at": start.isoformat(), "duration": row.get("duration"), "average_pace": row.get("average_pace"),
        "speed_unit": row.get("speed_unit"), "climb_unit": row.get("climb_unit"), "gpx_file": row.get("gpx_file"),
        **{k: _number(row.get(k)) for k in NUMBERS}}.items() if v not in (None, "")}}
    return {"workout_date": start.date(), "workout_type": kind, "activity": (row.get("type") or "")[:100],
            "minutes": mins if mins is not None and 0 <= mins <= 1440 else None,
            "distance": round(distance, 2) if kind in DISTANCE_TYPES and distance and 0 < distance <= 1000 else None,
            "distance_unit": unit, "note": (row.get("notes") or "")[:2000], "source_ref": row["activity_id"],
            "details": details}


def route(row):
    """The activity's track, summarised for drawing, or None (no GPX, or one with no usable points)."""
    if not row.get("gpx"):
        return None
    try:
        return routes.summarise(routes.parse(row["gpx"].encode("utf-8")))
    except routes.RouteError:
        return None


# ── Reading the synced tables ────────────────────────────────────────────────

def _readable(conn, kind):
    """None if the synced table can be read, else why not."""
    schema, name = TABLES[kind]
    qualified = sql.Identifier(schema, name).as_string(conn)
    if not conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (qualified,)).fetchone()["ok"]:
        return f"{schema}.{name} isn't in this database: check the synced table's name (docs/runkeeper-lakebase.md)."
    if not conn.execute("SELECT has_table_privilege(%s, 'SELECT') AS ok", (qualified,)).fetchone()["ok"]:
        return f"The app can't read {schema}.{name}: grant its role SELECT (docs/runkeeper-lakebase.md)."
    return None


def _check(conn):
    """Raises if the activities can't be read; returns why the tracks can't be (None when they can)."""
    problem = _readable(conn, "activities")
    if problem:
        raise SyncedTableUnavailable(problem)
    return _readable(conn, "gpx")


def state(conn):
    row = conn.execute("SELECT * FROM journal.runkeeper_bronze_load").fetchone() or {}
    return {"activities": {"at": row.get("through_at"), "id": row.get("through_id")},
            "gpx": {"at": row.get("gpx_through_at"), "id": row.get("gpx_through_id")}}


# A track belongs to the activity whose GPX File names it, else to the one that started when its file name says.
_MATCH = "(lower(g.gpx_file) = lower(a.gpx_file) OR g.activity_start = a.activity_date)"
_FIRST = "ORDER BY lower(g.gpx_file) = lower(a.gpx_file) DESC NULLS LAST"
_AFTER = "({t}._ingested_at::timestamptz, {t}.{k}) > (COALESCE(%(at)s::timestamptz, '-infinity'), COALESCE(%(id)s, ''))"


def _after(after):
    return {"at": after["at"], "id": after["id"] if after["at"] else None}


def read_activities(conn, after, limit, with_gpx=True):
    """The next activities written after the cursor, each with its track if it has landed (gpx, track_file)."""
    a, g = (sql.Identifier(*TABLES[k]) for k in ("activities", "gpx"))
    track = sql.SQL(f"""LEFT JOIN LATERAL (SELECT g.gpx, g.gpx_file AS track_file FROM {{g}} g WHERE {_MATCH}
                                           {_FIRST} LIMIT 1) g ON true""").format(g=g) if with_gpx else \
        sql.SQL("CROSS JOIN (SELECT NULL::text AS gpx, NULL::text AS track_file) g")
    return conn.execute(sql.SQL("SELECT a.*, g.gpx, g.track_file FROM {a} a {track} WHERE " + _AFTER.format(t="a", k="activity_id")
                                + " ORDER BY a._ingested_at::timestamptz, a.activity_id LIMIT %(limit)s")
                        .format(a=a, track=track), _after(after) | {"limit": limit}).fetchall()


def read_tracks(conn, after, limit):
    """The next tracks written after the cursor, each with its activity's workout (None until it's loaded), and
    whether that workout's route was already drawn from this track since it landed (drawn)."""
    a, g = (sql.Identifier(*TABLES[k]) for k in ("activities", "gpx"))
    return conn.execute(sql.SQL(f"""
        SELECT g.gpx_file, g.gpx, g._ingested_at, a.activity_id, w.workout_id, w.workout_type, w.distance, w.distance_unit,
               r.file_name = g.gpx_file AND r.created_at >= g._ingested_at::timestamptz AS drawn
        FROM {{g}} g
        LEFT JOIN LATERAL (SELECT a.activity_id, a.gpx_file FROM {{a}} a WHERE {_MATCH} {_FIRST} LIMIT 1) a ON true
        LEFT JOIN journal.workouts w ON w.source = 'runkeeper' AND w.source_ref = a.activity_id
        LEFT JOIN journal.workout_routes r ON r.workout_id = w.workout_id
        WHERE {_AFTER.format(t="g", k="gpx_file")}
        ORDER BY g._ingested_at::timestamptz, g.gpx_file LIMIT %(limit)s""").format(a=a, g=g),
                        _after(after) | {"limit": limit}).fetchall()


# ── Loading ──────────────────────────────────────────────────────────────────

def attach(conn, workout_id, kind, distance, unit, gpx, file_name):
    """Draw a workout's route from its track. Returns 1 if stored, 0 if not (no usable track, not cardio, or a
    route you uploaded yourself, which is never replaced). A blank distance is filled from the track."""
    if kind not in DISTANCE_TYPES:
        return 0
    r = route({"gpx": gpx})
    if not r:
        return 0
    stored = conn.execute("""
        INSERT INTO journal.workout_routes (workout_id, points, distance_km, elevation_gain_m, started_at, ended_at, file_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (workout_id) DO UPDATE SET points = EXCLUDED.points, distance_km = EXCLUDED.distance_km,
            elevation_gain_m = EXCLUDED.elevation_gain_m, started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at,
            file_name = EXCLUDED.file_name, created_at = now()
        WHERE journal.workout_routes.file_asset_id IS NULL""",
                          (workout_id, db.jsonb(r["points"]), r["distance_km"], r["elevation_gain_m"],
                           r["started_at"], r["ended_at"], (file_name or "")[:200])).rowcount
    if stored and distance is None and r["distance_km"]:
        km = r["distance_km"]
        conn.execute("UPDATE journal.workouts SET distance = %s WHERE workout_id = %s",
                     (round(km / 1.609344 if unit == "mi" else km, 2), workout_id))
    return stored


def apply(conn, rows):
    """Load a batch of activities (with their tracks, where landed) into the workouts. Returns counts."""
    out = {"workouts_added": 0, "workouts_updated": 0, "routes": 0, "skipped": []}
    for row in rows:
        w = workout(row)
        if not w:
            out["skipped"].append(row.get("activity_id"))
            continue
        saved = conn.execute("""
            INSERT INTO journal.workouts (workout_date, workout_type, activity, minutes, distance, distance_unit, note,
                                          source, source_ref, details)
            VALUES (%(workout_date)s, %(workout_type)s, %(activity)s, %(minutes)s, %(distance)s, %(distance_unit)s, %(note)s,
                    'runkeeper', %(source_ref)s, %(details)s)
            ON CONFLICT (source_ref) WHERE source = 'runkeeper' DO UPDATE
                SET minutes = EXCLUDED.minutes, distance_unit = EXCLUDED.distance_unit,
                    distance = CASE WHEN journal.workouts.workout_type IN ('Cardio', 'Dog walk')
                                    THEN EXCLUDED.distance ELSE journal.workouts.distance END,
                    details = journal.workouts.details || EXCLUDED.details
            RETURNING workout_id, workout_type, distance, distance_unit, (xmax = 0) AS added""",
                             {**w, "details": Jsonb(w["details"])}).fetchone()
        out["workouts_added" if saved["added"] else "workouts_updated"] += 1
        if row.get("gpx"):
            out["routes"] += attach(conn, saved["workout_id"], saved["workout_type"], saved["distance"],
                                    saved["distance_unit"], row["gpx"], row.get("track_file"))
    return out


def apply_tracks(conn, rows):
    """Add a batch of newly landed tracks to their activities' workouts. Returns counts."""
    out = {"routes": 0, "tracks_without_activity": 0}
    for row in rows:
        if row["activity_id"] is None:
            out["tracks_without_activity"] += 1
        # Not loaded yet: the activity brings its track when it loads. Already drawn: its activity just brought it.
        elif row["workout_id"] is not None and not row["drawn"]:
            out["routes"] += attach(conn, row["workout_id"], row["workout_type"], row["distance"], row["distance_unit"],
                                    row["gpx"], row["gpx_file"])
    return out


def _save_state(conn, kind, at, key):
    columns = ("through_at", "through_id") if kind == "activities" else ("gpx_through_at", "gpx_through_id")
    conn.execute(sql.SQL("""INSERT INTO journal.runkeeper_bronze_load (id, {at}, {key}) VALUES (true, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET {at} = EXCLUDED.{at}, {key} = EXCLUDED.{key}, updated_at = now()""")
                 .format(at=sql.Identifier(columns[0]), key=sql.Identifier(columns[1])), (at, key))


def record(conn, report):
    """The latest load's outcome, shown in the workspace's Settings › Diagnostics."""
    conn.execute("""INSERT INTO virtuwill.sync_reports (source, report) VALUES (%s, %s)
                    ON CONFLICT (source) DO UPDATE SET report = EXCLUDED.report, synced_at = now()""",
                 (SOURCE, Jsonb(report)))


def _add(total, part):
    for k, v in part.items():
        total[k] = total.get(k, [] if isinstance(v, list) else 0) + v


def sync(dry_run=False, reload=False, batch=BATCH):
    """Load the activities, then the tracks, written since the last load: a batch per transaction, so a long first
    load keeps its progress. A dry run reads and maps the first batch, then rolls it back. Returns a report."""
    total = {"workouts_added": 0, "workouts_updated": 0, "routes": 0, "tracks_without_activity": 0, "skipped": [],
             "batches": 0}
    for kind in ("activities", "gpx"):
        after = None
        while True:
            with db.tx() as conn:
                if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext('virtuwill_runkeeper_bronze')) AS ok").fetchone()["ok"]:
                    return {"skipped": "Another load is running."}
                gpx_problem = _check(conn)
                if gpx_problem:
                    total["gpx"] = gpx_problem
                    if kind == "gpx":
                        break
                if after is None:
                    after = {"at": None, "id": None} if reload else state(conn)[kind]
                rows = (read_activities(conn, after, batch, with_gpx=not gpx_problem) if kind == "activities"
                        else read_tracks(conn, after, batch))
                if not rows:
                    break
                _add(total, apply(conn, rows) if kind == "activities" else apply_tracks(conn, rows))
                total["batches"] += 1
                last = rows[-1]
                after = {"at": last["_ingested_at"], "id": last[KEYS[kind]]}
                if dry_run:
                    conn.rollback()
                    return total | {"dry_run": True, "previewed": kind, "more": len(rows) == batch}
                _save_state(conn, kind, after["at"], after["id"])
                if len(rows) < batch:
                    break
    if dry_run:
        return total | {"dry_run": True, "nothing_new": True}
    if not total["batches"]:
        total["nothing_new"] = True
    total["skipped"] = total["skipped"][:50]
    with db.tx() as conn:
        record(conn, total)
    return total


def status(conn):
    loaded = state(conn)
    out = {"tables": {k: ".".join(v) for k, v in TABLES.items()}, "every_minutes": _minutes(),
           "loaded_through": {k: v["at"].isoformat() if v["at"] else None for k, v in loaded.items()}}
    try:
        gpx_problem = _check(conn)
    except SyncedTableUnavailable as e:
        return out | {"available": False, "error": str(e)}
    for kind in ("activities",) if gpx_problem else ("activities", "gpx"):
        t = sql.Identifier(*TABLES[kind])
        row = conn.execute(sql.SQL("SELECT COUNT(*) AS rows, MAX(_ingested_at::timestamptz) AS newest, "
                                   "COUNT(*) FILTER (WHERE " + _AFTER.format(t="x", k=KEYS[kind]) + ") AS not_loaded "
                                   "FROM {t} x").format(t=t), _after(loaded[kind])).fetchone()
        out[kind] = {"rows": row["rows"], "newest": row["newest"].isoformat() if row["newest"] else None,
                     "not_loaded": row["not_loaded"]}
    if gpx_problem:
        out["gpx"] = {"available": False, "error": gpx_problem}
    workouts = conn.execute("""SELECT COUNT(*) AS n, COUNT(r.workout_id) AS routes, MIN(workout_date) AS first,
                                      MAX(workout_date) AS last
                               FROM journal.workouts w LEFT JOIN journal.workout_routes r USING (workout_id)
                               WHERE w.source = 'runkeeper'""").fetchone()
    last = conn.execute("SELECT report, synced_at FROM virtuwill.sync_reports WHERE source = %s", (SOURCE,)).fetchone()
    return out | {"available": True,
                  "workouts": {"count": workouts["n"], "with_route": workouts["routes"],
                               "first": workouts["first"] and workouts["first"].isoformat(),
                               "last": workouts["last"] and workouts["last"].isoformat()},
                  "last_load": {"at": last["synced_at"].isoformat(), **last["report"]} if last else None}


def run_once(dry_run=False, reload=False):
    """One load; a failure is recorded for Diagnostics and returned, not raised."""
    try:
        return sync(dry_run, reload), None
    except SyncedTableUnavailable as error:
        failure = str(error)
    if not dry_run:
        with db.tx() as conn:
            record(conn, {"error": failure})
    return None, failure

# ── Automatic: a background loop in each worker (the advisory lock lets one load at a time) ──

_started = False


def _minutes():
    try:
        return max(0, float(os.environ.get("RUNKEEPER_SYNC_MINUTES", "0")))
    except ValueError:
        return 0


def _loop(minutes):
    time.sleep(45)                                  # let the app finish starting
    while True:
        try:
            report, error = run_once()
            if error:
                log.warning("RunKeeper sync: %s", error)
            elif report and not report.get("nothing_new") and not report.get("skipped"):
                log.info("RunKeeper sync: %s", {k: report.get(k) for k in ("workouts_added", "workouts_updated", "routes")})
        except Exception:
            log.exception("RunKeeper sync failed")
        time.sleep(minutes * 60)


def start():
    """Start the loop once per process, when RUNKEEPER_SYNC_MINUTES is set and a database is attached."""
    global _started
    minutes = _minutes()
    if _started or not minutes or not db.configured():
        return
    _started = True
    threading.Thread(target=_loop, args=(minutes,), name="runkeeper-sync", daemon=True).start()


@bp.route("/api/v1/health/runkeeper-sync")
@admin_required
def runkeeper_sync_status():
    with db.tx() as conn:
        return jsonify(status(conn))


@bp.route("/api/v1/health/runkeeper-sync", methods=["POST"])
@admin_required
def runkeeper_sync_now():
    body = request.get_json(silent=True) or {}
    report, error = run_once(bool(body.get("dry_run")), bool(body.get("reload")))
    if error:
        return jsonify({"error": error}), 409
    return jsonify(report)
