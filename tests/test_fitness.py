"""Fitness: one base table for every workout, a fact table per kind, and the views everything reads.
All data here is made up."""
import io
import unittest

import psycopg

from tests.support import PG, admin_client, fresh_database, needs_database
from tests.test_habits import upgrade_from
from tests.test_workout_routes import TRACK, gpx
from app import app
from virtuwill import db


@needs_database
class FitnessTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)

    def log(self, **body):
        r = self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-02"} | body)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json

    def test_activity_types_by_kind(self):
        types = {t["activity_type"]: t for t in self.owner.get("/api/v1/health/activity-types").json}
        self.assertEqual({t: types[t]["category"] for t in ("run", "dog_walk", "bike", "strength", "hiit", "yoga", "other")},
                         {"run": "cardio", "dog_walk": "cardio", "bike": "cardio", "strength": "strength", "hiit": "hiit",
                          "yoga": "mobility", "other": "other"})
        self.assertFalse(types["dog_walk"]["counts_toward_goal"])
        self.assertEqual(types["strength"]["category_label"], "Lifting")

    def test_a_workout_by_its_activity_with_its_cardio_facts(self):
        run = self.log(activity_type="run", minutes=30, distance=5, distance_unit="km", calories=400, avg_heart_rate=150,
                       title="Tempo")
        self.assertEqual((run["name"], run["activity_label"], run["category"], float(run["distance_km"]), run["calories"]),
                         ("Tempo", "Run", "cardio", 5.0, 400))
        self.assertEqual(self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-02",
                                                                          "activity_type": "kayak"}).status_code, 400)
        self.assertEqual(self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-02", "activity_type": "run",
                                                                          "avg_heart_rate": 900}).status_code, 400)
        # Without an activity it's "other"; the older {workout_type, activity} still reads.
        self.assertEqual(self.log(minutes=10)["activity_type"], "other")
        self.assertEqual(self.log(workout_type="Cardio", activity="Evening ride")["activity_type"], "bike")

    def test_the_database_keeps_each_kind_s_facts_with_its_kind(self):
        lift = self.log(activity_type="strength", lifts=[{"lift": "Deadlift", "sets": 3, "reps": 5, "weight_lb": 225}])
        self.assertEqual(lift["lifts"], [{"lift": "Deadlift", "sets": 3, "reps": 5, "weight_lb": 225}])
        with psycopg.connect(PG, autocommit=True) as conn:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):        # a cardio row on a lifting session
                conn.execute("INSERT INTO fitness.workout_cardio (workout_id, distance) VALUES (%s, 3)", (lift["workout_id"],))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute("INSERT INTO fitness.workouts (workout_date, activity_type, source) VALUES ('2026-03-02', 'kayak', 'manual')")

    def test_changing_the_activity_keeps_or_drops_facts_by_kind(self):
        run = self.log(activity_type="run", distance=3.1)
        wid = run["workout_id"]
        upload = self.owner.post(f"/api/v1/health/workouts/{wid}/route", content_type="multipart/form-data",
                                 data={"file": (io.BytesIO(gpx(TRACK)), "run.gpx")})
        self.assertEqual(upload.status_code, 201)
        # Run → walk: still cardio, so the distance and the route stay.
        walk = self.owner.put(f"/api/v1/health/workouts/{wid}", json={"activity_type": "walk"}).json
        self.assertEqual((walk["activity_type"], float(walk["distance"]), walk["has_route"]), ("walk", 3.1, True))
        # Walk → yoga: the cardio facts and the route go.
        yoga = self.owner.put(f"/api/v1/health/workouts/{wid}", json={"activity_type": "yoga"}).json
        self.assertEqual((yoga["category"], yoga["distance"], yoga["has_route"]), ("mobility", None, False))
        self.assertIsNone(db.one("SELECT * FROM fitness.workout_routes WHERE workout_id = %s", wid))

    def test_the_day_counts_by_kind_and_a_dog_walk_never_qualifies(self):
        self.log(activity_type="run", minutes=30, distance=3, distance_unit="mi")
        self.log(activity_type="strength", minutes=20)
        self.log(activity_type="dog_walk", minutes=40, distance=2)
        day = self.owner.get("/api/v1/health/days?from=2026-03-02&to=2026-03-02").json[0]
        self.assertEqual((day["workout_minutes"], day["dog_walk_minutes"], day["cardio_minutes"], day["strength_minutes"]),
                         (50, 40, 30, 20))
        self.assertAlmostEqual(float(day["run_km"]), 4.83, places=2)
        habits = {h["habit"]: h["done"] for h in self.owner.get("/api/v1/today?date=2026-03-02").json["habits"]}
        self.assertEqual((habits["run"], habits["lift"]), (True, True))

    def test_the_rules_that_read_older_descriptions(self):
        cases = {("Cardio", "Running"): "run", (None, "Brunch with friends"): "other", ("Strength", "Running"): "strength",
                 (None, "Mountain Biking"): "bike", (None, "Strength Training"): "strength", (None, "Circuit Training"): "hiit",
                 (None, "Walking"): "walk", ("Dog walk", ""): "dog_walk", (None, "Walk the dog"): "dog_walk",
                 ("Mobility / recovery", "Yoga flow"): "yoga", ("Mobility / recovery", ""): "mobility", ("Other", "Bike"): "bike",
                 ("Cardio", ""): "cardio", (None, "Stairmaster / Stepwell"): "cardio", (None, "Rowing"): "row",
                 (None, "Arrow practice"): "other", (None, "Other"): "other"}
        got = {k: db.one("SELECT fitness.activity_type_for(%s, %s) AS t", *k)["t"] for k in cases}
        self.assertEqual(got, cases)


@needs_database
class FitnessUpgradeTests(unittest.TestCase):
    def test_journal_workouts_move_with_their_ids_and_facts(self):
        def old_rows(conn):
            conn.execute("""INSERT INTO journal.workouts (workout_id, workout_date, workout_type, activity, minutes, note,
                                                          distance, distance_unit, source, source_ref, details)
                            OVERRIDING SYSTEM VALUE VALUES
                (101, '2026-03-01', 'Cardio', 'Running', 28, 'easy', 3.1, 'mi', 'runkeeper', 'rk1',
                 '{"runkeeper": {"type": "Running", "calories_burned": 350, "average_heart_rate_bpm": 0}}'),
                (102, '2026-03-01', 'Strength', '', 45, 'after brunch', NULL, 'mi', 'manual', NULL, '{}'),
                (103, '2026-03-02', 'HIIT', '', NULL, '', NULL, 'mi', 'manual', NULL, '{}'),
                (104, '2026-03-02', 'Dog walk', '', 30, '', 1.5, 'mi', 'manual', NULL, '{}'),
                (105, '2026-03-03', 'Other', 'Bike', 40, '', NULL, 'mi', 'manual', NULL, '{}')""")
            conn.execute("""INSERT INTO journal.workout_routes (workout_id, points, distance_km, started_at, file_name)
                            VALUES (101, '[[40, -80], [40.01, -80]]', 1.1, '2026-03-01T13:00:00Z', 'run.gpx')""")
            conn.execute("INSERT INTO journal.workout_lifts VALUES (102, 0, 'Deadlift', 3), (102, 1, 'Bench press', 4)")
            conn.execute("INSERT INTO journal.workout_circuits (workout_id, rounds, exercises_per_round, work_seconds) VALUES (103, 3, 4, 40)")
        upgrade_from("112", old_rows)
        rows = {r["workout_id"]: r for r in db.all("SELECT * FROM fitness.workout_sessions ORDER BY workout_id")}
        self.assertEqual({i: rows[i]["activity_type"] for i in rows}, {101: "run", 102: "strength", 103: "hiit", 104: "dog_walk",
                                                                       105: "bike"})
        run = rows[101]
        self.assertEqual((float(run["distance"]), run["calories"], run["avg_heart_rate"], run["has_route"], run["title"],
                          run["source_ref"]), (3.1, 350, None, True, "Running", "rk1"))
        self.assertEqual(run["started_at"].isoformat(), "2026-03-01T13:00:00+00:00")
        self.assertEqual([x["lift"] for x in rows[102]["lifts"]], ["Deadlift", "Bench press"])
        self.assertEqual(rows[103]["circuit"]["total_seconds"], 3 * 4 * 40)
        self.assertEqual(float(rows[104]["distance"]), 1.5)
        self.assertIsNone(db.one("SELECT to_regclass('journal.workouts') AS t")["t"])
        # New workouts number on from the moved ones; the moved ones tick their habits.
        new = admin_client(app).post("/api/v1/health/workouts", json={"workout_date": "2026-03-04", "activity_type": "run"}).json
        self.assertGreater(new["workout_id"], 105)
        habits = {h["habit"]: h["done"] for h in admin_client(app).get("/api/v1/today?date=2026-03-01").json["habits"]}
        self.assertEqual((habits["run"], habits["lift"]), (True, True))


if __name__ == "__main__":
    unittest.main()
