"""Plaid bronze → Lakebase: rows shaped as the plaid_integration notebook lands them, loaded into the finance model.
Spark is stood in for by the rows its queries would return; everything here is made up."""
import importlib.util
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import psycopg
from psycopg.rows import dict_row

from tests.support import PG, fresh_database, needs_database
from virtuwill import db
from virtuwill.importers import plaid, plaid_load

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("plaid_bronze_job", ROOT / "jobs" / "plaid_bronze_to_lakebase.py")
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)


def balance(account_id, mask, kind, subtype, current, available, pulled, bank="Test Bank"):
    """A raw_plaid_balances row: type and subtype as str() of the API's value, times as ISO text."""
    return {"account_id": account_id, "item_label": bank, "institution_id": "ins_0", "account_name": "Made-up account",
            "official_name": None, "mask": mask, "type": kind, "subtype": subtype, "current": current,
            "available": available, "limit_amt": None, "iso_currency_code": "USD", "_pulled_at": pulled}


def txn(tid, account_id, amount, category, day="2026-03-02", pending=False, authorized="None"):
    """A raw_plaid_transactions row: dates as text, a missing date as 'None'."""
    return {"transaction_id": tid, "account_id": account_id, "item_label": "Test Bank", "date": day,
            "authorized_date": authorized, "name": "MADE UP MERCHANT", "merchant_name": "Made Up Merchant",
            "amount": amount, "iso_currency_code": "USD", "category": category, "pending": pending, "_sync_op": "added"}


class MappingTests(unittest.TestCase):
    def test_what_the_notebook_writes_maps_onto_the_model(self):
        self.assertEqual(plaid.personal_finance_category("FOOD_AND_DRINK_GROCERIES"),
                         {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"})
        self.assertEqual(plaid.personal_finance_category("INCOME"), {"primary": "INCOME", "detailed": ""})
        self.assertEqual(plaid.personal_finance_category("Food and Drink > Restaurants"), {})    # older categories → Review
        row = plaid.transaction_row(plaid_load.transaction_from_row(txn("t1", "a", 4.5, "FOOD_AND_DRINK_COFFEE")), "1111")
        self.assertEqual((row["transacted_on"], row["posted_on"], row["category"]), ("2026-03-02", "2026-03-02", "Dining"))
        # A pull just after midnight UTC is still the evening before in Chicago.
        self.assertEqual(plaid_load.local_day("2026-03-03T03:15:00.123456+00:00", "America/Chicago"), "2026-03-02")

    def test_the_job_imports_the_loader_without_flask(self):
        code = ("import sys; sys.modules['flask'] = None; import importlib.util as u; "
                f"s = u.spec_from_file_location('j', {str(ROOT / 'jobs' / 'plaid_bronze_to_lakebase.py')!r}); "
                "m = u.module_from_spec(s); s.loader.exec_module(m); print(m.importers().SOURCE)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.stdout.strip(), "plaid_bronze", out.stderr)


@needs_database
class LoadTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.conn = psycopg.connect(PG, autocommit=True, row_factory=dict_row)
        self.addCleanup(self.conn.close)

    def load(self, accounts, balances, transactions, removed=(), dry_run=False, through=None):
        with self.conn.transaction():
            return plaid_load.apply(self.conn, accounts, balances, transactions, set(removed), dry_run=dry_run,
                                    through=through or {"balances": datetime(2026, 3, 2, tzinfo=timezone.utc),
                                                        "transactions": datetime(2026, 3, 2, tzinfo=timezone.utc)})

    def rows(self):
        return {r["external_id"]: r for r in self.conn.execute(
            "SELECT external_id, amount, is_pending, category, movement_type FROM finance.transactions")}

    def test_a_day_then_the_next_a_pending_charge_settling_and_retirement_accounts(self):
        day1 = [balance("chk", "1111", "depository", "checking", 500.0, 480.0, "2026-03-02T13:00:00+00:00"),
                balance("card", "2222", "credit", "credit card", 120.5, None, "2026-03-02T13:00:00+00:00", bank="Amex"),
                balance("roth", "3333", "investment", "roth", 9000.0, None, "2026-03-02T13:00:00+00:00", bank="Fidelity"),
                balance("nomask", None, "depository", "savings", 10.0, 10.0, "2026-03-02T13:00:00+00:00")]
        t1 = [txn("t1", "card", 12.5, "FOOD_AND_DRINK_COFFEE"), txn("t2", "chk", -2000.0, "INCOME_WAGES"),
              txn("p1", "card", 30.0, "GENERAL_MERCHANDISE_OTHER", pending=True), txn("t9", "nomask", 5.0, "BANK_FEES")]

        preview = self.load(day1, day1, t1, dry_run=True)
        self.assertEqual(preview["preview"]["transactions"]["new"], 3)
        self.assertEqual(self.rows(), {})

        report = self.load(day1, day1, t1)
        self.assertEqual((report["accounts"], report["transactions"], report["transactions_without_account"]), (3, 3, 1))
        rows = self.rows()
        self.assertEqual((rows["t1"]["category"], rows["t2"]["movement_type"], rows["p1"]["is_pending"]),
                         ("Dining", "Payroll deposit", True))
        accounts = {r["mask"]: (r["institution"], r["account_type"], r["retirement_type"]) for r in self.conn.execute(
            "SELECT mask, institution, account_type, retirement_type FROM finance.accounts")}
        self.assertEqual(accounts["3333"], ("Fidelity", "retirement", "roth_ira"))
        self.assertEqual(accounts["2222"][:2], ("Amex", "credit_card"))
        self.assertEqual(plaid_load.state(self.conn)["transactions_through"], datetime(2026, 3, 2, tzinfo=timezone.utc))

        self.assertTrue(self.load(day1, [], [])["nothing_new"])

        # Next day: the pending charge settles under a new id, and Plaid removes the pending one.
        day2 = [balance("card", "2222", "credit", "credit card", 153.0, None, "2026-03-03T13:00:00+00:00", bank="Amex")]
        report = self.load(day1 + day2, day2, [txn("s1", "card", 32.5, "GENERAL_MERCHANDISE_OTHER", day="2026-03-03")],
                           removed={"p1"})
        self.assertEqual((report["transactions"], report["balances"], report["removed"]), (1, 1, 1))
        self.assertEqual(set(self.rows()), {"t1", "t2", "s1"})
        owed = self.conn.execute("""SELECT l.balance FROM finance.latest_balances l JOIN finance.accounts a USING (account_id)
                                    WHERE a.mask = '2222'""").fetchone()
        self.assertEqual(float(owed["balance"]), 153)
        diag = self.conn.execute("SELECT report FROM virtuwill.sync_reports WHERE source = 'plaid_bronze'").fetchone()
        self.assertEqual(diag["report"]["removed"], 1)

    def test_the_job_reads_from_where_it_stopped_and_a_dry_run_marks_nothing(self):
        seen = []
        stop = datetime(2026, 3, 2, 14, tzinfo=timezone.utc)

        def fake_read(spark, catalog, schema, since):
            seen.append(since)
            b = [balance("chk", "1111", "depository", "checking", 5.0, 5.0, "2026-03-02T13:00:00+00:00")]
            return b, b, [txn("t1", "chk", 1.0, "BANK_FEES")], set(), {"balances": stop, "transactions": stop}

        with mock.patch.object(job, "read_bronze", fake_read):
            job.run(None, self.conn, dry_run=True)
            self.assertEqual(self.rows(), {})
            job.run(None, self.conn)
            job.run(None, self.conn)
            job.run(None, self.conn, reload=True)
        self.assertEqual(seen, [{"balances": None, "transactions": None}, {"balances": None, "transactions": None},
                                {"balances": stop, "transactions": stop}, {"balances": None, "transactions": None}])
        self.assertEqual(set(self.rows()), {"t1"})                    # the reload matched, not doubled
        with db.tx() as conn:                                         # and the app sees it
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM finance.transactions").fetchone()["n"], 1)


if __name__ == "__main__":
    unittest.main()
