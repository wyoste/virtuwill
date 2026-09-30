"""Workout types: lifts and sets for strength, circuits for HIIT, distance only for cardio and dog walks.
All data here is made up."""
import io
import unittest

from tests.support import admin_client, fresh_database, needs_database
from tests.test_workout_routes import TRACK, gpx
from app import app


@needs_database
class WorkoutKindTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)

    def log(self, **body):
        return self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-01"} | body)

    def test_strength_keeps_its_lifts_and_sets_in_order(self):
        catalog = self.owner.get("/api/v1/health/lifts").json
        self.assertIn({"lift": "Bench press", "muscle_group": "Chest"}, catalog)
        r = self.log(workout_type="Strength", minutes=50,
                     lifts=[{"lift": "bench PRESS", "sets": "4"}, {"lift": "Back squat", "sets": 5}, {"lift": "Sled push", "sets": 3}])
        self.assertEqual(r.status_code, 201, r.json)
        # Known lifts keep the catalogue's spelling; a new one is added to the catalogue.
        self.assertEqual(r.json["lifts"], [{"lift": "Bench press", "sets": 4}, {"lift": "Back squat", "sets": 5},
                                           {"lift": "Sled push", "sets": 3}])
        self.assertIn("Sled push", [x["lift"] for x in self.owner.get("/api/v1/health/lifts").json])
        wid = r.json["workout_id"]
        r = self.owner.put(f"/api/v1/health/workouts/{wid}", json={"lifts": [{"lift": "Deadlift", "sets": 3}]})
        self.assertEqual(r.json["lifts"], [{"lift": "Deadlift", "sets": 3}])
        # A save that doesn't mention lifts leaves them alone.
        self.assertEqual(self.owner.put(f"/api/v1/health/workouts/{wid}", json={"note": "felt strong"}).json["lifts"],
                         [{"lift": "Deadlift", "sets": 3}])

    def test_refused_lifts(self):
        for lifts, message in (([{"lift": "Deadlift", "sets": 0}], "sets"), ([{"lift": "Deadlift", "sets": 2.5}], "sets"),
                               ([{"lift": "", "sets": 3}], "name"), ([{"lift": "Deadlift", "sets": 3}, {"lift": "deadlift", "sets": 2}], "twice"),
                               ("Deadlift", "list")):
            r = self.log(workout_type="Strength", lifts=lifts)
            self.assertEqual(r.status_code, 400, lifts)
            self.assertIn(message, r.json["error"])

    def test_hiit_circuit_and_its_length(self):
        circuit = {"rounds": 4, "exercises_per_round": 5, "work_seconds": 40, "exercise_rest_seconds": 20, "round_rest_seconds": 60}
        r = self.log(workout_type="HIIT", circuit=circuit)
        self.assertEqual(r.status_code, 201, r.json)
        # 4 × (5 × 40 + 4 × 20) + 3 × 60 = 1,300 s, which fills the blank minutes.
        self.assertEqual(r.json["circuit"], circuit | {"total_seconds": 1300})
        self.assertEqual(float(r.json["minutes"]), 21.7)
        # Minutes typed in are kept.
        self.assertEqual(float(self.log(workout_type="HIIT", minutes=30, circuit=circuit).json["minutes"]), 30)
        for bad in ({**circuit, "rounds": 0}, {**circuit, "work_seconds": None}, {**circuit, "round_rest_seconds": -5}):
            self.assertEqual(self.log(workout_type="HIIT", circuit=bad).status_code, 400)
        self.assertIn("HIIT", [w["workout_type"] for w in self.owner.get("/api/v1/health/workouts").json])

    def test_distance_and_routes_only_for_cardio_and_dog_walks(self):
        self.assertEqual(float(self.log(workout_type="Dog walk", distance=1.2).json["distance"]), 1.2)
        self.assertIsNone(self.log(workout_type="Strength", distance=3).json["distance"])
        run = self.log(workout_type="Cardio", activity="Run", distance=3.1).json
        wid = run["workout_id"]
        upload = lambda: self.owner.post(f"/api/v1/health/workouts/{wid}/route", content_type="multipart/form-data",
                                         data={"file": (io.BytesIO(gpx(TRACK)), "run.gpx")})
        self.assertEqual(upload().status_code, 201)
        # Changing the type to one without distance drops the distance, the route, and anything of the old type.
        changed = self.owner.put(f"/api/v1/health/workouts/{wid}", json={"workout_type": "Strength",
                                                                        "lifts": [{"lift": "Deadlift", "sets": 3}]}).json
        self.assertEqual((changed["distance"], changed["has_route"], changed["lifts"]), (None, False, [{"lift": "Deadlift", "sets": 3}]))
        self.assertEqual(upload().status_code, 400)
        back = self.owner.put(f"/api/v1/health/workouts/{wid}", json={"workout_type": "Cardio"}).json
        self.assertEqual(back["lifts"], [])

    def test_today_hands_the_editor_the_whole_workout(self):
        self.log(workout_type="Strength", lifts=[{"lift": "Pull-up", "sets": 3}])
        day = self.owner.get("/api/v1/today?date=2026-03-01").json
        self.assertEqual(day["workouts"][0]["lifts"], [{"lift": "Pull-up", "sets": 3}])


if __name__ == "__main__":
    unittest.main()
