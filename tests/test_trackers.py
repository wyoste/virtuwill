import io
import json
from pathlib import Path
import tempfile
import unittest

from app import app
from trackers import TrackerStore


def document(kind="finance"):
    seed = {"transactions": [], "receipts": [], "retirement": []} if kind == "finance" else {"foods": [], "recipes": [], "counts": {}}
    key = "yoste-finance-spa-v1" if kind == "finance" else "yoste-health-v1"
    return '<!doctype html><html><head></head><body><script id="seed" type="application/json">' + json.dumps(seed) + '</script><script>const key="' + key + '";</script></body></html>'


def finance_state():
    return {"version": 1, **{k: [] for k in ("transactions", "movements", "items", "receipts", "retirement", "allocations", "shopping")}}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.config.update(TESTING=True, SECRET_KEY="test-secret-" * 4, TRACKER_AUTH_CONFIGURED=True, TRACKER_DATA_DIR=self.temp.name)
        self.client = app.test_client()
        self.store = TrackerStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def login(self):
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
        return self.client.get('/api/admin/trackers/finance').json['csrf']

    def install(self, token, kind="finance"):
        return self.client.post(f'/api/admin/trackers/{kind}/install', data={"file": (io.BytesIO(document(kind).encode()), "tracker.html")}, headers={"X-Tracker-CSRF": token})

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
        wrong = self.client.post('/api/admin/trackers/health/install', data={"file": (io.BytesIO(document().encode()), 'finance.html')}, headers={"X-Tracker-CSRF": token})
        self.assertEqual(wrong.status_code, 400)
        self.assertNotIn('yoste-finance-spa-v1', self.client.get('/').text)

    def test_atomic_revision_conflict_and_restart_persistence(self):
        token = self.login()
        self.install(token)
        payload = {"revision": 0, "state": finance_state()}
        headers = {"X-Tracker-CSRF": token}
        self.assertEqual(self.client.put('/api/admin/trackers/finance', json=payload).status_code, 403)
        response = self.client.put('/api/admin/trackers/finance', json=payload, headers=headers)
        self.assertEqual(response.json['revision'], 1)
        payload['state']['shopping'] = [{"name": "stale"}]
        self.assertEqual(self.client.put('/api/admin/trackers/finance', json=payload, headers=headers).status_code, 409)
        row = TrackerStore(self.temp.name).get('finance')
        self.assertEqual(json.loads(row['state'])['shopping'], [])
        self.assertEqual(row['revision'], 1)
        self.assertEqual(self.client.put('/api/admin/trackers/finance', json={"revision": 1, "state": {}}, headers=headers).status_code, 400)
        payload['revision'] = 1
        payload['state']['shopping'] = [{"amount": float('nan')}]
        self.assertEqual(self.client.put('/api/admin/trackers/finance', json=payload, headers=headers).status_code, 400)

    def test_frame_sandbox_escape_and_logout(self):
        token = self.login()
        self.install(token)
        state = finance_state()
        state['shopping'] = [{"name": '</script><script>alert(1)</script>'}]
        self.store.save('finance', state, 0)
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
