"""Private tracker hosting: auth, CSRF, installs, revisions, the sandboxed frame,
and the projection of each save into the relational model."""
import io
import json
import unittest
from unittest import mock

from tests.support import admin_client, fresh_database
from app import app
from virtuwill import db, finance, trackers


def document(kind="finance"):
    seed = {"transactions": [], "receipts": [], "retirement": []} if kind == "finance" else {
        "foods": [{"id": "oats", "name": "Oats", "unit": "cup", "k": 300, "p": 10, "c": 54, "fa": 5, "fi": 8}],
        "recipes": [{"name": "Breakfast", "parts": [["oats", 1]]}], "counts": {}}
    key = "yoste-finance-spa-v1" if kind == "finance" else "yoste-health-v1"
    return ('<!doctype html><html><head></head><body><script id="seed" type="application/json">' + json.dumps(seed)
            + '</script><script>const key="' + key + '";</script></body></html>')


def finance_state():
    return {"version": 1, **{k: [] for k in ("transactions", "movements", "items", "receipts", "retirement", "allocations", "shopping")}}


def health_state(**changes):
    state = {"version": 1, "foods": [], "complete": ["2026-09-21"],
             "weights": [{"date": "2026-09-21", "value": 175.0, "morning": True, "note": ""},
                         {"date": "2026-09-21", "value": 176.2, "morning": False, "note": "evening"}],
             "workouts": [{"date": "2026-09-21", "type": "Strength", "minutes": 50, "note": ""},
                          {"date": "2026-09-22", "type": "Dog walk", "minutes": 30, "note": ""}],
             "meals": [{"date": "2026-09-21", "slot": "Lunch", "status": "Eaten", "name": "Salad", "note": "",
                        "k": 450, "p": 30, "c": 20, "fa": 15, "fi": 6}],
             "beers": [{"date": "2026-09-20", "name": "IPA", "count": 2, "oz": 12, "abv": 6.5, "k": 200, "std": 2.6}],
             "settings": {"height": 70, "goal": 22, "target": 2000, "mode": "maintain", "weekend": "5,6,0", "age": 40}}
    state.update(changes)
    return state


class TrackerTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        app.config.update(TESTING=True, TRACKER_AUTH_CONFIGURED=True)
        self.client = app.test_client()

    def login(self):
        self.client = admin_client(app)
        return self.client.get('/api/admin/trackers/finance').json['csrf']

    def install(self, token, kind="finance"):
        return self.client.post(f'/api/admin/trackers/{kind}/install', headers={"X-Tracker-CSRF": token},
                                data={"file": (io.BytesIO(document(kind).encode()), "tracker.html")})

    def save(self, token, kind, state, revision):
        return self.client.put(f'/api/admin/trackers/{kind}', json={"revision": revision, "state": state},
                               headers={"X-Tracker-CSRF": token})

    def test_auth_and_default_credentials_fail_closed(self):
        for path in ('/api/admin/trackers/finance', '/admin/trackers/health/frame'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.put('/api/admin/trackers/finance', json={}).status_code, 401)
        self.login()
        app.config['TRACKER_AUTH_CONFIGURED'] = False
        self.assertEqual(self.client.get('/api/admin/trackers/finance').status_code, 503)

    def test_private_install_csrf_validation_and_no_replacement(self):
        token = self.login()
        self.assertEqual(self.install('wrong').status_code, 403)
        self.assertEqual(self.install(token).status_code, 201)
        self.assertEqual(self.install(token).status_code, 409)
        self.assertEqual(self.client.get('/api/admin/trackers/other').status_code, 404)
        wrong = self.client.post('/api/admin/trackers/health/install', headers={"X-Tracker-CSRF": token},
                                 data={"file": (io.BytesIO(document().encode()), 'finance.html')})
        self.assertEqual(wrong.status_code, 400)
        self.assertNotIn('yoste-finance-spa-v1', self.client.get('/').text)

    def test_revision_conflict_and_validation(self):
        token = self.login()
        self.install(token)
        payload = {"revision": 0, "state": finance_state()}
        self.assertEqual(self.client.put('/api/admin/trackers/finance', json=payload).status_code, 403)
        response = self.save(token, 'finance', payload['state'], 0)
        self.assertEqual(response.json['revision'], 1)
        self.assertTrue(response.json['synced'])
        stale = finance_state() | {"shopping": [{"name": "stale"}]}
        self.assertEqual(self.save(token, 'finance', stale, 0).status_code, 409)
        row = trackers.get('finance')
        self.assertEqual(json.loads(row['state'])['shopping'], [])
        self.assertEqual(row['revision'], 1)
        self.assertEqual(self.save(token, 'finance', {}, 1).status_code, 400)
        self.assertEqual(self.save(token, 'finance', finance_state() | {"shopping": [{"amount": float('nan')}]}, 1).status_code, 400)

    def test_health_tracker_is_retired_but_kept(self):
        token = self.login()
        self.install(token, 'health')
        response = self.save(token, 'health', health_state(), 0)
        self.assertEqual(response.status_code, 410)
        self.assertIn('retired', response.json['error'])
        self.assertIn('retired', self.client.get('/api/admin/trackers/health').json['retired'])
        self.assertEqual(self.client.get('/admin/trackers/health/frame').status_code, 200)   # still readable for export

    def test_projection_keeps_row_ids_and_removes_deleted_records(self):
        """The one-time move re-projects a tracker's last state; records keep one row each."""
        trackers.install('health', document('health'))
        def project(state):
            with db.tx() as conn:
                return trackers.project(conn, 'health', state, document('health'))
        project(health_state())
        ids = lambda: {r["note"] or r["workout_type"]: r["workout_id"] for r in
                       db.all("SELECT workout_id, workout_type, note FROM journal.workouts WHERE source = 'health_tracker'")}
        before = ids()
        self.assertEqual(set(before), {"Strength", "Dog walk"})
        workouts = [health_state()["workouts"][0], {"date": "2026-09-23", "type": "Cardio", "minutes": 45, "note": "bike"}]
        project(health_state(workouts=workouts))
        after = ids()
        self.assertEqual(after["Strength"], before["Strength"])
        self.assertEqual(set(after), {"Strength", "bike"})
        project(health_state(workouts=workouts))
        self.assertEqual(ids(), after)
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM health.body_measurements")["n"], 2)
        self.assertEqual(db.one("SELECT name FROM health.recipes")["name"], "Breakfast")
        goals = {g['metric']: g for g in admin_client(app).get('/api/v1/health/goals').json}
        self.assertEqual(goals['weight']['target'], 153.3)      # BMI 22 at 70 in
        self.assertFalse(goals['weight']['editable'])

    def test_projection_failure_is_reported_and_the_record_still_saves(self):
        token = self.login()
        self.install(token)
        with mock.patch.object(finance, "project", side_effect=RuntimeError("unexpected record")):
            response = self.save(token, 'finance', finance_state(), 0)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['synced'])
        self.assertIn('unexpected record', response.json['syncError'])
        self.assertEqual(trackers.get('finance')['revision'], 1)
        diagnostics = self.client.get('/api/admin/diagnostics').json
        sync = next(s for s in diagnostics['syncs'] if s['source'] == 'finance_tracker')
        self.assertFalse(sync['ok'])

    def test_frame_sandbox_escape_and_logout(self):
        token = self.login()
        self.install(token)
        self.save(token, 'finance', finance_state() | {"shopping": [{"name": '</script><script>alert(1)</script>'}]}, 0)
        r = self.client.get('/admin/trackers/finance/frame')
        self.assertEqual(r.status_code, 200)
        self.assertIn('no-store', r.headers['Cache-Control'])
        self.assertIn("connect-src 'none'", r.headers['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', r.headers['Content-Security-Policy'])
        self.assertNotIn('</script><script>alert(1)', r.text)
        self.assertLess(r.text.index('window.TRACKER_BOOT='), r.text.index('const key='))
        self.client.post('/api/admin/logout')
        self.assertEqual(self.client.get('/admin/trackers/finance/frame').status_code, 401)
        with self.client.session_transaction() as session:
            self.assertNotIn('tracker_csrf', session)

    def test_frame_uses_parent_origin_behind_tls_proxy(self):
        token = self.login()
        self.install(token)
        boot = lambda r: json.loads(r.text.split('window.TRACKER_BOOT=', 1)[1].split(';\n', 1)[0])
        proxied = {'X-Forwarded-Host': 'vw.databricksapps.com'}
        r = self.client.get('/admin/trackers/finance/frame?origin=https://vw.databricksapps.com', headers=proxied)
        self.assertEqual(boot(r)['origin'], 'https://vw.databricksapps.com')
        r = self.client.get('/admin/trackers/finance/frame?origin=https://evil.example', headers=proxied)
        self.assertEqual(boot(r)['origin'], 'http://localhost')


if __name__ == '__main__':
    unittest.main()
