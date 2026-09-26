"""The Plaid job: mapping Plaid's shapes onto the finance model, and a run through the ingest API.
Everything here is made up, shaped like Plaid's /transactions/sync responses."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db

_spec = importlib.util.spec_from_file_location("plaid_job", Path(__file__).resolve().parent.parent / "jobs" / "plaid_to_virtuwill.py")
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)


def account(account_id, mask, kind, subtype, current, available=None, name="Test account"):
    return {"account_id": account_id, "mask": mask, "name": name, "official_name": None, "type": kind, "subtype": subtype,
            "balances": {"current": current, "available": available, "iso_currency_code": "USD"}}


def txn(tid, account_id, amount, primary, detailed="", pending=False, day="2026-03-02", authorized=None, pending_id=None,
        merchant="Test Merchant"):
    return {"transaction_id": tid, "account_id": account_id, "amount": amount, "date": day, "authorized_date": authorized,
            "name": merchant.upper(), "merchant_name": merchant, "pending": pending, "pending_transaction_id": pending_id,
            "personal_finance_category": {"primary": primary, "detailed": detailed}, "iso_currency_code": "USD"}


class MappingTests(unittest.TestCase):
    def test_accounts_balances_kinds_and_categories(self):
        accounts = [account("chk", "1111", "depository", "checking", 500.0, 480.0),
                    account("card", "2222", "credit", "credit card", 120.5),
                    account("ira", "3333", "investment", "roth", 9000),
                    account("nomask", None, "depository", "savings", 10)]
        txns = [txn("t1", "card", 12.5, "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", authorized="2026-03-01"),
                txn("t2", "card", 40, "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES"),
                txn("t3", "card", -100, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
                txn("t4", "chk", -2000, "INCOME", "INCOME_WAGES"),
                txn("t5", "chk", 300, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
                txn("t6", "card", -15, "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER"),
                txn("t7", "card", 9, "SOMETHING_NEW", ""),
                txn("t8", "card", 30, "GENERAL_MERCHANDISE", "", pending_id="p8"),
                txn("t9", "nomask", 5, "BANK_FEES", "")]
        body, skipped = job.bundle("Test Bank", accounts, txns, "2026-03-03", overrides={"ira": "3333"})
        self.assertEqual({a["mask"]: a["account_type"] for a in body["accounts"]},
                         {"1111": "checking", "2222": "credit_card", "3333": "retirement"})
        self.assertEqual(len(skipped), 1)
        self.assertIn("PLAID_MASKS", skipped[0])
        self.assertIn({"account_mask": "1111", "as_of": "2026-03-03", "balance": 480.0, "kind": "available"}, body["balances"])
        self.assertNotIn("available", [b["kind"] for b in body["balances"] if b["account_mask"] == "2222"])
        by_id = {t["external_id"]: t for t in body["transactions"]}
        self.assertEqual((by_id["t1"]["kind"], by_id["t1"]["category"], by_id["t1"]["transacted_on"]), ("expense", "Dining", "2026-03-01"))
        self.assertEqual(by_id["t2"]["category"], "Groceries")
        self.assertEqual(by_id["t2"]["transacted_on"], "2026-03-02")          # no authorized_date: the posted day, never "None"
        self.assertEqual((by_id["t3"]["kind"], by_id["t4"]["kind"], by_id["t5"]["kind"]), ("card_payment", "income", "transfer"))
        self.assertEqual((by_id["t6"]["kind"], by_id["t6"]["category"]), ("refund", "Shopping"))
        self.assertNotIn("category", by_id["t7"])                              # unknown → Review in the app
        self.assertIn("p8", by_id)                                             # a settled charge updates its pending twin
        self.assertNotIn("t9", by_id)                                          # its account was skipped

    def test_big_first_pulls_are_split(self):
        body = {"source": "plaid", "accounts": [{"mask": "1111"}], "balances": [{"x": 1}],
                "transactions": [{"n": i} for i in range(job.CHUNK * 2 + 5)]}
        parts = job.chunks(body)
        self.assertEqual([len(p["transactions"]) for p in parts], [job.CHUNK, job.CHUNK, 5])
        self.assertEqual([len(p["balances"]) for p in parts], [1, 0, 0])
        self.assertTrue(all(p["accounts"] for p in parts))


class FakePlaid:
    """Two pages on the first sync, then a later sync where a pending charge settles."""

    def __init__(self):
        self.calls = []

    def transactions_sync(self, req):
        cursor = req.get("cursor")
        self.calls.append(cursor)
        accounts = [account("card", "4444", "credit", "credit card", 80.0)]
        if cursor is None:
            return {"added": [txn("a1", "card", 20, "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")], "modified": [], "removed": [],
                    "accounts": accounts, "next_cursor": "c1", "has_more": True}
        if cursor == "c1":
            return {"added": [txn("p1", "card", 30, "GENERAL_MERCHANDISE", "", pending=True)], "modified": [], "removed": [],
                    "accounts": accounts, "next_cursor": "c2", "has_more": False}
        return {"added": [txn("s1", "card", 32.5, "GENERAL_MERCHANDISE", "", day="2026-03-04", pending_id="p1")],
                "modified": [], "removed": [{"transaction_id": "p1"}], "accounts": accounts, "next_cursor": "c3", "has_more": False}


@needs_database
class RunTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        owner = admin_client(app)
        token = owner.post("/api/v1/api-tokens", json={"name": "Plaid job test"}).json["token"]
        self.client = app.test_client()
        self.post = lambda body: (lambda r: (r.status_code, r.json))(
            self.client.post("/api/ingest/v1/finance", json=body, headers={"X-VirtuWill-Token": token}))
        self.state = Path(tempfile.mkdtemp()) / "state.json"
        env = {"PLAID_ACCESS_TOKENS": json.dumps({"Test Card Co": "access-sandbox-made-up"}),
               "PLAID_STATE_PATH": str(self.state), "PLAID_MASKS": "{}"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self):
        with db.tx() as conn:
            return {r["external_id"]: r for r in conn.execute(
                "SELECT external_id, amount, posted_on, is_pending FROM finance.transactions")}

    def test_runs_load_only_changes_and_settle_pending_charges(self):
        plaid = FakePlaid()
        self.assertEqual(job.run(dry_run=True, client=plaid, post=self.post), 0)
        self.assertEqual(self.rows(), {})
        self.assertFalse(self.state.exists())                        # a dry run keeps no cursor

        self.assertEqual(job.run(client=plaid, post=self.post), 0)
        self.assertEqual(json.loads(self.state.read_text())["cursors"], {"Test Card Co": "c2"})
        self.assertEqual(set(self.rows()), {"a1", "p1"})
        self.assertTrue(self.rows()["p1"]["is_pending"])

        self.assertEqual(job.run(client=plaid, post=self.post), 0)
        self.assertEqual(plaid.calls[-1], "c2")                      # asked only for what changed
        rows = self.rows()
        self.assertEqual(set(rows), {"a1", "p1"})                    # settled in place, not doubled
        self.assertEqual((float(rows["p1"]["amount"]), rows["p1"]["posted_on"].isoformat(), rows["p1"]["is_pending"]),
                         (32.5, "2026-03-04", False))
        with db.tx() as conn:
            owed = conn.execute("""SELECT l.balance FROM finance.latest_balances l JOIN finance.accounts a USING (account_id)
                                   WHERE a.mask = '4444'""").fetchone()
        self.assertEqual(float(owed["balance"]), 80.0)

    def test_a_refused_load_keeps_the_cursor_for_the_next_run(self):
        self.assertEqual(job.run(client=FakePlaid(), post=lambda body: (400, {"error": "made-up failure"})), 1)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
