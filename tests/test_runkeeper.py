"""RunKeeper: the bronze job's reading of the export, and the app's load from the Lakebase synced table.
The synced table is stood in for by a plain table shaped like the bronze one; the activities are made up."""
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import psycopg

from tests.support import PG, admin_client, fresh_database, needs_database
from tests.test_workout_routes import TRACK, gpx
from app import app
from virtuwill import runkeeper_synced as rk

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("runkeeper_job", ROOT / "jobs" / "runkeeper_to_bronze.py")
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)

HEADER = ("Activity Id,Date,Type,Route Name,Distance (mi),Duration,Average Pace,Average Speed (mph),Calories Burned,"
          "Climb (ft),Average Heart Rate (bpm),Friend's Tagged,Notes,GPX File\n")
CSV = HEADER + (
    'a1,2019-05-04 16:35:09,Running,,3.10,28:30,9:11,6.53,350,120,,,"Easy, with ""strides""",2019-05-04-163509.gpx\n'
    "a2,2019-05-06 06:10:00,Strength Training,,0.00,45:00,,,250,,,,,\n"
    "a3,2019-05-07 18:00:00,Cycling,,20.5,1:05:12,3:10,18.9,900,800,,,,\n")


class JobTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "activities" / "2026-09").mkdir(parents=True)
        (self.dir / "gpx").mkdir()
        (self.dir / "activities" / "2026-09" / "CardioActivities.csv").write_text(CSV)
        (self.dir / "activities" / "measurements.csv").write_text("Date,Weight\n2019-05-04,180\n")
        (self.dir / "gpx" / "2019-05-04-163509.gpx").write_bytes(gpx(TRACK))
        (self.dir / "gpx" / "2019-05-07-180000.gpx").write_bytes(gpx(TRACK))     # a3 names none: matched by its start
        (self.dir / "gpx" / "2018-01-01-000000.gpx").write_bytes(gpx(TRACK))     # no activity

    def test_headers_and_units(self):
        self.assertEqual(job.column("Distance (km)"), ("distance", "km"))
        self.assertEqual(job.column("Average Heart Rate (bpm)"), ("average_heart_rate_bpm", None))
        self.assertEqual(job.column("Friend's Tagged"), ("friends_tagged", None))
        self.assertEqual(job.column("Something New"), (None, None))
        self.assertEqual(job.gpx_name_for("2019-05-04 16:35:09"), "2019-05-04-163509.gpx")

    def test_reads_the_export_and_finds_each_track(self):
        activities, files = job.read_activities(str(self.dir / "activities"))
        self.assertEqual(len(files), 1)                                       # measurements.csv isn't read
        by_id = {a["activity_id"]: a for a in activities}
        self.assertEqual(by_id["a1"]["notes"], 'Easy, with "strides"')
        self.assertEqual((by_id["a1"]["distance_unit"], by_id["a1"]["speed_unit"], by_id["a1"]["climb_unit"]), ("mi", "mph", "ft"))
        self.assertIsNone(by_id["a2"]["gpx_file"])
        rows, summary = job.plan(activities, job.gpx_index(str(self.dir / "gpx")), {})
        self.assertEqual({r["activity_id"]: bool(r["_gpx_path"]) for r in rows}, {"a1": True, "a2": False, "a3": True})
        self.assertEqual((summary["with_gpx"], summary["gpx_files_without_activity"]), (2, 1))
        written = job.with_gpx_text(next(r for r in rows if r["activity_id"] == "a1"))
        self.assertIn("<trkpt", written["gpx"])
        self.assertEqual(set(written), set(job.COLUMNS) - {"_ingested_at"})

    def test_unchanged_activities_are_not_written_again_and_the_newest_export_wins(self):
        activities, _ = job.read_activities(str(self.dir / "activities"))
        index = job.gpx_index(str(self.dir / "gpx"))
        rows, _ = job.plan(activities, index, {})
        existing = {r["activity_id"]: r["_row_hash"] for r in rows}
        self.assertEqual(job.plan(activities, index, existing)[1]["new_or_changed"], 0)
        newer = self.dir / "activities" / "CardioActivities (1).csv"
        newer.write_text(CSV.replace("28:30", "29:00"))
        os.utime(newer, (2e9, 2e9))
        activities, _ = job.read_activities(str(self.dir / "activities"))
        rows, summary = job.plan(activities, index, existing)
        self.assertEqual(([r["activity_id"] for r in rows], summary["activities"]), (["a1"], 3))
        self.assertEqual(rows[0]["duration"], "29:00")


class MappingTests(unittest.TestCase):
    def test_durations_types_and_dates(self):
        self.assertEqual((rk.minutes("28:30"), rk.minutes("1:05:12"), rk.minutes(""), rk.minutes("abc")), (28.5, 65.2, None, None))
        self.assertEqual((rk.workout_type("Running"), rk.workout_type("Strength Training"), rk.workout_type("Yoga"),
                          rk.workout_type("Circuit Training"), rk.workout_type("Kayaking")),
                         ("Cardio", "Strength", "Mobility / recovery", "HIIT", "Cardio"))
        w = rk.workout({"activity_id": "a1", "activity_date": "2019-05-04 23:35:09", "type": "Running", "distance": "3.10",
                        "distance_unit": "mi", "duration": "28:30", "calories_burned": "350"})
        self.assertEqual((str(w["workout_date"]), w["minutes"], w["distance"]), ("2019-05-04", 28.5, 3.1))
        self.assertEqual(w["details"]["runkeeper"]["calories_burned"], 350)
        strength = rk.workout({"activity_id": "a2", "activity_date": "2019-05-06 06:10:00", "type": "Strength Training",
                               "distance": "0.00", "duration": "45:00"})
        self.assertIsNone(strength["distance"])
        self.assertIsNone(rk.workout({"activity_id": "x", "activity_date": "not a date"}))

    def test_the_loop_only_starts_when_asked(self):
        with mock.patch.dict(os.environ, {"RUNKEEPER_SYNC_MINUTES": "0"}), mock.patch.object(rk.threading, "Thread") as thread:
            rk.start()
        thread.assert_not_called()


SYNCED = """
DROP SCHEMA IF EXISTS bronze CASCADE;
CREATE SCHEMA bronze;
CREATE TABLE bronze.runkeeper_activity (
    activity_id TEXT PRIMARY KEY, activity_date TEXT, type TEXT, route_name TEXT, distance TEXT, distance_unit TEXT,
    duration TEXT, average_pace TEXT, average_speed TEXT, speed_unit TEXT, calories_burned TEXT, climb TEXT, climb_unit TEXT,
    average_heart_rate_bpm TEXT, friends_tagged TEXT, notes TEXT, gpx_file TEXT, gpx TEXT, gpx_bytes BIGINT, _extra TEXT,
    _source_file TEXT, _file_modified_at TIMESTAMPTZ, _row_hash TEXT, _ingested_at TIMESTAMPTZ);
"""
RUN1 = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


@needs_database
class SyncTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute(SYNCED)
        self.addCleanup(self.drop)
        self.owner = admin_client(app)

    def drop(self):
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS bronze CASCADE")

    def land(self, rows, at):
        """Rows as the job MERGEs them: every row of one run stamped with the same _ingested_at."""
        with psycopg.connect(PG, autocommit=True) as conn:
            for r in rows:
                r = {**r, "_ingested_at": at}
                conn.execute(f"""INSERT INTO bronze.runkeeper_activity ({', '.join(r)}) VALUES ({', '.join(['%s'] * len(r))})
                                 ON CONFLICT (activity_id) DO UPDATE SET {', '.join(f'{k} = EXCLUDED.{k}' for k in r)}""",
                             list(r.values()))

    def activity(self, aid, when, kind="Running", distance="3.10", duration="28:30", track=True, notes=None):
        return {"activity_id": aid, "activity_date": when, "type": kind, "distance": distance, "distance_unit": "mi",
                "duration": duration, "notes": notes, "gpx_file": f"{aid}.gpx" if track else None,
                "gpx": gpx(TRACK).decode() if track else None}

    def sync(self, **body):
        r = self.owner.post("/api/v1/health/runkeeper-sync", json=body)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def test_without_the_synced_table_it_says_why(self):
        self.drop()
        r = self.owner.post("/api/v1/health/runkeeper-sync", json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("bronze.runkeeper_activity", r.get_json()["error"])
        self.assertFalse(self.owner.get("/api/v1/health/runkeeper-sync").get_json()["available"])

    def test_activities_land_on_their_dates_with_routes(self):
        self.land([self.activity("a1", "2019-05-04 16:35:09", notes="Easy"),
                   self.activity("a2", "2019-05-06 06:10:00", "Strength Training", "0.00", "45:00", track=False),
                   self.activity("a3", "2019-05-07 18:00:00", "Cycling", distance="", duration="1:05:12")], RUN1)
        preview = self.sync(dry_run=True)
        self.assertEqual((preview["workouts_added"], preview["dry_run"]), (3, True))
        self.assertEqual(self.owner.get("/api/v1/health/runkeeper-sync").get_json()["not_loaded"], 3)

        report = self.sync()
        self.assertEqual((report["workouts_added"], report["routes"]), (3, 2))
        day = self.owner.get("/api/v1/today?date=2019-05-04").get_json()
        [run] = day["workouts"]
        self.assertEqual((run["activity"], run["workout_type"], float(run["distance"]), float(run["minutes"]), run["note"],
                          run["has_route"], run["source"]), ("Running", "Cardio", 3.1, 28.5, "Easy", True, "runkeeper"))
        route = self.owner.get(f"/api/v1/health/workouts/{run['workout_id']}/route").get_json()
        self.assertEqual(len(route["points"]), 5)
        [lift] = self.owner.get("/api/v1/today?date=2019-05-06").get_json()["workouts"]
        self.assertEqual((lift["workout_type"], lift["distance"], lift["has_route"]), ("Strength", None, False))
        [ride] = self.owner.get("/api/v1/today?date=2019-05-07").get_json()["workouts"]
        self.assertAlmostEqual(float(ride["distance"]), 1.0, delta=0.02)                # from the track: 1.6 km

        self.assertTrue(self.sync()["nothing_new"])
        status = self.owner.get("/api/v1/health/runkeeper-sync").get_json()
        self.assertEqual((status["not_loaded"], status["workouts"]["count"]), (0, 3))

    def test_a_changed_activity_updates_its_numbers_but_keeps_what_was_edited(self):
        self.land([self.activity("a1", "2019-05-04 16:35:09")], RUN1)
        self.sync()
        [run] = self.owner.get("/api/v1/today?date=2019-05-04").get_json()["workouts"]
        r = self.owner.put(f"/api/v1/health/workouts/{run['workout_id']}", json={"activity": "Long run", "note": "Felt great"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.land([self.activity("a1", "2019-05-04 16:35:09", duration="30:00")], RUN1 + timedelta(days=1))
        self.assertEqual(self.sync()["workouts_updated"], 1)
        [run] = self.owner.get("/api/v1/today?date=2019-05-04").get_json()["workouts"]
        self.assertEqual((run["activity"], run["note"], float(run["minutes"])), ("Long run", "Felt great", 30.0))

    def test_a_long_history_loads_in_batches_and_reload_reads_it_again(self):
        self.land([self.activity(f"a{i:03}", f"2018-01-{i % 28 + 1:02} 07:00:00", track=False) for i in range(7)], RUN1)
        report = rk.sync(batch=3)
        self.assertEqual((report["workouts_added"], report["batches"]), (7, 3))
        again = self.sync(reload=True)
        self.assertEqual((again["workouts_added"], again["workouts_updated"]), (0, 7))
