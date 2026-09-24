"""Storage layer tests.

Local-file tests always run. Lakebase tests run against any Postgres when
VIRTUWILL_TEST_PG is set to a libpq connection string, e.g.
  VIRTUWILL_TEST_PG="host=localhost port=5432 dbname=lake user=app password=pw sslmode=disable"
"""
import io
import json
import os
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
            db.execute(f"TRUNCATE {storage.SCHEMA}.collections, {storage.SCHEMA}.media, {storage.SCHEMA}.trackers")
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
