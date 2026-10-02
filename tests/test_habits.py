"""Habits: a row per day with a column per habit. NULL follows the day's records; true or false is the owner's answer.
All data here is made up."""
import unittest
from unittest import mock

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db, migrate


def upgrade_from(before, setup):
    """A database at the schema files before `before` (e.g. "111"), with setup(conn) run on it, then upgraded."""
    every = db.schema_files()
    older = [p for p in every if db.schema_order(p.name) < db.schema_order(before + "_")]
    # The start-up data steps are this release's code: they run on the upgrade, not on the older schema.
    with mock.patch.object(db, "schema_files", lambda: older), mock.patch.object(migrate, "run_pending", lambda conn: None):
        fresh_database()
    with db.tx() as conn:
        setup(conn)
    db.reset()
    with db.tx():
        pass


@needs_database
class HabitTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)

    def habits(self, day):
        return {h["habit"]: (h["done"], h["origin"]) for h in self.owner.get(f"/api/v1/today?date={day}").json["habits"]}

    def put(self, day, body):
        return self.owner.put(f"/api/v1/days/{day}/habits", json=body)

    def test_a_day_is_one_row_and_needs_no_journal_entry(self):
        r = self.put("2026-09-24", {"read": True, "drink": False})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual({h["habit"]: h["done"] for h in r.json}["read"], True)
        self.assertIsNone(self.owner.get("/api/v1/today?date=2026-09-24").json["entry"])     # no blank entry made
        row = db.one("SELECT * FROM journal.daily_habits WHERE day = '2026-09-24'")
        self.assertEqual((row["read"], row["drink"], row["run"]), (True, False, None))
        # Setting one leaves the others; null clears one back to the day's records.
        self.put("2026-09-24", {"guitar": True})
        self.put("2026-09-24", {"read": None})
        row = db.one("SELECT * FROM journal.daily_habits WHERE day = '2026-09-24'")
        self.assertEqual((row["read"], row["drink"], row["guitar"]), (None, False, True))

    def test_bad_requests(self):
        self.assertEqual(self.put("2026-09-24", {"flossing": True}).status_code, 400)
        self.assertEqual(self.put("2026-09-24", {"read": "yes"}).status_code, 400)
        self.assertEqual(self.put("not-a-day", {"read": True}).status_code, 400)
        self.assertEqual(app.test_client().put("/api/v1/days/2026-09-24/habits", json={"read": True}).status_code, 401)
        # An unknown habit is never made up (a typo used to become a habit).
        self.assertNotIn("flossing", [r["habit"] for r in db.all("SELECT habit FROM journal.habits")])

    def test_records_tick_habits_and_a_set_habit_wins(self):
        self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-09-24", "workout_type": "Cardio",
                                                        "activity": "Running", "minutes": 30})
        self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-09-25", "workout_type": "Strength",
                                                        "minutes": 40, "note": "after brunch"})
        self.owner.post("/api/v1/health/drinks", json={"drink_date": "2026-09-25", "name": "Beer", "containers": 2})
        self.assertEqual(self.habits("2026-09-24")["run"], (True, "derived"))
        day = self.habits("2026-09-25")
        self.assertEqual((day["lift"], day["drink"]), ((True, "derived"), (True, "derived")))
        self.assertEqual(day["run"], (False, "none"))                       # "brunch" in a note isn't a run
        self.put("2026-09-25", {"lift": False})
        self.assertEqual(self.habits("2026-09-25")["lift"], (False, "manual"))

    def test_the_journal_page_still_saves_habits_with_the_entry(self):
        self.owner.post("/api/journal/entry", json={"id": "t1", "date": "2026-09-24", "habits": {"read": True, "smoke": False}})
        entry = next(e for e in self.owner.get("/api/journal/entries").json if e["id"] == "t1")
        self.assertEqual(entry["habits"], {"read": True, "smoke": False})
        # A save without habits (a screen editing part of the entry) keeps them; one with habits replaces them.
        self.owner.post("/api/journal/entry", json={"id": "t1", "date": "2026-09-24", "freeWrite": "more"})
        self.owner.post("/api/journal/entry", json={"id": "t1", "date": "2026-09-24", "habits": {"guitar": True}})
        entry = next(e for e in self.owner.get("/api/journal/entries").json if e["id"] == "t1")
        self.assertEqual(entry["habits"], {"guitar": True})
        # Deleting the entry leaves the day's habits.
        self.owner.delete("/api/journal/entry/t1")
        self.assertEqual(self.habits("2026-09-24")["guitar"], (True, "manual"))

    def test_the_daily_summary_and_the_wide_view_count_ticked_habits(self):
        self.owner.post("/api/v1/health/workouts", json={"workout_date": "2026-09-24", "workout_type": "Cardio",
                                                        "activity": "Run", "minutes": 30})
        self.put("2026-09-24", {"read": True, "smoke": True})
        summary = db.one("SELECT habits_built, habits_to_limit FROM core.daily_summary WHERE day = '2026-09-24'")
        self.assertEqual((summary["habits_built"], summary["habits_to_limit"]), (2, 1))       # run (ticked) and read; smoke
        wide = db.one("SELECT * FROM journal.habit_days WHERE day = '2026-09-24'")
        self.assertEqual((wide["run"], wide["read"], wide["smoke"], wide["lift"]), (True, True, True, False))


@needs_database
class HabitUpgradeTests(unittest.TestCase):
    def test_habit_logs_become_one_row_per_day(self):
        def old_rows(conn):
            conn.execute("INSERT INTO journal.habits (habit, label) VALUES ('meditate', 'Meditate')")
            conn.execute("INSERT INTO journal.entries (entry_date, entry_id) VALUES ('2026-09-20', 'e1'), ('2026-09-21', 'e2')")
            conn.execute("""INSERT INTO journal.habit_logs VALUES ('2026-09-20', 'run', true), ('2026-09-20', 'drink', false),
                                                                  ('2026-09-21', 'meditate', true)""")
        upgrade_from("111", old_rows)
        rows = {r["day"].isoformat(): r for r in db.all("SELECT * FROM journal.daily_habits ORDER BY day")}
        self.assertEqual((rows["2026-09-20"]["run"], rows["2026-09-20"]["drink"], rows["2026-09-20"]["read"]), (True, False, None))
        self.assertTrue(rows["2026-09-21"]["meditate"])            # a habit an older release added keeps its answers
        self.assertIsNone(db.one("SELECT to_regclass('journal.habit_logs') AS t")["t"])


if __name__ == "__main__":
    unittest.main()
