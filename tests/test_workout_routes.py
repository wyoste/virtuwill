"""Workout distance and routes from GPX/TCX files. The tracks here are made up."""
import io
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import routes


def gpx(points, doctype=""):
    pts = "".join(f'<trkpt lat="{lat}" lon="{lng}"><ele>{ele}</ele><time>{t}</time></trkpt>' for lat, lng, ele, t in points)
    return (f'<?xml version="1.0"?>{doctype}<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>{pts}</trkseg></trk></gpx>').encode()


# About 1.6 km due north in four steps, 10 minutes, climbing 12 m.
TRACK = [(40.0000, -80.0, 100, "2026-03-01T07:00:00Z"), (40.0036, -80.0, 104, "2026-03-01T07:02:30Z"),
         (40.0072, -80.0, 102, "2026-03-01T07:05:00Z"), (40.0108, -80.0, 108, "2026-03-01T07:07:30Z"),
         (40.0144, -80.0, 112, "2026-03-01T07:10:00Z")]

TCX = b"""<?xml version="1.0"?><TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities><Activity>
<Lap><Track><Trackpoint><Time>2026-03-02T07:00:00Z</Time><Position><LatitudeDegrees>51.0</LatitudeDegrees><LongitudeDegrees>0.0</LongitudeDegrees></Position>
<AltitudeMeters>10</AltitudeMeters></Trackpoint><Trackpoint><Time>2026-03-02T07:20:00Z</Time><Position><LatitudeDegrees>51.09</LatitudeDegrees>
<LongitudeDegrees>0.0</LongitudeDegrees></Position><AltitudeMeters>30</AltitudeMeters></Trackpoint></Track></Lap></Activity></Activities></TrainingCenterDatabase>"""


class ParseTests(unittest.TestCase):
    def test_gpx_and_tcx(self):
        s = routes.summarise(routes.parse(gpx(TRACK)))
        self.assertAlmostEqual(s["distance_km"], 1.60, delta=0.02)
        self.assertEqual((s["elevation_gain_m"], len(s["points"])), (14.0, 5))
        self.assertEqual((s["ended_at"] - s["started_at"]).total_seconds(), 600)
        t = routes.summarise(routes.parse(TCX))
        self.assertAlmostEqual(t["distance_km"], 10.0, delta=0.1)
        self.assertEqual(t["elevation_gain_m"], 20.0)

    def test_long_tracks_are_thinned_for_drawing_but_measured_in_full(self):
        many = [(40 + i * 0.0001, -80.0, None, None) for i in range(5000)]
        s = routes.summarise(many)
        self.assertLessEqual(len(s["points"]), routes.MAX_POINTS + 1)
        self.assertEqual(s["points"][-1], [round(many[-1][0], 6), -80.0])
        self.assertAlmostEqual(s["distance_km"], 55.6, delta=0.2)

    def test_refused_files(self):
        for content, message in ((b"not xml", "couldn't be read"), (gpx(TRACK[:1]), "at least two"),
                                 (gpx(TRACK, '<!DOCTYPE gpx [<!ENTITY a "b">]>'), "plain GPX")):
            with self.assertRaises(routes.RouteError) as caught:
                routes.parse(content)
            self.assertIn(message, str(caught.exception))


@needs_database
class WorkoutRouteTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        self.visitor = app.test_client()

    def upload(self, workout_id, content, name="run.gpx"):
        return self.owner.post(f"/api/v1/health/workouts/{workout_id}/route", content_type="multipart/form-data",
                               data={"file": (io.BytesIO(content), name)})

    def test_distance_and_a_route_that_fills_in_the_blanks(self):
        typed = self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-01", "workout_type": "Cardio",
                                                                 "activity": "Run", "distance": 3.1, "distance_unit": "mi",
                                                                 "minutes": 28}).json
        self.assertEqual((typed["distance"], typed["distance_unit"], typed["has_route"]), (3.1, "mi", False))
        self.assertEqual(self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-01",
                                                                          "distance_unit": "yards"}).status_code, 400)

        run = self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-03-01", "workout_type": "Cardio",
                                                               "activity": "Run", "distance_unit": "km"}).json
        self.assertEqual(self.visitor.post(f"/api/v1/health/workouts/{run['workout_id']}/route").status_code, 401)
        self.assertEqual(self.upload(run["workout_id"], b"<nope").status_code, 400)
        made = self.upload(run["workout_id"], gpx(TRACK))
        self.assertEqual(made.status_code, 201, made.json)
        listed = next(w for w in self.owner.get("/api/v1/health/workouts?date=2026-03-01").json if w["workout_id"] == run["workout_id"])
        self.assertTrue(listed["has_route"])
        self.assertAlmostEqual(listed["distance"], 1.6, delta=0.02)            # filled in, in the workout's unit
        self.assertEqual(listed["minutes"], 10)
        route = self.owner.get(f"/api/v1/health/workouts/{run['workout_id']}/route").json
        self.assertEqual(route["points"][0], [40.0, -80.0])
        self.assertEqual(route["file_name"], "run.gpx")

        # A typed distance isn't overwritten; a new file replaces the route.
        self.assertEqual(self.upload(typed["workout_id"], TCX, name="ride.tcx").status_code, 201)
        again = next(w for w in self.owner.get("/api/v1/health/workouts?date=2026-03-01").json if w["workout_id"] == typed["workout_id"])
        self.assertEqual((again["distance"], again["minutes"]), (3.1, 28))
        self.assertEqual(self.owner.delete(f"/api/v1/health/workouts/{typed['workout_id']}/route").status_code, 200)
        self.assertEqual(self.owner.get(f"/api/v1/health/workouts/{typed['workout_id']}/route").status_code, 404)
        self.assertEqual(self.owner.get("/api/v1/health/workouts/999999/route").status_code, 404)


if __name__ == "__main__":
    unittest.main()
