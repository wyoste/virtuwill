"""The workspace API (/api/v1) and the page routes of the new site structure."""
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db, media


class PageRouteTests(unittest.TestCase):
    """Page shells render without a database: every public URL, the workspace and its sign-in."""

    def test_public_urls_serve_the_site_and_old_admin_moves_to_the_workspace(self):
        client = app.test_client()
        for path in ("/", "/music", "/music/some-song", "/career", "/career/projects/dmp", "/projects", "/projects/dmp", "/writing", "/writing/abc",
                     "/garden", "/travel", "/resume", "/contact"):
            page = client.get(path)
            self.assertEqual(page.status_code, 200, path)
            self.assertIn('id="nl-career"', page.text)
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

    def test_one_meal_of_several_foods_with_their_own_portions(self):
        make = lambda name, **n: self.owner.post("/api/v1/health/foods", json={"name": name, **n}).json["food_id"]
        egg = make("Test egg", unit="1 large", calories=70, protein_g=6, fat_g=5)
        tortilla = make("Test tortilla", unit="1 tortilla", calories=140, protein_g=4, carbs_g=24, fat_g=3.5)
        oil = make("Test olive oil", unit="1 tbsp", calories=120, fat_g=14)
        meal = self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "slot": "breakfast",
            "description": "Egg burrito", "calories": 9999,
            "items": [{"food_id": egg, "quantity": 3}, {"food_id": tortilla, "quantity": 1}, {"food_id": oil, "quantity": 0.5}]})
        self.assertEqual(meal.status_code, 201, meal.json)
        m = meal.json
        self.assertEqual((m["description"], m["calories"], m["protein_g"], m["fat_g"]), ("Egg burrito", 410, 22, 25.5))
        self.assertEqual([(i["description"], i["quantity"], i["calories"]) for i in m["items"]],
                         [("Test egg", 3, 210), ("Test tortilla", 1, 140), ("Test olive oil", 0.5, 60)])
        # Change a portion: the totals follow; the day's views read the new totals.
        m = self.owner.put(f"/api/v1/health/meals/{m['meal_id']}", json={"items": [{"food_id": egg, "quantity": 2},
                                                                                  {"food_id": tortilla, "quantity": 1}]}).json
        self.assertEqual((m["calories"], len(m["items"])), (280, 2))
        day = self.owner.get("/api/v1/health/days?from=2026-09-24&to=2026-09-24").json[0]
        self.assertEqual(day["meal_calories"], 280)
        self.assertEqual(len(self.owner.get("/api/v1/today?date=2026-09-24").json["meals"][0]["items"]), 2)
        # A note-only edit keeps the items; an unknown food or a zero portion is refused.
        self.assertEqual(len(self.owner.put(f"/api/v1/health/meals/{m['meal_id']}", json={"note": "spicy"}).json["items"]), 2)
        self.assertEqual(self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "items": [{"food_id": "nope"}]}).status_code, 400)
        self.assertEqual(self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "items": [{"food_id": egg, "quantity": 0}]}).status_code, 400)
        # A meal with no saved foods at all (the everyday case) logs too.
        self.assertEqual(self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "description": "Toast"}).status_code, 201)
        # Without items the meal keeps hand-entered nutrition.
        plain = self.owner.post("/api/v1/health/meals", json={"meal_date": "2026-09-24", "description": "Cafe lunch", "calories": 650, "items": []}).json
        self.assertEqual((plain["calories"], plain["items"]), (650, []))

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

    def test_music_behind_the_scenes(self):
        song = self.owner.post("/api/v1/music/songs", json={"title": "Test Ballad"}).json
        self.addCleanup(lambda: db.run("DELETE FROM music.songs WHERE song_id = %s", song["song_id"]))
        url = f"/api/v1/music/songs/{song['song_id']}"
        saved = self.owner.put(url, json={
            "published": True, "meaning": "About a made-up place.", "themes": "home, Leaving , home",
            "capo": "2", "tuning": "", "time_signature": "6/8", "strumming": "D DU", "influences": "Test folk",
            "sections": [{"section_type": "verse", "chords": "G-C-D", "lyrics": "first test line\nsecond test line"}],
            "notes": [{"line_text": "first test line", "note": "Written on a test train."}, {"line_text": "", "note": "dropped"}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        public = self.visitor.get("/api/v1/music/songs/test-ballad").json
        self.assertEqual((public["meaning"], public["themes"], public["capo"], public["time_signature"]),
                         ("About a made-up place.", ["home", "Leaving"], 2, "6/8"))
        self.assertEqual(public["notes"], [{"line_text": "first test line", "note": "Written on a test train."}])
        listed = next(s for s in self.visitor.get("/api/v1/music").json["songs"] if s["slug"] == "test-ballad")
        self.assertEqual((listed["has_lyrics"], listed["has_chords"], listed["note_count"]), (True, True, 1))
        self.assertNotIn("notes", listed)
        for bad in ({"capo": 13}, {"capo": "high"}, {"themes": 5}, {"notes": "x"}):
            self.assertEqual(self.owner.put(url, json=bad).status_code, 400, bad)
        self.owner.put(url, json={"capo": None, "notes": []})
        public = self.visitor.get("/api/v1/music/songs/test-ballad").json
        self.assertEqual((public["capo"], public["notes"]), (None, []))
        self.assertEqual(self.visitor.put(url, json={"meaning": "x"}).status_code, 401)

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
