"""Savings goals owned by Money: a dollar target, and progress from a linked account or a saved amount.
All accounts and amounts here are made up."""
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db


@needs_database
class MoneyGoalTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        with db.tx() as conn:
            conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, mask, account_type) VALUES
                            ('test-savings', 'Test Bank', 'Rainy day', '0001', 'savings'),
                            ('test-card', 'Test Bank', 'Test card', '0002', 'credit_card')""")
            conn.execute("""INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, source)
                            VALUES ('2026-03-01', 'test-savings', 2500, 'reported', 'manual')""")

    def goals(self):
        return {g["name"]: g for g in self.owner.get("/api/v1/money/goals").json["goals"]}

    def test_a_goal_with_a_saved_amount(self):
        r = self.owner.post("/api/v1/money/goals", json={"name": "Family trip", "target": "5000", "saved": "1250",
                                                         "due_on": "2027-06-01", "contribution_per_check": 100})
        self.assertEqual(r.status_code, 201, r.json)
        g = r.json
        self.assertEqual((g["goal_id"], g["target"], g["balance"], g["pct_of_target"], g["balance_source"]),
                         ("family-trip", 5000, 1250, 25.0, "saved"))
        # A newer amount wins (same day: the later entry); an older, back-dated one doesn't.
        later = self.owner.post(f"/api/v1/money/goals/{g['goal_id']}/saved", json={"amount": 2000})
        self.assertEqual((later.status_code, later.json["balance"], later.json["as_of"]), (201, 2000, g["as_of"]))
        older = self.owner.post(f"/api/v1/money/goals/{g['goal_id']}/saved", json={"amount": 10, "as_of": "2001-01-01"})
        self.assertEqual(older.json["balance"], 2000)
        edited = self.owner.put(f"/api/v1/money/goals/{g['goal_id']}", json={"target": 4000, "note": "made up"}).json
        self.assertEqual((edited["target"], edited["pct_of_target"], edited["note"]), (4000, 50.0, "made up"))
        self.assertEqual(self.owner.delete(f"/api/v1/money/goals/{g['goal_id']}").json, {"ok": True})
        self.assertEqual(self.goals(), {})
        self.assertIsNone(db.one("SELECT 1 FROM finance.balance_snapshots WHERE goal_id = 'family-trip'"))

    def test_a_goal_that_follows_an_account(self):
        g = self.owner.post("/api/v1/money/goals", json={"name": "Emergency fund", "target": 10000, "account_id": "test-savings"}).json
        self.assertEqual((g["balance"], g["pct_of_target"], g["balance_source"], g["account_name"], g["account_mask"]),
                         (2500, 25.0, "account", "Rainy day", "0001"))
        # Its progress is the account's; a saved amount doesn't apply.
        self.assertEqual(self.owner.post("/api/v1/money/goals/emergency-fund/saved", json={"amount": 1}).status_code, 409)
        with db.tx() as conn:
            conn.execute("""INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, source)
                            VALUES ('2026-03-15', 'test-savings', 3000, 'reported', 'manual')""")
        self.assertEqual(self.goals()["Emergency fund"]["balance"], 3000)
        # Unlinking falls back to saved amounts (none yet).
        self.assertIsNone(self.owner.put("/api/v1/money/goals/emergency-fund", json={"account_id": None}).json["balance"])

    def test_refused_goals(self):
        for body, status, message in (({"target": 100}, 400, "name"), ({"name": "No target"}, 400, "target"),
                                      ({"name": "Zero", "target": 0}, 400, "target"),
                                      ({"name": "Card", "target": 100, "account_id": "test-card"}, 400, "not a card"),
                                      ({"name": "Ghost", "target": 100, "account_id": "nope"}, 400, "doesn't exist"),
                                      ({"name": "Late", "target": 100, "due_on": "soon"}, 400, "date")):
            r = self.owner.post("/api/v1/money/goals", json=body)
            self.assertEqual(r.status_code, status, body)
            self.assertIn(message, r.json["error"])
        self.owner.post("/api/v1/money/goals", json={"name": "Car", "target": 100})
        self.assertEqual(self.owner.post("/api/v1/money/goals", json={"name": "car", "target": 200}).status_code, 409)
        self.assertEqual(self.owner.put("/api/v1/money/goals/nope", json={"target": 1}).status_code, 404)
        self.assertEqual(app.test_client().post("/api/v1/money/goals", json={"name": "x", "target": 1}).status_code, 401)

    def test_goal_accounts_are_cash_and_savings_only(self):
        accounts = [a["account_id"] for a in self.owner.get("/api/v1/money/goals").json["goalAccounts"]]
        self.assertEqual(accounts, ["test-savings"])


if __name__ == "__main__":
    unittest.main()
