"""Career: the CV, ethos and projects page, and the owner's editing of it."""
import io
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app


@needs_database
class CareerTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        self.visitor = app.test_client()

    def test_the_cv_moves_out_of_the_page_once_and_links_projects_to_roles(self):
        d = self.visitor.get("/api/v1/career").json
        self.assertTrue(d["profile"]["full_name"])
        self.assertTrue(all(d["highlights"][s] for s in ("pillar", "impact", "strength", "certification")))
        self.assertTrue(d["roles"] and d["skills"] and d["education"])
        # Newest role first; the current one has no end date.
        self.assertIsNone(d["roles"][0]["ended_on"])
        self.assertEqual(sorted(r["started_on"] for r in d["roles"])[::-1], [r["started_on"] for r in d["roles"]])
        roles = {r["role_id"] for r in d["roles"]}
        linked = [p for p in d["projects"] if p["role_id"]]
        self.assertTrue(linked and all(p["role_id"] in roles for p in linked))

        # Deleting a seeded role sticks: the seed does not come back on the next start.
        role_id = d["roles"][-1]["role_id"]
        self.assertEqual(self.owner.delete(f"/api/v1/career/roles/{role_id}").status_code, 200)
        from virtuwill import career, db
        with db.tx() as conn:
            career.seed(conn)
        self.assertNotIn(role_id, [r["role_id"] for r in self.visitor.get("/api/v1/career").json["roles"]])

    def test_owner_edits_and_visitors_see_only_what_is_visible(self):
        for method, path in (("put", "/api/v1/career/profile"), ("post", "/api/v1/career/roles"), ("post", "/api/v1/career/cv"),
                             ("get", "/api/v1/career/roles")):
            self.assertEqual(getattr(self.visitor, method)(path, json={}).status_code, 401, path)

        role = self.owner.post("/api/v1/career/roles", json={
            "slug": "test-co", "title": "Analyst", "organization": "Test Co", "started_on": "2012-02-01",
            "ended_on": "2013-05-01", "bullets": "First thing\n\nSecond thing", "tags": ["SQL"], "visible": False})
        self.assertEqual(role.status_code, 201, role.json)
        self.assertEqual(role.json["bullets"], ["First thing", "Second thing"])
        self.assertNotIn("Test Co", [r["organization"] for r in self.visitor.get("/api/v1/career").json["roles"]])
        self.assertIn("Test Co", [r["organization"] for r in self.owner.get("/api/v1/career?view=owner").json["roles"]])

        bad = self.owner.post("/api/v1/career/roles", json={"slug": "x", "title": "T", "organization": "O",
                                                            "started_on": "2015-01-01", "ended_on": "2014-01-01"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("ended_on", bad.json["error"])
        self.assertEqual(self.owner.post("/api/v1/career/highlights", json={"section": "motto", "title": "x"}).status_code, 400)

        self.assertEqual(self.owner.put("/api/v1/career/profile", json={"linkedin_url": "javascript:alert(1)"}).status_code, 400)
        self.assertEqual(self.owner.put("/api/v1/career/profile", json={"full_name": ""}).status_code, 400)
        saved = self.owner.put("/api/v1/career/profile", json={"headline": "Data Product Lead", "phone": ""}).json
        self.assertEqual((saved["headline"], saved["phone"]), ("Data Product Lead", ""))

    def test_a_project_names_the_role_it_came_from(self):
        role_id = self.visitor.get("/api/v1/career").json["roles"][0]["role_id"]
        self.assertEqual(self.owner.put("/api/v1/projects/virtuwill", json={"role_id": 999999}).status_code, 400)
        self.assertEqual(self.owner.put("/api/v1/projects/virtuwill", json={"role_id": role_id}).json["role_id"], role_id)
        self.assertIsNone(self.owner.put("/api/v1/projects/virtuwill", json={"role_id": None}).json["role_id"])

    def test_an_uploaded_cv_replaces_the_bundled_one(self):
        bundled = self.visitor.get("/career/cv")
        self.assertEqual(bundled.status_code, 200)
        self.assertIn("attachment", bundled.headers["Content-Disposition"])
        not_pdf = self.owner.post("/api/v1/career/cv", content_type="multipart/form-data",
                                  data={"file": (io.BytesIO(b"<html>"), "cv.pdf")})
        self.assertEqual(not_pdf.status_code, 400)
        pdf = b"%PDF-1.4\n% made-up test CV\n"
        self.assertEqual(self.owner.post("/api/v1/career/cv", content_type="multipart/form-data",
                                         data={"file": (io.BytesIO(pdf), "cv.pdf")}).status_code, 200)
        self.assertEqual(self.visitor.get("/career/cv").data, pdf)
        self.assertEqual(self.visitor.get("/resume/download").data, pdf)   # the old address still works
        self.owner.delete("/api/v1/career/cv")
        self.assertNotEqual(self.visitor.get("/career/cv").data, pdf)


if __name__ == "__main__":
    unittest.main()
