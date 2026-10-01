"""RunKeeper, from the Lakebase synced table into the workouts — automatically.

prod.bronze.raw_runkeeper_activities (jobs/runkeeper_to_bronze.py lands the
RunKeeper export there) is mirrored into this database's bronze schema by a
Lakebase synced table, read-only (TABLE). Every RUNKEEPER_SYNC_MINUTES the app
reads the activities written since its last load and loads each one into
journal.workouts (source 'runkeeper', source_ref = RunKeeper's Activity Id) on
the date it started, with its GPX track in journal.workout_routes. Today,
Health and the journal then show it on that date like any other workout.

A reloaded activity updates its numbers (time, distance, the RunKeeper details)
and its route, but keeps the type, name, note and date you gave it in the app.

    RUNKEEPER_SYNC_MINUTES      how often to check (0, the default, turns the loop off)

    GET  /api/v1/health/runkeeper-sync   what the synced table holds, how far it's been loaded
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
TABLE = ("bronze", "runkeeper_activity")  # the synced copy of prod.bronze.raw_runkeeper_activities
BATCH = 200                               # activities per transaction; each carries its whole GPX file
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
    """The synced table is missing, or this app can't read it."""


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


# ── Reading the synced table ─────────────────────────────────────────────────

def _check(conn):
    schema, name = TABLE
    qualified = sql.Identifier(schema, name).as_string(conn)
    if not conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (qualified,)).fetchone()["ok"]:
        raise SyncedTableUnavailable(f"{schema}.{name} isn't in this database: check the synced table's name "
                                     "(docs/runkeeper-lakebase.md).")
    if not conn.execute("SELECT has_table_privilege(%s, 'SELECT') AS ok", (qualified,)).fetchone()["ok"]:
        raise SyncedTableUnavailable(f"The app can't read {schema}.{name}: grant its role SELECT (docs/runkeeper-lakebase.md).")


def state(conn):
    return conn.execute("SELECT through_at, through_id FROM journal.runkeeper_bronze_load").fetchone() or \
        {"through_at": None, "through_id": None}


def read(conn, after, limit=BATCH):
    """The next activities written after (at, id), in the order they were written."""
    return conn.execute(sql.SQL("""SELECT * FROM {t}
                                   WHERE (_ingested_at::timestamptz, activity_id)
                                         > (COALESCE(%s::timestamptz, '-infinity'), COALESCE(%s, ''))
                                   ORDER BY _ingested_at::timestamptz, activity_id LIMIT %s""").format(t=sql.Identifier(*TABLE)),
                        (after["through_at"], after["through_id"] if after["through_at"] else None, limit)).fetchall()


# ── Loading ──────────────────────────────────────────────────────────────────

def apply(conn, rows):
    """Load one batch of synced rows into the workouts. Returns counts."""
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
            RETURNING workout_id, workout_type, distance, (xmax = 0) AS added""",
                             {**w, "details": Jsonb(w["details"])}).fetchone()
        out["workouts_added" if saved["added"] else "workouts_updated"] += 1
        if saved["workout_type"] not in DISTANCE_TYPES:
            continue
        r = route(row)
        if not r:
            continue
        # A route you uploaded in the app (kept as a media asset) is never replaced.
        stored = conn.execute("""
            INSERT INTO journal.workout_routes (workout_id, points, distance_km, elevation_gain_m, started_at, ended_at, file_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workout_id) DO UPDATE SET points = EXCLUDED.points, distance_km = EXCLUDED.distance_km,
                elevation_gain_m = EXCLUDED.elevation_gain_m, started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at,
                file_name = EXCLUDED.file_name, created_at = now()
            WHERE journal.workout_routes.file_asset_id IS NULL""",
                              (saved["workout_id"], db.jsonb(r["points"]), r["distance_km"], r["elevation_gain_m"],
                               r["started_at"], r["ended_at"], (row.get("gpx_file") or "")[:200])).rowcount
        out["routes"] += stored
        if stored and saved["distance"] is None and r["distance_km"]:
            km = r["distance_km"]
            conn.execute("UPDATE journal.workouts SET distance = %s WHERE workout_id = %s",
                         (round(km / 1.609344 if w["distance_unit"] == "mi" else km, 2), saved["workout_id"]))
    return out


def _save_state(conn, last):
    conn.execute("""INSERT INTO journal.runkeeper_bronze_load (id, through_at, through_id) VALUES (true, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET through_at = EXCLUDED.through_at, through_id = EXCLUDED.through_id,
                                                   updated_at = now()""",
                 (last["_ingested_at"], last["activity_id"]))


def record(conn, report):
    """The latest load's outcome, shown in the workspace's Settings › Diagnostics."""
    conn.execute("""INSERT INTO virtuwill.sync_reports (source, report) VALUES (%s, %s)
                    ON CONFLICT (source) DO UPDATE SET report = EXCLUDED.report, synced_at = now()""",
                 (SOURCE, Jsonb(report)))


def _add(total, part):
    for k, v in part.items():
        total[k] = total.get(k, [] if isinstance(v, list) else 0) + v


def sync(dry_run=False, reload=False, batch=BATCH):
    """Load everything written since the last load, a batch per transaction, so a long first load keeps its
    progress. A dry run reads and maps the first batch, then rolls it back. Returns a report."""
    total = {"workouts_added": 0, "workouts_updated": 0, "routes": 0, "skipped": [], "batches": 0}
    after = None
    while True:
        with db.tx() as conn:
            if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext('virtuwill_runkeeper_bronze')) AS ok").fetchone()["ok"]:
                return {"skipped": "Another load is running."}
            _check(conn)
            if after is None:
                after = {"through_at": None, "through_id": None} if reload else state(conn)
            rows = read(conn, after, batch)
            if not rows:
                break
            _add(total, apply(conn, rows))
            total["batches"] += 1
            last = rows[-1]
            after = {"through_at": last["_ingested_at"], "through_id": last["activity_id"]}
            if dry_run:
                conn.rollback()
                return total | {"dry_run": True, "more": len(rows) == batch}
            _save_state(conn, last)
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
    out = {"table": ".".join(TABLE), "every_minutes": _minutes(),
           "loaded_through": loaded["through_at"].isoformat() if loaded["through_at"] else None}
    try:
        _check(conn)
    except SyncedTableUnavailable as e:
        return out | {"available": False, "error": str(e)}
    row = conn.execute(sql.SQL("""SELECT COUNT(*) AS rows, COUNT(gpx) AS with_gpx, MAX(_ingested_at::timestamptz) AS newest,
                                         COUNT(*) FILTER (WHERE (_ingested_at::timestamptz, activity_id)
                                             > (COALESCE(%s::timestamptz, '-infinity'), COALESCE(%s, ''))) AS not_loaded
                                  FROM {t}""").format(t=sql.Identifier(*TABLE)),
                       (loaded["through_at"], loaded["through_id"] if loaded["through_at"] else None)).fetchone()
    workouts = conn.execute("SELECT COUNT(*) AS n, MIN(workout_date) AS first, MAX(workout_date) AS last "
                            "FROM journal.workouts WHERE source = 'runkeeper'").fetchone()
    last = conn.execute("SELECT report, synced_at FROM virtuwill.sync_reports WHERE source = %s", (SOURCE,)).fetchone()
    return out | {"available": True, "rows": row["rows"], "with_gpx": row["with_gpx"], "not_loaded": row["not_loaded"],
                  "newest": row["newest"].isoformat() if row["newest"] else None,
                  "workouts": {"count": workouts["n"], "first": workouts["first"] and workouts["first"].isoformat(),
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
