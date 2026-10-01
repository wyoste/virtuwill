"""RunKeeper: the app's load from the Lakebase synced tables into the workouts. The synced tables are stood in for by
plain tables shaped like the bronze ones the notebooks land; the activities and tracks are made up."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg

from tests.support import PG, admin_client, fresh_database, needs_database
from tests.test_workout_routes import TRACK, gpx
from app import app
from virtuwill import runkeeper_synced as rk

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
        # A stopwatch left running: the time is left blank, not 25 hours.
        self.assertIsNone(rk.workout({"activity_id": "x", "activity_date": "2026-06-23 07:41:18", "duration": "25:51:49"})["minutes"])

    def test_the_loop_only_starts_when_asked(self):
        with mock.patch.dict(os.environ, {"RUNKEEPER_SYNC_MINUTES": "0"}), mock.patch.object(rk.threading, "Thread") as thread:
            rk.start()
        thread.assert_not_called()


ACTIVITY_TABLE = """
CREATE TABLE bronze.runkeeper_activities (
    activity_id TEXT PRIMARY KEY, activity_date TEXT, type TEXT, route_name TEXT, distance TEXT, distance_unit TEXT,
    duration TEXT, average_pace TEXT, average_speed TEXT, speed_unit TEXT, calories_burned TEXT, climb TEXT, climb_unit TEXT,
    average_heart_rate_bpm TEXT, friends_tagged TEXT, notes TEXT, gpx_file TEXT, _extra TEXT,
    _source_file TEXT, _file_modified_at TIMESTAMPTZ, _row_hash TEXT, _ingested_at TIMESTAMPTZ);
"""
GPX_TABLE = """
CREATE TABLE bronze.runkeeper_gpx (
    gpx_file TEXT PRIMARY KEY, activity_start TEXT, track_started_at TEXT, points BIGINT, gpx TEXT, gpx_bytes BIGINT,
    _source_file TEXT, _file_modified_at TIMESTAMPTZ, _row_hash TEXT, _ingested_at TIMESTAMPTZ);
"""
RUN1 = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


def start_in_name(name):
    """As the GPX notebook writes activity_start: '2019-05-04-163509.gpx' → '2019-05-04 16:35:09'."""
    d, t = name[:10], name[11:17]
    return f"{d} {t[:2]}:{t[2:4]}:{t[4:]}"


@needs_database
class SyncTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.sql("DROP SCHEMA IF EXISTS bronze CASCADE; CREATE SCHEMA bronze;" + ACTIVITY_TABLE + GPX_TABLE)
        self.addCleanup(self.sql, "DROP SCHEMA IF EXISTS bronze CASCADE")
        self.owner = admin_client(app)

    def sql(self, text):
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute(text)

    def land(self, table, key, rows, at):
        """Rows as a notebook MERGEs them: every row of one run stamped with the same _ingested_at."""
        with psycopg.connect(PG, autocommit=True) as conn:
            for r in rows:
                r = {**r, "_ingested_at": at}
                conn.execute(f"""INSERT INTO bronze.{table} ({', '.join(r)}) VALUES ({', '.join(['%s'] * len(r))})
                                 ON CONFLICT ({key}) DO UPDATE SET {', '.join(f'{k} = EXCLUDED.{k}' for k in r)}""",
                             list(r.values()))

    def activities(self, rows, at):
        self.land("runkeeper_activities", "activity_id", rows, at)

    def tracks(self, names, at):
        self.land("runkeeper_gpx", "gpx_file", [{"gpx_file": n, "activity_start": start_in_name(n),
                                                 "gpx": gpx(TRACK).decode()} for n in names], at)

    def activity(self, aid, when, kind="Running", distance="3.10", duration="28:30", gpx_file=None, notes=None):
        return {"activity_id": aid, "activity_date": when, "type": kind, "distance": distance, "distance_unit": "mi",
                "duration": duration, "notes": notes, "gpx_file": gpx_file}

    def sync(self, **body):
        r = self.owner.post("/api/v1/health/runkeeper-sync", json=body)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def day(self, when):
        return self.owner.get(f"/api/v1/today?date={when}").get_json()["workouts"]

    def test_without_the_activities_table_it_says_why(self):
        self.sql("DROP TABLE bronze.runkeeper_activities")
        r = self.owner.post("/api/v1/health/runkeeper-sync", json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("bronze.runkeeper_activities", r.get_json()["error"])
        self.assertFalse(self.owner.get("/api/v1/health/runkeeper-sync").get_json()["available"])

    def test_activities_land_on_their_dates_with_their_tracks(self):
        self.tracks(["2019-05-04-163509.gpx", "2019-05-07-180000.gpx", "2018-01-01-000000.gpx"], RUN1)
        self.activities([self.activity("a1", "2019-05-04 16:35:09", gpx_file="2019-05-04-163509.gpx", notes="Easy"),
                         self.activity("a2", "2019-05-06 06:10:00", "Strength Training", "0.00", "45:00"),
                         # names no file: its track is the one named for its start
                         self.activity("a3", "2019-05-07 18:00:00", "Cycling", distance="", duration="1:05:12")], RUN1)
        preview = self.sync(dry_run=True)
        self.assertEqual((preview["workouts_added"], preview["previewed"]), (3, "activities"))
        status = self.owner.get("/api/v1/health/runkeeper-sync").get_json()
        self.assertEqual((status["activities"]["not_loaded"], status["gpx"]["not_loaded"]), (3, 3))

        report = self.sync()
        self.assertEqual((report["workouts_added"], report["routes"], report["tracks_without_activity"]), (3, 2, 1))
        [run] = self.day("2019-05-04")
        self.assertEqual((run["activity"], run["workout_type"], float(run["distance"]), float(run["minutes"]), run["note"],
                          run["has_route"], run["source"]), ("Running", "Cardio", 3.1, 28.5, "Easy", True, "runkeeper"))
        route = self.owner.get(f"/api/v1/health/workouts/{run['workout_id']}/route").get_json()
        self.assertEqual((len(route["points"]), route["file_name"]), (5, "2019-05-04-163509.gpx"))
        [lift] = self.day("2019-05-06")
        self.assertEqual((lift["workout_type"], lift["distance"], lift["has_route"]), ("Strength", None, False))
        [ride] = self.day("2019-05-07")
        self.assertAlmostEqual(float(ride["distance"]), 1.0, delta=0.02)                # from the track: 1.6 km

        self.assertTrue(self.sync()["nothing_new"])
        status = self.owner.get("/api/v1/health/runkeeper-sync").get_json()
        self.assertEqual((status["activities"]["not_loaded"], status["gpx"]["not_loaded"], status["workouts"]["count"],
                          status["workouts"]["with_route"]), (0, 0, 3, 2))

    def test_a_track_that_lands_after_its_activity_is_added_to_the_workout(self):
        self.activities([self.activity("a1", "2019-05-04 16:35:09", gpx_file="2019-05-04-163509.gpx")], RUN1)
        self.assertEqual(self.sync()["routes"], 0)
        self.assertFalse(self.day("2019-05-04")[0]["has_route"])
        self.tracks(["2019-05-04-163509.gpx"], RUN1 + timedelta(hours=1))
        report = self.sync()
        self.assertEqual((report["routes"], report["workouts_added"], report["workouts_updated"]), (1, 0, 0))
        self.assertTrue(self.day("2019-05-04")[0]["has_route"])

    def test_without_the_track_table_workouts_still_load(self):
        self.sql("DROP TABLE bronze.runkeeper_gpx")
        self.activities([self.activity("a1", "2019-05-04 16:35:09", gpx_file="2019-05-04-163509.gpx")], RUN1)
        report = self.sync()
        self.assertEqual(report["workouts_added"], 1)
        self.assertIn("bronze.runkeeper_gpx", report["gpx"])
        self.assertFalse(self.owner.get("/api/v1/health/runkeeper-sync").get_json()["gpx"]["available"])
        # Once it's synced, the tracks are read from the start.
        self.sql("CREATE SCHEMA IF NOT EXISTS bronze;" + GPX_TABLE)
        self.tracks(["2019-05-04-163509.gpx"], RUN1)
        self.assertEqual(self.sync()["routes"], 1)

    def test_a_changed_activity_updates_its_numbers_but_keeps_what_was_edited(self):
        self.activities([self.activity("a1", "2019-05-04 16:35:09")], RUN1)
        self.sync()
        [run] = self.day("2019-05-04")
        r = self.owner.put(f"/api/v1/health/workouts/{run['workout_id']}", json={"activity": "Long run", "note": "Felt great"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.activities([self.activity("a1", "2019-05-04 16:35:09", duration="30:00")], RUN1 + timedelta(days=1))
        self.assertEqual(self.sync()["workouts_updated"], 1)
        [run] = self.day("2019-05-04")
        self.assertEqual((run["activity"], run["note"], float(run["minutes"])), ("Long run", "Felt great", 30.0))

    def test_a_long_history_loads_in_batches_and_reload_reads_it_again(self):
        self.activities([self.activity(f"a{i:03}", f"2018-01-{i % 28 + 1:02} 07:00:00") for i in range(7)], RUN1)
        report = rk.sync(batch=3)
        self.assertEqual((report["workouts_added"], report["batches"]), (7, 3))
        again = self.sync(reload=True)
        self.assertEqual((again["workouts_added"], again["workouts_updated"]), (0, 7))
