"""Storage layer tests.

Local-file tests always run. Lakebase tests run against any Postgres when
VIRTUWILL_TEST_PG is set to a libpq connection string, e.g.
  VIRTUWILL_TEST_PG="host=localhost port=5432 dbname=lake user=app password=pw sslmode=disable"
"""
import io
import json
import os
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import storage
from app import app
from trackers import TrackerStore

PG = os.environ.get("VIRTUWILL_TEST_PG")


def pg_env():
    names = {"dbname": "PGDATABASE"}
    return {names.get(k, "PG" + k.upper()): v for k, v in (part.split("=", 1) for part in PG.split())}


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(storage, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        storage.use(storage.LocalBackend(self.temp.name))
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_defaults_and_atomic_file_writes(self):
        self.assertEqual(storage.load("music_catalog"), {"tracks": []})
        storage.save("blog", [{"id": "1"}])
        self.assertEqual(json.loads((Path(self.temp.name) / "blog.json").read_text()), [{"id": "1"}])
        self.assertFalse(list(Path(self.temp.name).glob("*.tmp")))

    def test_page_data_requires_admin_and_known_name(self):
        self.assertEqual(self.client.get("/api/data/travel_pins").json, {"data": None})
        self.assertEqual(self.client.put("/api/data/travel_pins", json={"data": "[]"}).status_code, 401)
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
        self.assertEqual(self.client.put("/api/data/secrets", json={"data": "x"}).status_code, 404)
        self.assertEqual(self.client.put("/api/data/travel_pins", json={"data": 5}).status_code, 400)
        self.assertEqual(self.client.put("/api/data/travel_pins", json={"data": '[{"id":1}]'}).status_code, 200)
        self.assertEqual(self.client.get("/api/data/travel_pins").json, {"data": '[{"id":1}]'})


@unittest.skipUnless(PG, "set VIRTUWILL_TEST_PG to run Lakebase tests")
class LakebaseStorageTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, pg_env())
        env.start()
        self.addCleanup(env.stop)
        self.backend = storage.LakebaseBackend()
        with self.backend.connect() as db:
            db.execute(f"TRUNCATE {storage.SCHEMA}.collections, {storage.SCHEMA}.media, {storage.SCHEMA}.trackers, "
                       "journal.entries, journal.entry_tags, journal.habit_logs, journal.meals, journal.workouts, "
                       "health.body_measurements, health.alcohol, health.daily_logs, health.foods, health.profile")
            db.execute("DELETE FROM health.goals WHERE metric IN ('weight', 'bmi', 'daily_calories')")
            db.execute("UPDATE health.goals SET target = 5 WHERE metric = 'workout_days_per_week'")
        storage.use(self.backend)
        self.addCleanup(storage.use, storage.LocalBackend())
        self.addCleanup(lambda: self.backend._pool.close())
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True

    def test_collections_seed_from_repo_then_persist(self):
        seeded = storage.load("garden")
        self.assertEqual(seeded, json.loads((storage.DATA_DIR / "garden.json").read_text()))
        storage.save("garden", {"beds": [{"id": "bed-1"}]})
        storage.save("garden", {"beds": [{"id": "bed-2"}]})
        self.assertEqual(storage.load("garden"), {"beds": [{"id": "bed-2"}]})
        with self.backend.connect() as db:
            self.assertEqual(db.execute(f"SELECT revision FROM {storage.SCHEMA}.collections WHERE name = 'garden'").fetchone()["revision"], 2)

    def test_app_routes_write_to_database(self):
        before = self.client.get("/api/blog").json
        r = self.client.post("/api/blog", json={"title": "Hello", "body": "World"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(storage.load("blog")), len(before) + 1)
        self.client.put("/api/data/travel_visited", json={"data": '{"countries":["US"],"states":[]}'})
        self.assertEqual(storage.load("travel_visited"), '{"countries":["US"],"states":[]}')

    def test_uploads_survive_losing_the_local_disk(self):
        r = self.client.post("/api/garden/photo", data={"files": (io.BytesIO(b"jpegbytes"), "p.jpg"), "caption": "c"},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.data)
        photo = r.json["added"][0]
        path = storage.STATIC_DIR / photo["url"].removeprefix("/static/")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        path.unlink()  # simulate a redeploy
        served = self.client.get(photo["url"])
        self.assertEqual((served.status_code, served.data), (200, b"jpegbytes"))
        served.close()
        self.assertEqual(storage.restore_media(), 1)
        self.assertEqual(path.read_bytes(), b"jpegbytes")
        self.client.delete("/api/garden/photo/" + photo["id"])
        self.assertFalse(path.exists())
        self.assertIsNone(storage.get_media(photo["url"].removeprefix("/static/")))

    def restart(self):
        self.backend._pool.close()
        self.backend = storage.LakebaseBackend()
        storage.use(self.backend)

    def rows(self, sql, *args):
        with self.backend.connect() as db:
            return db.execute(sql, args).fetchall()

    def test_journal_moves_into_date_grain_schema_once(self):
        with self.backend.connect() as db:
            db.execute("TRUNCATE journal.entries CASCADE")
            db.execute("TRUNCATE journal.meals")
            db.execute(f"DELETE FROM {storage.SCHEMA}.migrations WHERE name = 'journal_schema_v1'")
        self.restart()
        original = json.loads((storage.DATA_DIR / "journal_entries.json").read_text())
        moved = storage.journal_list()
        self.assertEqual([e["id"] for e in moved], [e["id"] for e in original])
        for before in original:
            after = next(e for e in moved if e["id"] == before["id"])
            for key in ("date", "quote", "quoteAuthor", "freeWrite", "source"):
                self.assertEqual(after[key], before[key], key)
            self.assertEqual(after["meals"], {k: (before["meals"].get(k) or "").strip() for k in "BLD"})
            self.assertEqual(after["tags"], before.get("tags") or [])
            self.assertEqual(after["habits"], before.get("habits") or {})
            self.assertEqual(after["accounts"], before.get("accounts") or [])
            self.assertEqual(storage.lakebase_model().timestamp(after["createdAt"]), storage.lakebase_model().timestamp(before["createdAt"]))
        meal_count = sum(1 for e in original for k in "BLD" if (e["meals"].get(k) or "").strip())
        self.assertEqual(len(self.rows("SELECT 1 FROM journal.meals WHERE source = 'journal'")), meal_count)
        self.assertEqual(len(self.rows("SELECT 1 FROM journal.habit_logs")), sum(len(e.get("habits") or {}) for e in original))
        # Deleting everything must not re-import on the next start.
        for entry in moved:
            self.assertTrue(storage.journal_delete(entry["id"]))
        self.assertEqual(self.rows("SELECT count(*) AS n FROM journal.meals WHERE source = 'journal'")[0]["n"], 0)
        self.restart()
        self.assertEqual(storage.journal_list(), [])

    def test_journal_routes_one_entry_per_date(self):
        with self.client.session_transaction() as session:
            session["journal_unlocked"] = True
        entry = {"id": "t1", "date": "2030-01-02", "quote": "q", "meals": {"B": "oats"}, "tags": ["b", "a"], "habits": {"run": True}}
        r = self.client.post("/api/journal/entry", json=entry)
        self.assertEqual((r.status_code, r.json["wasNew"]), (201, True))
        r = self.client.post("/api/journal/entry", json={**entry, "quote": "updated", "health": {"workouts": [{"minutes": 99}]}})
        self.assertEqual((r.status_code, r.json["wasNew"]), (200, False))
        self.assertEqual(self.client.post("/api/journal/entry", json={**entry, "id": "t2"}).status_code, 409)
        self.assertEqual(self.client.post("/api/journal/entry", json={**entry, "id": "t3", "date": "bad"}).status_code, 400)
        listed = self.client.get("/api/journal/entries").json
        self.assertEqual([e["id"] for e in listed], ["t1"])
        self.assertEqual((listed[0]["quote"], listed[0]["meals"], listed[0]["tags"]), ("updated", {"B": "oats", "L": "", "D": ""}, ["b", "a"]))
        self.assertEqual(listed[0]["health"]["workouts"], [])  # read-only field is never written back
        # Moving an entry to another date carries its meals, tags and habits.
        self.client.post("/api/journal/entry", json={**entry, "date": "2030-01-05"})
        self.assertEqual([r["meal_date"].isoformat() for r in self.rows("SELECT meal_date FROM journal.meals")], ["2030-01-05"])
        self.assertEqual([r["tag"] for r in self.rows("SELECT tag FROM journal.entry_tags WHERE entry_date = '2030-01-05' ORDER BY position")], ["b", "a"])
        self.assertTrue(self.client.get("/api/journal/check/2030-01-05").json["exists"])
        self.assertFalse(self.client.get("/api/journal/check/2030-01-02").json["exists"])
        self.assertFalse(self.client.get("/api/journal/check/not-a-date").json["exists"])
        self.assertEqual(self.client.delete("/api/journal/entry/t1").status_code, 200)
        self.assertEqual(self.client.delete("/api/journal/entry/t1").status_code, 404)
        self.assertEqual(self.rows("SELECT count(*) AS n FROM journal.habit_logs")[0]["n"], 0)

    def health_state(self, today, workouts, weights=None):
        """A Health tracker state in the real yoste-health-v1 shape."""
        day = lambda n: (today - timedelta(days=n)).isoformat()
        return {
            "version": 1,
            "settings": {"height": 71, "goal": 23, "target": 2200, "mode": "loss", "weekend": "5,6,0", "age": 33,
                         "coffee": "Black", "profileRevision": 1},
            "weights": weights if weights is not None else [
                {"date": day(2), "value": 188.0, "morning": True, "note": ""},
                {"date": day(1), "value": 187.0, "morning": True, "note": ""},
                {"date": day(0), "value": 186.0, "morning": True, "note": ""},
                {"date": day(0), "value": 188.4, "morning": False, "note": "afternoon reference"},
            ],
            "meals": [
                {"date": day(0), "slot": "Breakfast", "status": "Eaten", "name": "Oats", "note": "1 × bowl",
                 "k": 350, "p": 12, "c": 60, "fa": 6, "fi": 8},
                {"date": day(0), "slot": "Dinner", "status": "Planned", "name": "Tacos", "note": "",
                 "k": 700, "p": 30, "c": 70, "fa": 25, "fi": 12},
                {"date": "not a date", "slot": "Lunch", "status": "Eaten", "name": "lost", "k": 1},
            ],
            "beers": [{"date": day(0), "name": "IPA", "count": 3, "oz": 12, "abv": 6.5, "k": 600, "std": 3.9}],
            "complete": [day(1), day(1)],
            "foods": [{"id": "oats", "name": "Oats", "unit": "cup", "k": 300, "p": 10, "c": 54, "fa": 5, "fi": 8,
                       "status": "Label", "url": "https://example.com/oats"}],
            "workouts": workouts,
        }

    def test_health_tracker_feeds_shared_tables_and_views(self):
        from test_trackers import document
        today = self.rows("SELECT current_date AS d")[0]["d"]
        monday = today - timedelta(days=today.weekday())
        store = TrackerStore(self.backend)
        store.install("health", document("health"))
        workouts = [{"date": monday.isoformat(), "type": "Strength", "minutes": 30, "note": "squats"},
                    {"date": monday.isoformat(), "type": "Cardio", "minutes": 20, "note": ""},
                    {"date": today.isoformat(), "type": "Dog walk", "minutes": 60, "note": "with the dogs"}]
        self.assertTrue(store.save("health", self.health_state(today, workouts), 0))

        # Base tables carry the tracker's real fields.
        meals = {r["description"]: r for r in self.rows("SELECT * FROM journal.meals WHERE source = 'health_tracker'")}
        self.assertEqual((meals["Oats"]["slot"], meals["Oats"]["status"], float(meals["Oats"]["calories"]), float(meals["Oats"]["fiber_g"])),
                         ("breakfast", "eaten", 350, 8))
        self.assertEqual(meals["Tacos"]["status"], "planned")
        self.assertEqual([r["is_dog_walk"] for r in self.rows("SELECT is_dog_walk FROM journal.workouts ORDER BY workout_id")], [False, False, True])
        drink = self.rows("SELECT * FROM health.alcohol")[0]
        self.assertEqual((float(drink["containers"]), float(drink["standard_drinks"])), (3, 3.9))
        self.assertEqual([r["log_date"] for r in self.rows("SELECT log_date FROM health.daily_logs")], [today - timedelta(days=1)])
        self.assertEqual(self.rows("SELECT food_id FROM health.foods")[0]["food_id"], "oats")
        profile = self.rows("SELECT * FROM health.profile")[0]
        self.assertEqual((float(profile["height_in"]), float(profile["bmi_goal"]), profile["alcohol_days"]), (71, 23, [5, 6, 7]))

        # Views reproduce the tracker's own math.
        monday_row = self.rows("SELECT * FROM health.daily_activity WHERE day = %s", monday)[0]
        self.assertEqual((float(monday_row["workout_minutes"]), monday_row["qualifying_workout_day"]), (50, True))
        day = self.rows("SELECT * FROM health.daily_activity WHERE day = %s", today)[0]
        self.assertEqual(float(day["meal_calories"]), 350)          # planned meals are not eaten
        self.assertEqual(float(day["planned_calories"]), 700)
        self.assertEqual(float(day["total_calories"]), 950)         # eaten food + beer
        self.assertEqual(float(day["calorie_target"]), 2200)
        self.assertFalse(day["alcohol_within_rules"])               # 3 beers is at the boundary
        self.assertEqual((day["weigh_ins"], day["morning_weigh_ins"]), (2, 1))
        self.assertEqual((float(day["first_weight"]), float(day["weight"]), float(day["min_weight"]), float(day["max_weight"])),
                         (186.0, 188.4, 186.0, 188.4))
        if today != monday:
            self.assertEqual((float(day["dog_walk_minutes"]), day["qualifying_workout_day"]), (60, False))
        week = self.rows("SELECT * FROM health.weekly_workout_progress WHERE week_start = %s", monday)[0]
        self.assertEqual((week["qualifying_days"], float(week["target_days"]), week["goal_met"]), (1, 5, False))
        trend = self.rows("SELECT * FROM health.weight_trend ORDER BY day DESC")[0]
        self.assertEqual((float(trend["weight"]), float(trend["morning_avg_7d"])), (188.4, 187.0))  # afternoon reading excluded
        self.assertEqual(float(trend["bmi"]), round(187.0 * 0.45359237 / (71 * 0.0254) ** 2, 1))
        goals = {g["metric"]: g for g in self.rows("SELECT * FROM health.goal_progress")}
        self.assertEqual(float(goals["weight"]["target"]), round(23 * (71 * 0.0254) ** 2 / 0.45359237, 1))  # BMI 23 → 164.9 lb
        self.assertEqual((float(goals["bmi"]["target"]), float(goals["daily_calories"]["target"])), (23, 2200))
        self.assertEqual((float(goals["daily_calories"]["current_value"]), goals["daily_calories"]["met"]), (950, True))
        self.assertEqual((float(goals["beers_per_day"]["current_value"]), goals["beers_per_day"]["met"]), (3, False))
        self.assertEqual(float(goals["workout_days_per_week"]["current_value"]), 1)

        # Workouts and weigh-ins logged directly share the tables and survive tracker saves.
        r = self.client.post("/api/health/workouts", json={"date": today.isoformat(), "minutes": 45, "activity": "Bike"})
        self.assertEqual(r.status_code, 201, r.data)
        manual_id = r.json["id"]
        r = self.client.post("/api/health/weigh-ins", json={"date": today.isoformat(), "value": 185.5, "morning": True,
                                                            "at": f"{today.isoformat()}T23:30:00+00:00"})
        self.assertEqual(r.status_code, 201, r.data)
        weigh_in_id = r.json["id"]
        self.assertEqual(self.client.post("/api/health/weigh-ins", json={"date": today.isoformat(), "value": 5}).status_code, 400)
        self.assertTrue(store.save("health", self.health_state(today, workouts[:1]), 1))
        self.assertEqual(sorted(r["source"] for r in self.rows("SELECT source FROM journal.workouts")), ["health_tracker", "manual"])
        day = self.rows("SELECT * FROM health.daily_activity WHERE day = %s", today)[0]
        self.assertEqual((day["weigh_ins"], float(day["weight"])), (3, 185.5))  # the timed reading is the latest

        # The journal shows that day's workouts from the shared table.
        with self.client.session_transaction() as session:
            session["journal_unlocked"] = True
        self.client.post("/api/journal/entry", json={"id": "j1", "date": today.isoformat()})
        health = self.client.get("/api/journal/entries").json[0]["health"]
        self.assertEqual([w["activity"] for w in health["workouts"]], ["Bike"] if today != monday else ["Strength", "Bike"])
        self.assertEqual(health["totalCalories"], 950)

        dash = self.client.get("/api/health/dashboard").json
        self.assertTrue(dash["available"])
        self.assertEqual((dash["sync"]["workouts"], dash["sync"]["weights"], dash["sync"]["drinks"], dash["sync"]["foods"]), (1, 4, 1, 1))
        self.assertEqual(dash["sync"]["skipped"], {"meals": 1})
        self.assertEqual(dash["sync"]["fields"]["meals"], ["c", "date", "fa", "fi", "k", "name", "note", "p", "slot", "status"])
        self.assertEqual(len(dash["weighIns"]), 5)
        self.assertEqual(self.client.put("/api/health/goals/workout_days_per_week", json={"target": 4}).status_code, 200)
        self.assertEqual(self.client.put("/api/health/goals/nope", json={"target": 4}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/health/workouts/{manual_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/health/weigh-ins/{weigh_in_id}").status_code, 200)
        tracker_id = self.rows("SELECT workout_id FROM journal.workouts WHERE source = 'health_tracker'")[0]["workout_id"]
        self.assertEqual(self.client.delete(f"/api/health/workouts/{tracker_id}").status_code, 404)

    def test_connections_use_the_app_timezone(self):
        with mock.patch.dict(os.environ, {"APP_TIMEZONE": "America/Chicago"}):
            self.restart()
            self.assertEqual(self.rows("SHOW TimeZone")[0]["TimeZone"], "America/Chicago")

    def test_projection_errors_never_block_tracker_saves(self):
        from test_trackers import document
        store = TrackerStore(self.backend)
        store.install("health", document("health"))
        today = self.rows("SELECT current_date AS d")[0]["d"]
        with mock.patch.object(storage.lakebase_model(), "project_health", side_effect=RuntimeError("odd record")):
            self.assertTrue(store.save("health", self.health_state(today, []), 0))
        self.assertEqual(store.get("health")["revision"], 1)
        self.assertIn("odd record", self.client.get("/api/health/dashboard").json["sync"]["error"])

    def test_trackers_use_lakebase(self):
        from test_trackers import document, finance_state
        store = TrackerStore(self.backend)
        store.install("finance", document())
        with self.assertRaises(storage.AlreadyInstalled):
            store.install("finance", document())
        self.assertTrue(store.save("finance", finance_state(), 0))
        self.assertFalse(store.save("finance", finance_state(), 0))
        self.assertEqual(store.get("finance")["revision"], 1)

    def test_unreachable_database_returns_503(self):
        storage.use(storage.LakebaseBackend())
        with mock.patch.dict(os.environ, {"PGPORT": "1", "PGHOST": "127.0.0.1"}):
            r = self.client.get("/api/blog")
        self.assertEqual(r.status_code, 503)
        self.assertIn("unavailable", r.json["error"])


if __name__ == "__main__":
    unittest.main()
