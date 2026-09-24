"""Garden photos tagged to beds and plant types; travel stops with photos. All data here is made up."""
import base64
import io
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db, media

# A 1×1 PNG.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def upload(client, url, name="photo.png", content=PNG, **form):
    return client.post(url, content_type="multipart/form-data", data={"files": [(io.BytesIO(content), name)], **form})


@needs_database
class GardenPhotoTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        self.visitor = app.test_client()
        with db.tx() as conn:
            conn.execute("""INSERT INTO garden.beds (bed_id, name, shape_type, width_ft, height_ft) VALUES
                            ('bed-a', 'Front bed', 'rectangle', 4, 8), ('bed-b', 'Back bed', 'rectangle', 3, 3)""")
            species = [r["species_id"] for r in conn.execute("SELECT species_id FROM garden.species ORDER BY species_id LIMIT 2")]
            conn.execute("""INSERT INTO garden.plantings (planting_id, bed_id, species_id, display_name, grid_i, grid_j)
                            VALUES ('p1', 'bed-a', %s, 'Plant', 0, 0)""", (species[0],))
        self.species = species
        self.addCleanup(self._remove_files)

    def _remove_files(self):
        with db.tx() as conn:
            for r in conn.execute("SELECT path FROM core.media_assets WHERE path LIKE 'garden/photos/%%'"):
                (media.STATIC / r["path"]).unlink(missing_ok=True)

    def test_the_page_lists_beds_plant_types_and_default_text(self):
        d = self.visitor.get("/api/v1/garden").json
        self.assertTrue({"Back bed", "Front bed"} <= {b["name"] for b in d["beds"]})
        front = next(b for b in d["beds"] if b["id"] == "bed-a")
        self.assertEqual((front["plants"], [s["id"] for s in front["species"]]), (1, [self.species[0]]))
        self.assertEqual(next(b for b in d["beds"] if b["id"] == "bed-b")["plants"], 0)
        self.assertIn(self.species[0], [s["id"] for s in d["species"]])
        self.assertNotIn(self.species[1], [s["id"] for s in d["species"]])   # only what grows or is photographed
        self.assertTrue(d["text"]["philosophy"] and d["text"]["note"])
        self.assertEqual((d["totals"]["beds"], d["totals"]["photos"]), (len(d["beds"]), 0))
        self.assertEqual(d["totals"]["plants"], sum(b["plants"] for b in d["beds"]))

    def test_photos_are_tagged_to_beds_and_plant_types_and_retagged(self):
        self.assertEqual(upload(self.visitor, "/api/v1/garden/photos").status_code, 401)
        self.assertEqual(upload(self.owner, "/api/v1/garden/photos", name="notes.html", content=b"<html>").status_code, 400)
        made = upload(self.owner, "/api/v1/garden/photos", caption="First bloom", taken_on="2026-05-02",
                      beds=["bed-a", "no-such-bed"], species=[self.species[1]])
        self.assertEqual(made.status_code, 201, made.json)
        photo = made.json["photos"][0]
        self.assertEqual((photo["caption"], photo["date"], photo["beds"], photo["species"]),
                         ("First bloom", "2026-05-02", ["bed-a"], [self.species[1]]))
        d = self.visitor.get("/api/v1/garden").json
        self.assertEqual(next(b for b in d["beds"] if b["id"] == "bed-a")["photos"], 1)
        self.assertIn(self.species[1], [s["id"] for s in d["species"]])   # a photographed plant type is listed

        self.assertEqual(self.visitor.put(f"/api/v1/garden/photos/{photo['id']}", json={"beds": []}).status_code, 401)
        moved = self.owner.put(f"/api/v1/garden/photos/{photo['id']}", json={"beds": ["bed-b"], "species": []}).json
        self.assertEqual((moved["beds"], moved["species"], moved["caption"]), (["bed-b"], [], "First bloom"))
        self.assertEqual(self.owner.put(f"/api/v1/garden/photos/{photo['id']}", json={"taken_on": "someday"}).status_code, 400)
        self.assertEqual(self.owner.put("/api/v1/garden/photos/missing", json={"caption": "x"}).status_code, 404)

        # A deleted bed's tag is ignored.
        with db.tx() as conn:
            conn.execute("DELETE FROM garden.beds WHERE bed_id = 'bed-b'")
        self.assertEqual(self.visitor.get("/api/v1/garden").json["photos"][0]["beds"], [])


    def test_a_photo_uploads_and_shows_when_the_app_folder_is_read_only(self):
        from unittest import mock
        with mock.patch("pathlib.Path.write_bytes", side_effect=OSError("read-only file system")):
            made = upload(self.owner, "/api/v1/garden/photos", caption="Kept in the database", beds=["bed-a"])
        self.assertEqual(made.status_code, 201, made.json)
        url = made.json["photos"][0]["url"]
        self.assertFalse((media.STATIC / url.removeprefix("/static/")).exists())
        self.assertEqual(self.visitor.get(url).data, PNG)   # served from the database


class CountryListTests(unittest.TestCase):
    """The picklist the travel screen pins stops with."""

    def test_countries_have_names_codes_and_positions(self):
        import json
        countries = json.loads((media.STATIC / "data" / "countries.json").read_text(encoding="utf-8"))
        self.assertGreater(len(countries), 200)
        codes = [c["code"] for c in countries if c["code"]]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(len(c) == 2 and c.isupper() for c in codes))
        self.assertTrue(all(-90 <= c["lat"] <= 90 and -180 <= c["lng"] <= 180 and c["name"] for c in countries))
        by_code = {c["code"]: c for c in countries}
        self.assertTrue({"US", "FR", "NO", "MX", "JP", "IT"} <= set(by_code))
        self.assertAlmostEqual(by_code["FR"]["lat"], 46.6, delta=2)    # mainland France, not an overseas territory


@needs_database
class TravelStopTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        self.visitor = app.test_client()
        self.addCleanup(self._remove_files)

    def _remove_files(self):
        folder = media.STATIC / "travel" / "photos"
        for path in folder.glob("*") if folder.exists() else []:
            with db.tx() as conn:
                if conn.execute("SELECT 1 FROM core.media_assets WHERE path = %s", ("travel/photos/" + path.name,)).fetchone():
                    path.unlink(missing_ok=True)

    def test_stops_validate_and_carry_photos_with_captions(self):
        self.assertEqual(self.visitor.post("/api/v1/travel/places", json={}).status_code, 401)
        self.assertEqual(self.owner.post("/api/v1/travel/places", json={"name": "Somewhere", "lat": 95, "lng": 0}).status_code, 400)
        self.assertEqual(self.owner.post("/api/v1/travel/places", json={"name": "", "lat": 1, "lng": 1}).status_code, 400)
        stop = self.owner.post("/api/v1/travel/places", json={"name": "Test Café", "city": "Testville", "lat": "12.5", "lng": -45.25,
                                                                "type": "recommend", "visited": "2025-06-01"})
        self.assertEqual(stop.status_code, 201, stop.json)
        pid = stop.json["id"]
        self.assertEqual((stop.json["city"], stop.json["type"], stop.json["visited"]), ("Testville", "recommend", "2025-06-01"))

        self.assertEqual(upload(self.visitor, f"/api/v1/travel/places/{pid}/photos").status_code, 401)
        self.assertEqual(upload(self.owner, f"/api/v1/travel/places/{pid}/photos", name="x.svg", content=b"<svg/>").status_code, 400)
        withphoto = upload(self.owner, f"/api/v1/travel/places/{pid}/photos", caption="Morning coffee").json
        self.assertEqual(withphoto["photoItems"][0]["caption"], "Morning coffee")
        url = withphoto["photoItems"][0]["url"]
        self.assertEqual(self.visitor.get(url).data, PNG)

        # The public map data carries the photo and its caption.
        place = next(p for p in self.visitor.get("/api/v1/travel").json["places"] if p["id"] == pid)
        self.assertEqual(place["photos"], [url])

        # Saving the whole list back (as the map did) keeps the uploaded file and its caption.
        self.owner.put("/api/v1/travel/places", json=self.visitor.get("/api/v1/travel").json["places"])
        with db.tx() as conn:
            row = conn.execute("SELECT asset_id, url, caption FROM travel.place_photos WHERE place_id = %s", (str(pid),)).fetchone()
        self.assertEqual((row["asset_id"] is not None, row["url"], row["caption"]), (True, None, "Morning coffee"))

        edited = self.owner.put(f"/api/v1/travel/places/{pid}/photos/0", json={"caption": "Cortado"}).json
        self.assertEqual(edited["photoItems"][0]["caption"], "Cortado")
        self.assertEqual(self.owner.put(f"/api/v1/travel/places/{pid}", json={"note": "Go early"}).json["note"], "Go early")
        self.assertEqual(self.owner.delete(f"/api/v1/travel/places/{pid}/photos/0").status_code, 200)
        self.assertEqual(self.visitor.get(url).status_code, 404)
        self.assertEqual(self.owner.delete(f"/api/v1/travel/places/{pid}").status_code, 200)
        self.assertEqual(self.owner.delete(f"/api/v1/travel/places/{pid}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
