"""The site against a real PostgreSQL database: schema versions, the one-time
upgrade from the earlier Lakebase layout, and every page's API round trip."""
import io
import json
import os
import unittest
from unittest import mock

from tests.support import PG, admin_client, drop_schemas, fresh_database, needs_database, pg_env
from app import app
from virtuwill import db, media


class NoDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"PGHOST": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        db.reset()
        self.addCleanup(db.reset)

    def test_pages_load_and_data_requests_say_the_database_is_missing(self):
        client = app.test_client()
        page = client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('id="chatFab"', page.text)
        response = client.get('/api/garden')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json['available'])
        self.assertEqual(admin_client(app).get('/api/admin/diagnostics').json['database'], {"configured": False, "reachable": False})


@needs_database
class SchemaTests(unittest.TestCase):
    def setUp(self):
        fresh_database()

    def test_each_schema_file_is_applied_once_and_edits_are_reported(self):
        with db.tx() as conn:
            status = db.schema_status(conn)
        self.assertEqual(status["pending"], [])
        self.assertEqual([a["version"] for a in status["applied"]], [p.name for p in db.schema_files()])
        with db.tx() as conn:
            conn.execute("UPDATE virtuwill.schema_versions SET checksum = 'old' WHERE version = '20_health.sql'")
        db.reset()
        with db.tx() as conn:        # a second start re-runs nothing and flags the edited file
            self.assertEqual(db.schema_status(conn)["edited"], ["20_health.sql"])
            self.assertEqual([r["name"] for r in conn.execute("SELECT name FROM virtuwill.migrations ORDER BY name")],
                             ["career_seed_v1", "relational_v1"])

    def test_calendar_accepts_historical_dates(self):
        self.assertTrue(db.one("SELECT 1 AS ok FROM core.calendar WHERE day = '1999-05-01'"))


@needs_database
class UpgradeTests(unittest.TestCase):
    """A database written by the previous release (journal/health v0, collections, media)."""

    def setUp(self):
        fresh_database(bootstrap=False)
        import psycopg
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute("""
                CREATE SCHEMA journal; CREATE SCHEMA health; CREATE SCHEMA virtuwill;
                CREATE TABLE journal.entries (entry_date DATE PRIMARY KEY, entry_id TEXT UNIQUE, quote TEXT, quote_author TEXT,
                    free_write TEXT, source TEXT, account_snapshots JSONB, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ);
                CREATE TABLE journal.entry_tags (entry_date DATE, tag TEXT, position INTEGER);
                CREATE TABLE journal.habit_logs (entry_date DATE, habit TEXT, done BOOLEAN);
                CREATE TABLE journal.meals (meal_date DATE, slot TEXT, description TEXT, source TEXT, source_ref TEXT);
                CREATE TABLE journal.workouts (workout_date DATE, activity TEXT, minutes NUMERIC, note TEXT, is_dog_walk BOOLEAN,
                    source TEXT);
                CREATE TABLE health.body_measurements (measured_on DATE, measured_at TIMESTAMPTZ, metric TEXT, value NUMERIC,
                    unit TEXT, is_morning BOOLEAN, note TEXT, source TEXT);
                CREATE TABLE virtuwill.collections (name TEXT PRIMARY KEY, data JSONB);
                CREATE TABLE virtuwill.media (path TEXT PRIMARY KEY, content_type TEXT, content BYTEA);
                CREATE TABLE virtuwill.migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
                CREATE TABLE virtuwill.trackers (kind TEXT PRIMARY KEY, document TEXT NOT NULL, state TEXT,
                    revision INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
                INSERT INTO journal.entries VALUES ('2026-09-21', 'e1', 'q', 'a', 'written', 'manual',
                    '[{"institution": "USAA", "name": "Checking", "balance": "100"}]', '2026-09-21T12:00:00Z', now());
                INSERT INTO journal.entry_tags VALUES ('2026-09-21', 'calm', 0);
                INSERT INTO journal.habit_logs VALUES ('2026-09-21', 'run', true);
                INSERT INTO journal.meals VALUES ('2026-09-21', 'dinner', 'fish', 'journal', 'e1'),
                                                 ('2026-09-21', 'lunch', 'from tracker', 'health_tracker', '#0');
                INSERT INTO journal.workouts VALUES ('2026-09-22', 'Bike', 40, '', false, 'manual'),
                                                    ('2026-09-22', 'Strength', 50, '', false, 'health_tracker');
                INSERT INTO health.body_measurements VALUES ('2026-09-22', NULL, 'weight', 172.5, 'lb', true, '', 'manual');
                INSERT INTO virtuwill.migrations (name) VALUES ('journal_schema_v1');
            """)
            conn.execute("INSERT INTO virtuwill.collections VALUES ('travel_pins', %s), ('garden_gallery_note', %s), ('music_catalog', %s)",
                         (json.dumps(json.dumps([{"id": 1, "name": "Oxford", "city": "Oxford, MS", "lat": 34.36, "lng": -89.52,
                                                  "type": "visited", "note": "", "photos": []}])),
                          json.dumps("Spring notes"),
                          json.dumps({"tracks": [{"id": "t9", "title": "Uploaded", "src": "/static/audio/test_upload_song.mp3"}]})))
            conn.execute("INSERT INTO virtuwill.media VALUES ('audio/test_upload_song.mp3', 'audio/mpeg', %s)", (b"ID3 bytes",))
        db.reset()
        with db.tx():
            pass
        self.addCleanup(lambda: (media.STATIC / "audio/test_upload_song.mp3").unlink(missing_ok=True))

    def test_everything_moves_and_the_old_tables_are_kept_aside(self):
        client = admin_client(app, journal=True)
        entries = {e["id"]: e for e in client.get('/api/journal/entries').json}
        e1 = entries["e1"]
        self.assertEqual((e1["date"], e1["freeWrite"], e1["tags"], e1["habits"]), ("2026-09-21", "written", ["calm"], {"run": True}))
        self.assertEqual(e1["meals"]["D"], "fish")
        self.assertEqual(e1["accounts"], [{"institution": "USAA", "name": "Checking", "balance": 100.0}])
        manual = db.all("SELECT workout_type, activity, source FROM journal.workouts")
        self.assertEqual([(r["workout_type"], r["activity"], r["source"]) for r in manual], [("Other", "Bike", "manual")])
        self.assertEqual(db.one("SELECT value FROM health.body_measurements")["value"], 172.5)
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM legacy_journal_v0.entries")["n"], 1)
        self.assertEqual(json.loads(client.get('/api/data/travel_pins').json['data'])[0]["name"], "Oxford")
        self.assertEqual(client.get('/api/data/garden_gallery_note').json['data'], "Spring notes")
        self.assertEqual(client.get('/api/music/catalog').json['tracks'][0]['src'], "/static/audio/test_upload_song.mp3")
        self.assertEqual(app.test_client().get('/static/audio/test_upload_song.mp3').data, b"ID3 bytes")


@needs_database
class ApiTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        app.config.update(TESTING=True)
        self.admin = admin_client(app)
        self.visitor = app.test_client()

    def test_admin_sign_in_opens_the_journal(self):
        entry = {"id": "j1", "date": "2026-09-21", "freeWrite": "hello", "meals": {"B": "eggs"}, "tags": ["a"], "habits": {"read": True}}
        self.assertEqual(self.visitor.post('/api/journal/entry', json=entry).status_code, 401)
        self.assertEqual(self.admin.post('/api/journal/entry', json=entry).status_code, 201)
        self.assertEqual(self.admin.post('/api/journal/entry', json=entry | {"id": "j2"}).status_code, 409)
        self.assertEqual(self.admin.post('/api/journal/entry', json=entry | {"id": "j3", "date": "1850-01-01"}).status_code, 400)
        self.assertTrue(self.admin.get('/api/journal/check/2026-09-21').json['exists'])
        self.assertEqual(self.admin.delete('/api/journal/entry/j1').status_code, 200)
        self.assertNotIn("j1", [e['id'] for e in self.admin.get('/api/journal/entries').json])

    def test_weights_are_calculated_in_pounds_whatever_the_entry_unit(self):
        self.admin.post('/api/health/weigh-ins', json={"date": "2026-09-21", "value": 170, "morning": True})
        with db.tx() as conn:
            conn.execute("""INSERT INTO health.body_measurements (measured_on, metric, value, unit, is_morning, source)
                            VALUES ('2026-09-21', 'weight', 86, 'kg', true, 'manual')""")
        day = self.admin.get('/api/health/dashboard').json['weights'][0]
        self.assertEqual(day['weight_unit'], 'lb')
        self.assertAlmostEqual(day['morning_weight'], (170 + 86 / 0.45359237) / 2, places=0)

    def test_manual_workouts_can_be_logged_and_removed(self):
        created = self.admin.post('/api/health/workouts', json={"date": "2026-09-21", "minutes": 45, "type": "Cardio", "activity": "Bike"})
        self.assertEqual(created.status_code, 201)
        dashboard = self.admin.get('/api/health/dashboard').json
        self.assertEqual(dashboard['workouts'][0]['activity'], 'Bike')
        self.assertEqual(self.admin.delete(f"/api/health/workouts/{created.json['id']}").status_code, 200)
        self.assertEqual(self.visitor.get('/api/health/dashboard').status_code, 401)

    def test_garden_round_trip_keeps_plant_details_and_logs_health(self):
        garden = {"calibration": {"feetPerPixel": 0.1}, "backgroundImageUrl": "/static/garden_illustrated.png", "beds": [
            {"id": "b1", "name": "Front", "color": "#5DCAA5", "shape": {"type": "rectangle", "width": 10, "height": 4},
             "transform": {"x": 1.5, "y": 2, "rotation": 0},
             "plants": [{"id": "p1", "speciesId": "tomato", "displayName": "Roma", "gi": 1, "gj": 2, "health": 3,
                         "notes": "", "radiusFt": 1.2, "age": 2}]}]}
        self.assertEqual(self.visitor.post('/api/garden', json=garden).status_code, 401)
        self.assertEqual(self.admin.post('/api/garden', json=garden).status_code, 200)
        plant = self.visitor.get('/api/garden').json['beds'][0]['plants'][0]
        self.assertEqual((plant['radiusFt'], plant['age'], plant['health']), (1.2, 2, 3))
        garden['beds'][0]['plants'][0]['health'] = 1
        self.admin.post('/api/garden', json=garden)
        self.assertEqual([r["health"] for r in db.all("SELECT health FROM garden.plant_observations ORDER BY observation_id")], [3, 1])

    def test_music_catalog_keeps_order_sections_and_recordings(self):
        catalog = {"tracks": [
            {"id": "t2", "title": "Second", "src": "/static/audio/This life.mp3", "chords": "G C D", "published": True,
             "sections": [{"type": "chorus", "label": "Chorus", "lyrics": "la"}]},
            {"id": "t1", "title": "First", "src": None, "published": False}]}
        self.admin.post('/api/music/catalog', json=catalog)
        tracks = self.visitor.get('/api/music/catalog').json['tracks']
        self.assertEqual([t['id'] for t in tracks], ["t2", "t1"])
        self.assertEqual((tracks[0]['chords'], tracks[0]['sections'][0]['type'], tracks[0]['src']),
                         ("G C D", "chorus", "/static/audio/This life.mp3"))
        singles = self.visitor.get('/api/music/library').json['singles']
        self.assertIn("This life", [s['name'] for s in singles])

    def test_blog_contact_and_portfolio(self):
        titles = lambda client: [p['title'] for p in client.get('/api/blog').json]
        post = self.admin.post('/api/blog', json={"title": "Hi", "body": "Body", "published": False}).json['post']
        self.assertNotIn("Hi", titles(self.visitor))
        self.assertIn("Hi", titles(self.admin))
        self.admin.put(f"/api/blog/{post['id']}", json={"published": True})
        self.assertIn("Hi", titles(self.visitor))
        self.assertEqual(self.visitor.post('/api/contact', json={"name": "A", "message": "Hello"}).status_code, 200)
        message = self.admin.get('/api/contact/messages').json[0]
        self.admin.post(f"/api/contact/read/{message['id']}")
        self.assertTrue(self.admin.get('/api/contact/messages').json[0]['read'])
        self.assertIsNone(self.visitor.get('/api/data/portfolio_layout').json['data'])
        self.admin.put('/api/data/portfolio_layout', json={"data": json.dumps({"dmp": {"visible": False, "deleted": False}})})
        layout = json.loads(self.visitor.get('/api/data/portfolio_layout').json['data'])
        self.assertFalse(layout["dmp"]["visible"])

    def test_travel_is_null_until_saved_then_round_trips(self):
        self.assertIsNone(self.visitor.get('/api/data/travel_visited').json['data'])
        self.admin.put('/api/data/travel_visited', json={"data": json.dumps({"countries": ["us", "MX"], "states": ["TX"]})})
        self.assertEqual(json.loads(self.visitor.get('/api/data/travel_visited').json['data']),
                         {"countries": ["MX", "US"], "states": ["TX"]})
        self.assertEqual(self.visitor.put('/api/data/travel_visited', json={"data": "{}"}).status_code, 401)

    def test_chat_button_follows_the_admin_setting(self):
        self.assertNotIn('id="chatFab"', self.visitor.get('/').text)
        self.assertEqual(self.visitor.put('/api/settings', json={"site.chat_enabled": True}).status_code, 401)
        self.assertEqual(self.admin.put('/api/settings', json={"site.chat_enabled": "yes"}).status_code, 400)
        self.assertEqual(self.admin.put('/api/settings', json={"site.chat_enabled": True}).status_code, 200)
        self.assertIn('id="chatFab"', self.visitor.get('/').text)
        self.assertEqual(self.visitor.get('/api/settings').json, {"site.chat_enabled": True})

    def test_private_files_are_served_only_to_the_owner(self):
        with db.tx() as conn:
            media.register(conn, "blog/private-test.png", b"secret", visibility="private")
        self.addCleanup(lambda: db.run("DELETE FROM core.media_assets WHERE path = 'blog/private-test.png'"))
        self.assertEqual(self.visitor.get('/static/blog/private-test.png').status_code, 404)
        self.assertEqual(self.admin.get('/static/blog/private-test.png').data, b"secret")

    def test_uploaded_project_pages_are_sandboxed(self):
        upload = self.admin.post('/api/portfolio/upload', data={"file": (io.BytesIO(b"<script>1</script>"), "demo_test.html")})
        self.addCleanup(lambda: (media.STATIC / "portfolio/demo_test.html").unlink(missing_ok=True))
        self.assertEqual(upload.status_code, 200)
        page = self.visitor.get('/static/portfolio/demo_test.html')
        self.assertIn("sandbox", page.headers["Content-Security-Policy"])
        page.close()
        self.assertEqual(self.visitor.get('/api/portfolio/uploads').json[0]['url'], "/static/portfolio/demo_test.html")

    def test_diagnostics_show_schema_and_trackers(self):
        self.assertEqual(self.visitor.get('/api/admin/diagnostics').status_code, 401)
        d = self.admin.get('/api/admin/diagnostics').json
        self.assertTrue(d['database']['reachable'])
        self.assertEqual(d['schema']['pending'], [])
        self.assertEqual(d['trackers'], [])


def tearDownModule():
    db.reset()
    if PG:
        os.environ.update(pg_env())
        drop_schemas()


if __name__ == '__main__':
    unittest.main()
