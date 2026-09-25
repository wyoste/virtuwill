"""The ingest API: token-authenticated pushes of balances and transactions. All data here is made up."""
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db

URL = "/api/ingest/v1/finance"


@needs_database
class IngestTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        self.client = app.test_client()
        made = self.owner.post("/api/v1/api-tokens", json={"name": "Test feed"})
        self.assertEqual(made.status_code, 201, made.json)
        self.token = made.json["token"]
        self.auth = {"X-VirtuWill-Token": self.token}

    def push(self, body, headers=None):
        return self.client.post(URL, json=body, headers=self.auth if headers is None else headers)

    def rows(self, sql, *args):
        with db.tx() as conn:
            return conn.execute(sql, args).fetchall()

    def test_tokens_are_shown_once_and_guard_every_route(self):
        listed = self.owner.get("/api/v1/api-tokens").json
        self.assertEqual(listed[0]["name"], "Test feed")
        self.assertNotIn("token", listed[0])
        self.assertNotIn("token_hash", listed[0])
        self.assertTrue(self.token.startswith(listed[0]["token_prefix"]))
        self.assertEqual(self.client.post("/api/v1/api-tokens", json={"name": "x"}).status_code, 401)   # owners only

        body = {"balances": [{"account_mask": "1111", "as_of": "2026-01-10", "balance": 10}]}
        self.assertEqual(self.push(body, headers={}).status_code, 401)
        self.assertEqual(self.push(body, headers={"X-VirtuWill-Token": "vw_not-a-real-token"}).status_code, 401)
        self.assertEqual(self.push(body, headers={"Authorization": "Bearer " + self.token}).status_code, 201)
        # A signed-in browser session is not a token.
        self.assertEqual(self.owner.post(URL, json=body).status_code, 401)
        self.assertEqual(self.client.get("/api/ingest/v1", headers=self.auth).json["token"], "Test feed")

        read_only = self.owner.post("/api/v1/api-tokens", json={"name": "Reader", "scopes": ["finance:read"]}).json["token"]
        self.assertEqual(self.push(body, headers={"X-VirtuWill-Token": read_only}).status_code, 403)
        self.assertEqual(self.client.get("/api/ingest/v1/finance/status", headers={"X-VirtuWill-Token": read_only}).status_code, 200)

        self.assertEqual(self.owner.delete(f"/api/v1/api-tokens/{listed[0]['token_id']}").status_code, 200)
        self.assertEqual(self.push(body).status_code, 401)

    def test_push_balances_and_transactions_then_repeat(self):
        body = {"source": "test", "accounts": [{"mask": "2222", "institution": "Test Bank", "name": "Test Card", "account_type": "credit_card"}],
                "balances": [{"account_mask": "2222", "as_of": "2026-01-10", "balance": 250}],
                "transactions": [{"account_mask": "2222", "posted_on": "2026-01-09", "description": "Test Cafe", "amount": 4.5, "category": "Dining"},
                                 {"account_mask": "2222", "posted_on": "2026-01-09", "description": "Payment", "amount": -100, "kind": "card_payment"},
                                 {"account_mask": "3333", "posted_on": "2026-01-08", "description": "Test Grocer", "amount": 20}]}
        dry = self.push(body | {"dry_run": True})
        self.assertEqual((dry.status_code, dry.json["preview"]["counts"]["transactions"]["new"]), (200, 3))
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM finance.transactions")[0]["n"], 0)

        first = self.push(body)
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(first.json["report"]["transactions"]["new"], 3)
        self.assertEqual(self.rows("SELECT submitted_by FROM finance.staged_imports")[0]["submitted_by"], "api:Test feed")
        # Account 3333 was created though only a transaction named it.
        self.assertEqual(len(self.rows("SELECT 1 FROM finance.accounts WHERE mask IN ('2222', '3333')")), 2)

        again = self.push(body)
        self.assertEqual(again.status_code, 200)
        self.assertIn("skipped", again.json["report"])
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM finance.transactions")[0]["n"], 3)

        status = self.client.get("/api/ingest/v1/finance/status", headers=self.auth).json
        card = next(a for a in status["accounts"] if a["mask"] == "2222")
        self.assertEqual((card["balance"], card["last_transaction_on"]), (250, "2026-01-09"))
        self.assertEqual(status["recent_loads"][0]["submitted_by"], "api:Test feed")

    def test_bank_ids_keep_same_amount_charges_apart_and_pending_charges_settle(self):
        def tx(ext, day, amount, pending=False):
            return {"account_mask": "4444", "posted_on": day, "description": "Test Store", "amount": amount,
                    "external_id": ext, "pending": pending}
        self.push({"transactions": [tx("a1", "2026-02-01", 9.99), tx("p1", "2026-02-02", 30, pending=True)]})
        # Next run: overlaps a1, a second 9.99 charge a day later with its own id, and p1 settles at a new amount and date.
        second = self.push({"transactions": [tx("a1", "2026-02-01", 9.99), tx("a2", "2026-02-02", 9.99), tx("p1", "2026-02-03", 32.5)]}).json
        self.assertEqual(second["report"]["transactions"], {"new": 1, "seen": 2, "taken_over": 0})
        rows = {r["external_id"]: r for r in self.rows("SELECT external_id, amount, posted_on, is_pending FROM finance.transactions")}
        self.assertEqual(sorted(rows), ["a1", "a2", "p1"])
        self.assertEqual((float(rows["p1"]["amount"]), rows["p1"]["posted_on"].isoformat(), rows["p1"]["is_pending"]), (32.5, "2026-02-03", False))

    def test_a_later_balance_for_the_same_day_replaces_the_earlier(self):
        self.push({"balances": [{"account_mask": "5555", "as_of": "2026-03-01", "balance": 100}]})
        self.push({"balances": [{"account_mask": "5555", "as_of": "2026-03-01", "balance": 120}]})
        latest = self.rows("""SELECT l.balance FROM finance.latest_balances l JOIN finance.accounts a USING (account_id)
                              WHERE a.mask = '5555'""")
        self.assertEqual(float(latest[0]["balance"]), 120)
        self.assertEqual(len(self.rows("""SELECT 1 FROM finance.balance_snapshots s JOIN finance.accounts a USING (account_id)
                                          WHERE a.mask = '5555'""")), 1)

    def test_bad_requests_are_explained(self):
        cases = [({}, "Nothing to load"), ({"balances": "x"}, "must be a list"), ({"transacions": []}, "Unknown fields"),
                 ({"transactions": [{"account_mask": "12", "posted_on": "2026-01-01", "amount": 1}]}, "last four digits"),
                 ({"transactions": [{"account_mask": "1234", "posted_on": "2026-01-01", "amount": 1, "kind": "gift"}]}, "kind must be"),
                 ({"balances": [{"account_mask": "1234", "as_of": "2026-01-01", "balance": 1, "kind": "statement_closing"}]}, "reported"),
                 ({"transactions": [{"account_mask": "1234", "posted_on": "soon", "amount": 1}]}, "not a date")]
        for body, message in cases:
            r = self.push(body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(message, r.json["error"], body)
        self.assertEqual(self.client.post(URL, data="not json", headers=self.auth, content_type="application/json").status_code, 400)


if __name__ == "__main__":
    unittest.main()
