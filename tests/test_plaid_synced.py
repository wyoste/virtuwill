"""Plaid from the Lakebase synced tables into the finance model. The synced tables are stood in for by plain tables
shaped like the bronze ones the plaid_integration notebook lands (text times, 'None' for a missing date); all made up."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg

from tests.support import PG, admin_client, fresh_database, needs_database
from app import app
from virtuwill import db, plaid_synced
from virtuwill.importers import plaid, plaid_load

SYNCED = """
DROP SCHEMA IF EXISTS bronze CASCADE;
CREATE SCHEMA bronze;
CREATE TABLE bronze.plaid_balance (
    _pulled_at TEXT, item_id TEXT, item_label TEXT, institution_id TEXT, account_id TEXT, account_name TEXT,
    official_name TEXT, mask TEXT, type TEXT, subtype TEXT, current DOUBLE PRECISION, available DOUBLE PRECISION,
    limit_amt DOUBLE PRECISION, iso_currency_code TEXT, _rescued_data TEXT, _source_file TEXT,
    _ingested_at TIMESTAMPTZ, _pull_batch TEXT, PRIMARY KEY (account_id, _pulled_at));
CREATE TABLE bronze.plaid_transaction (
    _pulled_at TEXT, _sync_op TEXT, item_label TEXT, transaction_id TEXT, account_id TEXT, date TEXT, authorized_date TEXT,
    name TEXT, merchant_name TEXT, amount DOUBLE PRECISION, iso_currency_code TEXT, category TEXT, pending BOOLEAN,
    _rescued_data TEXT, _source_file TEXT, _ingested_at TIMESTAMPTZ, _pull_batch TEXT,
    PRIMARY KEY (transaction_id, _pulled_at, _sync_op));
"""
DAY1 = datetime(2026, 3, 2, 13, tzinfo=timezone.utc)


class MappingTests(unittest.TestCase):
    def test_what_the_notebook_writes_maps_onto_the_model(self):
        self.assertEqual(plaid.personal_finance_category("FOOD_AND_DRINK_GROCERIES"),
                         {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"})
        self.assertEqual(plaid.personal_finance_category("INCOME"), {"primary": "INCOME", "detailed": ""})
        self.assertEqual(plaid.personal_finance_category("Food and Drink > Restaurants"), {})    # older categories → Review
        row = {"transaction_id": "t1", "account_id": "a", "date": "2026-03-02", "authorized_date": "None", "amount": 4.5,
               "merchant_name": "Cafe", "category": "FOOD_AND_DRINK_COFFEE", "pending": False}
        row = plaid.transaction_row(plaid_load.transaction_from_row(row), "1111")
        self.assertEqual((row["transacted_on"], row["posted_on"], row["category"]), ("2026-03-02", "2026-03-02", "Dining"))
        # A pull just after midnight UTC is still the evening before in Chicago.
        self.assertEqual(plaid_load.local_day("2026-03-03T03:15:00.123456+00:00", "America/Chicago"), "2026-03-02")

    def test_the_loop_only_starts_when_asked(self):
        with mock.patch.dict(os.environ, {"PLAID_SYNC_MINUTES": "0"}), mock.patch.object(plaid_synced.threading, "Thread") as thread:
            plaid_synced.start()
        thread.assert_not_called()


@needs_database
class SyncTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute(SYNCED)
        self.addCleanup(self.drop)
        self.owner = admin_client(app)

    def drop(self):
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS bronze CASCADE")

    def land(self, table, rows, at):
        """Rows as Auto Loader lands them in bronze, and the synced table mirrors them."""
        with psycopg.connect(PG, autocommit=True) as conn:
            for r in rows:
                r = {**r, "_ingested_at": at, "_pulled_at": r.get("_pulled_at") or (at - timedelta(minutes=2)).isoformat()}
                conn.execute(f"INSERT INTO bronze.{table} ({', '.join(r)}) VALUES ({', '.join(['%s'] * len(r))})", list(r.values()))

    def balance(self, account_id, mask, kind, subtype, current, available=None, bank="Test Bank"):
        return {"item_label": bank, "institution_id": "ins_0", "account_id": account_id, "account_name": "Made-up account",
                "mask": mask, "type": kind, "subtype": subtype, "current": current, "available": available,
                "iso_currency_code": "USD"}

    def txn(self, tid, account_id, amount, category, day="2026-03-02", pending=False, op="added"):
        return {"_sync_op": op, "item_label": "Test Bank", "transaction_id": tid, "account_id": account_id, "date": day,
                "authorized_date": "None", "name": "MADE UP", "merchant_name": "Made Up Merchant", "amount": amount,
                "iso_currency_code": "USD", "category": category, "pending": pending}

    def sync(self, **body):
        r = self.owner.post("/api/v1/money/plaid-sync", json=body)
        return r.status_code, r.json

    def rows(self):
        with db.tx() as conn:
            return {r["external_id"]: r for r in conn.execute(
                "SELECT external_id, amount, is_pending, category, movement_type FROM finance.transactions")}

    def test_missing_synced_tables_are_reported(self):
        self.drop()
        status, reply = self.sync()
        self.assertEqual(status, 409)
        self.assertIn("bronze.plaid_balance", reply["error"])
        self.assertFalse(self.owner.get("/api/v1/money/plaid-sync").json["available"])
        diag = self.owner.get("/api/admin/diagnostics").json
        self.assertIn("plaid_balance", next(s for s in diag["syncs"] if s["source"] == "plaid_bronze")["error"])

    def test_card_payments_interest_and_account_names_as_plaid_sends_them(self):
        card = {**self.balance("card", "7237", "credit", "credit card", 625.89, bank="Chase"), "account_name": "CREDIT CARD",
                "official_name": "Ultimate Rewards®"}
        k401 = {**self.balance("k", "2047", "investment", "401k", 30791.22, bank="Fidelity"),
                "account_name": "GREYSTAR 401(K) PLAN", "official_name": "Investment"}
        hysa = self.balance("hysa", "8154", "depository", "savings", 21060.18, 21060.18, bank="Amex")
        self.land("plaid_balance", [card, k401, hysa], DAY1)
        self.land("plaid_transaction", [self.txn("pay", "card", -800, "LOAN_DISBURSEMENTS_OTHER_DISBURSEMENT"),
                                             self.txn("int", "hysa", -12.5, "INCOME_INTEREST_EARNED"),
                                             self.txn("sal", "hysa", -2000, "INCOME_SALARY")], DAY1)
        self.sync()
        rows = self.rows()
        self.assertEqual({k: rows[k]["movement_type"] for k in rows},
                         {"pay": "Credit card payment", "int": "Interest income", "sal": "Payroll deposit"})
        with db.tx() as conn:
            names = {r["mask"]: r["name"] for r in conn.execute("SELECT mask, name FROM finance.accounts")}
        self.assertEqual((names["7237"], names["2047"]), ("Ultimate Rewards®", "GREYSTAR 401(K) PLAN"))

    def test_a_posted_charge_that_names_its_pending_one_keeps_the_row(self):
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute("ALTER TABLE bronze.plaid_transaction ADD COLUMN pending_transaction_id TEXT")
        self.land("plaid_balance", [self.balance("card", "2222", "credit", "credit card", 80)], DAY1)
        self.land("plaid_transaction", [self.txn("p1", "card", 30, "GENERAL_MERCHANDISE_OTHER", pending=True)], DAY1)
        self.sync()
        with db.tx() as conn:
            conn.execute("INSERT INTO finance.categories (category) VALUES ('Gifts') ON CONFLICT DO NOTHING")
            conn.execute("UPDATE finance.transactions SET category = 'Gifts' WHERE external_id = 'p1'")
        day2 = DAY1 + timedelta(days=1)
        self.land("plaid_transaction", [{**self.txn("s1", "card", 32.5, "GENERAL_MERCHANDISE_OTHER", day="2026-03-03"),
                                              "pending_transaction_id": "p1"},
                                             {"_sync_op": "removed", "transaction_id": "p1", "account_id": "card",
                                              "item_label": "Test Bank", "date": "None", "authorized_date": "None",
                                              "pending": False}], day2)
        status, report = self.sync()
        self.assertEqual(report["removed"], 0)
        rows = self.rows()
        self.assertEqual(set(rows), {"p1"})                         # the posted charge took over the pending row
        self.assertEqual((float(rows["p1"]["amount"]), rows["p1"]["is_pending"], rows["p1"]["category"]), (32.5, False, "Gifts"))

    def test_loads_what_landed_then_only_what_is_new(self):
        self.land("plaid_balance", [self.balance("chk", "1111", "depository", "checking", 500, 480),
                                         self.balance("card", "2222", "credit", "credit card", 120.5, bank="Amex"),
                                         self.balance("roth", "3333", "investment", "roth", 9000, bank="Fidelity"),
                                         self.balance("nomask", None, "depository", "savings", 10, 10)], DAY1)
        self.land("plaid_transaction", [self.txn("t1", "card", 12.5, "FOOD_AND_DRINK_COFFEE"),
                                             self.txn("t2", "chk", -2000, "INCOME_WAGES"),
                                             self.txn("p1", "card", 30, "GENERAL_MERCHANDISE_OTHER", pending=True),
                                             self.txn("t9", "nomask", 5, "BANK_FEES")], DAY1)

        status, preview = self.sync(dry_run=True)
        self.assertEqual((status, preview["preview"]["transactions"]["new"]), (200, 3))
        self.assertEqual(self.rows(), {})
        self.assertEqual(self.owner.get("/api/v1/money/plaid-sync").json["transactions"]["not_loaded"], 4)

        status, first = self.sync()
        self.assertEqual((first["accounts"], first["transactions"], first["transactions_without_account"]), (3, 3, 1))
        rows = self.rows()
        self.assertEqual((rows["t1"]["category"], rows["t2"]["movement_type"], rows["p1"]["is_pending"]),
                         ("Dining", "Payroll deposit", True))
        with db.tx() as conn:
            accounts = {r["mask"]: (r["institution"], r["account_type"], r["retirement_type"]) for r in conn.execute(
                "SELECT mask, institution, account_type, retirement_type FROM finance.accounts")}
        self.assertEqual(accounts["3333"], ("Fidelity", "retirement", "roth_ira"))
        self.assertTrue(self.sync()[1]["nothing_new"])

        # The next day's pull: the pending charge settles under a new id and Plaid removes the pending one.
        day2 = DAY1 + timedelta(days=1)
        self.land("plaid_balance", [self.balance("card", "2222", "credit", "credit card", 153, bank="Amex")], day2)
        self.land("plaid_transaction", [self.txn("s1", "card", 32.5, "GENERAL_MERCHANDISE_OTHER", day="2026-03-03"),
                                             {"_sync_op": "removed", "transaction_id": "p1", "account_id": "card",
                                              "item_label": "Test Bank", "date": "None", "authorized_date": "None",
                                              "pending": False}], day2)
        status, later = self.sync()
        self.assertEqual((later["transactions"], later["balances"], later["removed"]), (1, 1, 1))
        self.assertEqual(set(self.rows()), {"t1", "t2", "s1"})
        with db.tx() as conn:
            owed = conn.execute("""SELECT l.balance FROM finance.latest_balances l JOIN finance.accounts a USING (account_id)
                                   WHERE a.mask = '2222'""").fetchone()
        self.assertEqual(float(owed["balance"]), 153)
        seen = self.owner.get("/api/v1/money/plaid-sync").json
        self.assertEqual((seen["transactions"]["not_loaded"], seen["last_load"]["removed"]), (0, 1))

        # Reading everything again matches what's there; nothing doubles.
        self.sync(reload=True)
        self.assertEqual(set(self.rows()), {"t1", "t2", "s1"})

    def test_the_background_load_is_the_same_load(self):
        self.land("plaid_balance", [self.balance("chk", "1111", "depository", "checking", 5, 5)], DAY1)
        self.land("plaid_transaction", [self.txn("t1", "chk", 1, "BANK_FEES")], DAY1)
        report, error = plaid_synced.run_once()
        self.assertIsNone(error)
        self.assertEqual(report["transactions"], 1)
        self.assertEqual(set(self.rows()), {"t1"})


if __name__ == "__main__":
    unittest.main()
