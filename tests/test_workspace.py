"""The workspace API (/api/v1) and the page routes of the new site structure."""
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db, media


class PageRouteTests(unittest.TestCase):
    """Page shells render without a database: every public URL, the workspace and its sign-in."""

    def test_public_urls_serve_the_site_and_old_admin_moves_to_the_workspace(self):
        client = app.test_client()
        for path in ("/", "/music", "/music/some-song", "/projects", "/projects/dmp", "/writing", "/writing/abc",
                     "/garden", "/travel", "/resume", "/contact"):
            page = client.get(path)
            self.assertEqual(page.status_code, 200, path)
            self.assertIn('id="nl-projects"', page.text)
            self.assertNotIn('Admin Mode', page.text)
        self.assertEqual(client.get("/admin").headers["Location"], "/app")

    def test_workspace_asks_visitors_to_sign_in(self):
        visitor = app.test_client().get("/app/health")
        self.assertEqual(visitor.status_code, 200)
        self.assertIn("signedIn: false", visitor.text)
        self.assertNotIn("page-planner", visitor.text)
        self.assertIn("no-store", visitor.headers["Cache-Control"])
        owner = admin_client(app).get("/app/health")
        self.assertIn("signedIn: true", owner.text)
        self.assertIn("page-planner", owner.text)


@needs_database
class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        self.visitor = app.test_client()

    def test_owner_only(self):
        for path in ("/api/v1/today", "/api/v1/health/overview", "/api/v1/health/workouts", "/api/v1/money/overview",
                     "/api/v1/money/transactions", "/api/v1/health/profile"):
            self.assertEqual(self.visitor.get(path).status_code, 401, path)
        self.assertEqual(self.visitor.post("/api/v1/health/workouts", json={}).status_code, 401)

    def test_records_validate_and_round_trip(self):
        bad = self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-09-24", "minutes": 5000})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("minutes", bad.json["error"])
        self.assertEqual(self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "slot": "brunch"}).status_code, 400)
        w = self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-09-24", "workout_type": "Strength", "minutes": 50}).json
        self.assertEqual(self.owner.put(f"/api/v1/health/workouts/{w['workout_id']}", json={"minutes": 55}).json["minutes"], 55)
        self.assertEqual(self.owner.get("/api/v1/health/workouts?date=2026-09-24").json[0]["workout_id"], w["workout_id"])
        self.assertEqual(self.owner.delete(f"/api/v1/health/workouts/{w['workout_id']}").status_code, 200)
        self.assertEqual(self.owner.delete(f"/api/v1/health/workouts/{w['workout_id']}").status_code, 404)

    def test_a_meal_from_a_saved_food_gets_its_nutrition(self):
        food = self.owner.post("/api/v1/health/foods", json={"name": "Test oats", "unit": "cup", "calories": 300, "protein_g": 10}).json
        meal = self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "slot": "breakfast",
                                                              "food_id": food["food_id"], "quantity": 1.5}).json
        self.assertEqual((meal["description"], meal["calories"], meal["protein_g"]), ("Test oats", 450, 15))
        self.assertEqual(self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "food_id": "nope"}).status_code, 400)

    def test_profile_drives_the_derived_goals(self):
        self.owner.put("/api/v1/health/profile", json={"height_in": 70, "bmi_goal": 22, "calorie_target": 2000})
        goals = {g["metric"]: g for g in self.owner.get("/api/v1/health/goals").json}
        self.assertEqual((goals["weight"]["target"], goals["daily_calories"]["target"]), (153.3, 2000))
        self.assertEqual(self.owner.put("/api/v1/health/goals/weight", json={"target": 150}).status_code, 409)
        self.assertEqual(self.owner.put("/api/v1/health/profile", json={"bmi_goal": 99}).status_code, 400)
        self.assertEqual(self.owner.put("/api/v1/health/profile", json={"alcohol_days": [5, 9]}).status_code, 400)

    def test_habits_tick_from_the_day_and_a_set_habit_wins(self):
        self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-09-24", "workout_type": "Strength", "minutes": 40})
        habits = {h["habit"]: h for h in self.owner.get("/api/v1/today?date=2026-09-24").json["habits"]}
        self.assertEqual((habits["lift"]["done"], habits["lift"]["origin"]), (True, "derived"))
        self.owner.post("/api/journal/entry", json={"id": "t1", "date": "2026-09-24", "habits": {"lift": False}})
        habits = {h["habit"]: h for h in self.owner.get("/api/v1/today?date=2026-09-24").json["habits"]}
        self.assertEqual((habits["lift"]["done"], habits["lift"]["origin"]), (False, "manual"))
        entry = self.owner.get("/api/journal/entries").json
        self.assertEqual(next(e for e in entry if e["id"] == "t1")["derivedHabits"], {"lift": True})

    def test_journal_edits_keep_meals_and_balances_that_were_not_sent(self):
        self.owner.post("/api/journal/entry", json={"id": "t2", "date": "2026-09-20", "meals": {"B": "eggs"},
                                                    "accounts": [{"institution": "Bank", "name": "Checking", "balance": "10"}]})
        self.owner.post("/api/journal/entry", json={"id": "t2", "date": "2026-09-20", "freeWrite": "edited"})
        e = next(x for x in self.owner.get("/api/journal/entries").json if x["id"] == "t2")
        self.assertEqual((e["freeWrite"], e["meals"]["B"], e["accounts"][0]["balance"]), ("edited", "eggs", 10.0))

    def test_music_songs_versions_and_what_visitors_see(self):
        with db.tx() as conn:
            asset = media.register(conn, "audio/test_song_take.mp3", b"ID3")
            conn.execute("INSERT INTO music.albums (album_id, title, published) VALUES ('hidden-album', 'Hidden', false)")
            rec = conn.execute("""INSERT INTO music.recordings (album_id, title, audio_asset_id, published)
                                  VALUES ('hidden-album', 'take one', %s, true) RETURNING recording_id""", (asset,)).fetchone()["recording_id"]
        self.addCleanup(lambda: (db.run("DELETE FROM music.recordings WHERE recording_id = %s", rec),
                                 db.run("DELETE FROM core.media_assets WHERE path = 'audio/test_song_take.mp3'")))
        owner = self.owner.get("/api/v1/music?view=owner").json
        self.assertIn(rec, [r["recording_id"] for r in owner["unassigned"]])
        song = self.owner.post("/api/v1/music/songs", json={"title": "Test Song", "recording_id": rec}).json
        self.assertEqual(song["slug"], "test-song")
        self.assertEqual(self.visitor.get("/api/v1/music/songs/test-song").status_code, 404)        # draft
        self.owner.put(f"/api/v1/music/songs/{song['song_id']}", json={"published": True, "story": "Why",
                       "sections": [{"section_type": "chorus", "lyrics": "la", "chords": "G C"}]})
        public = self.visitor.get("/api/v1/music/songs/test-song").json
        self.assertEqual((public["story"], public["sections"][0]["chords"]), ("Why", "G C"))
        self.assertEqual(public["versions"], [])                                                  # its album is hidden
        self.assertNotIn("Hidden", [a["title"] for a in self.visitor.get("/api/v1/music").json["albums"]])
        self.owner.put("/api/v1/music/albums/hidden-album", json={"published": True})
        self.assertEqual(len(self.visitor.get("/api/v1/music/songs/test-song").json["versions"]), 1)
        self.assertIsNone(self.visitor.get("/api/v1/music?view=owner").json["unassigned"])       # visitors never get owner view

    def test_projects_hidden_ones_stay_hidden(self):
        self.owner.put("/api/v1/projects/dmp", json={"visible": False, "chips": ["Databricks", "SQL"]})
        self.assertNotIn("dmp", [p["id"] for p in self.visitor.get("/api/v1/projects").json])
        self.assertEqual(self.visitor.get("/api/v1/projects/dmp").status_code, 404)
        owner_view = {p["id"]: p for p in self.owner.get("/api/v1/projects?view=owner").json}
        self.assertEqual(owner_view["dmp"]["chips"], ["Databricks", "SQL"])
        self.assertEqual(self.visitor.put("/api/v1/projects/snc", json={"visible": False}).status_code, 401)

    def test_travel_and_site_text(self):
        self.owner.put("/api/v1/travel/places", json=[{"id": 7, "name": "Oxford", "lat": 34.36, "lng": -89.52, "type": "visited"}])
        self.owner.put("/api/v1/travel/visited", json={"countries": ["us"], "states": ["ms"]})
        t = self.visitor.get("/api/v1/travel").json
        self.assertEqual((t["places"][0]["name"], t["visited"]), ("Oxford", {"countries": ["US"], "states": ["MS"]}))
        self.assertEqual(self.visitor.put("/api/v1/site-text/home.intro", json={"value": "x"}).status_code, 401)
        self.owner.put("/api/v1/site-text/home.intro", json={"value": "Hello"})
        self.assertEqual(self.visitor.get("/api/v1/site-text/home.intro").json["value"], "Hello")
        self.assertEqual(self.visitor.get("/api/v1/site-text/secret").status_code, 404)

    def test_money_screens_read_without_data(self):
        for path in ("/api/v1/money/overview", "/api/v1/money/months", "/api/v1/money/transactions?month=2026-08",
                     "/api/v1/money/receipts", "/api/v1/money/accounts", "/api/v1/money/budgets", "/api/v1/money/goals"):
            self.assertEqual(self.owner.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
