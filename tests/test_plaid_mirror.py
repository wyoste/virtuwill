"""Plaid served from the lakehouse: the synced tables in Lakebase → the finance model, and the job that refreshes them.
The synced tables are stood in for by plain tables of the same shape; everything in them is made up."""
import importlib.util
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from tests.support import PG, admin_client, fresh_database, needs_database
from app import app
from virtuwill import db
from virtuwill.importers import plaid

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("plaid_lakebase_refresh", ROOT / "jobs" / "plaid_lakebase_refresh.py")
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)

MIRROR = """
DROP TABLE IF EXISTS public.plaid_transactions, public.plaid_balances;
CREATE TABLE public.plaid_transactions (
    transaction_id TEXT PRIMARY KEY, account_id TEXT, item_label TEXT, date DATE, authorized_date DATE, name TEXT,
    merchant_name TEXT, amount NUMERIC, iso_currency_code TEXT, category TEXT, pending BOOLEAN, synced_at TIMESTAMPTZ {extra});
CREATE TABLE public.plaid_balances (
    account_id TEXT NOT NULL, item_label TEXT, institution_id TEXT, account_name TEXT, official_name TEXT, mask TEXT,
    type TEXT, subtype TEXT, current NUMERIC, available NUMERIC, limit_amt NUMERIC, iso_currency_code TEXT,
    as_of TIMESTAMPTZ NOT NULL, PRIMARY KEY (account_id, as_of));
"""


def at(day, hour=13):
    return datetime.fromisoformat(f"{day}T{hour:02d}:30:00").replace(tzinfo=timezone.utc)


class MappingTests(unittest.TestCase):
    def test_personal_finance_category_from_what_the_lakehouse_keeps(self):
        self.assertEqual(plaid.personal_finance_category("FOOD_AND_DRINK_GROCERIES"),
                         {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"})
        self.assertEqual(plaid.personal_finance_category("income"), {"primary": "INCOME", "detailed": ""})
        self.assertEqual(plaid.personal_finance_category('{"primary": "TRAVEL", "detailed": "TRAVEL_FLIGHTS"}')["primary"], "TRAVEL")
        self.assertEqual(plaid.personal_finance_category('["Food and Drink", "Restaurants"]'), {})
        self.assertEqual(plaid.personal_finance_category("SOMETHING_ELSE"), {})
        self.assertEqual(plaid.personal_finance_category("Food and Drink > Restaurants"), {})   # the notebook's older-category path
        self.assertEqual(plaid.personal_finance_category(None), {})
        t = {"amount": -40, "personal_finance_category": plaid.personal_finance_category("LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")}
        self.assertEqual(plaid.kind_and_category(t), ("card_payment", None))


class RefreshJobTests(unittest.TestCase):
    def test_the_sql_files_split_into_their_statements(self):
        create = job.statements((job.SQL_DIR / "plaid_serving_create.sql").read_text())
        refresh = job.statements((job.SQL_DIR / "plaid_serving_refresh.sql").read_text())
        self.assertEqual([s.split()[:2] for s in create], [["CREATE", "TABLE"]] * 2)
        self.assertEqual([s.split()[0] for s in refresh], ["MERGE", "MERGE"])
        self.assertTrue(all(";" not in s for s in create + refresh))

    def test_waits_for_every_synced_table_and_stops_before_the_app_on_a_failure(self):
        class Pipelines:
            def __init__(self, finals):
                self.finals, self.polls = finals, {}

            def start_update(self, pid):
                return type("R", (), {"update_id": "u-" + pid})()

            def get_update(self, pid, update_id):
                self.polls[pid] = self.polls.get(pid, 0) + 1
                state = "RUNNING" if self.polls[pid] < 2 else self.finals[pid]
                return type("R", (), {"update": type("U", (), {"state": state})()})()

        w = type("W", (), {})()
        w.pipelines = Pipelines({"p1": "COMPLETED", "p2": "COMPLETED"})
        self.assertEqual(job.refresh(w, ["p1", "p2"], sleep=lambda s: None), {"p1": "COMPLETED", "p2": "COMPLETED"})

        w.pipelines = Pipelines({"p1": "COMPLETED", "p2": "FAILED"})
        told = []
        with unittest.mock.patch.dict("os.environ", {"PLAID_SYNC_PIPELINE_IDS": "p1,p2"}):
            with unittest.mock.patch.object(job.time, "sleep", lambda s: None):
                code = job.run(only=None, spark=FakeSpark(), workspace=w, post=lambda body: told.append(body) or (200, {}))
        self.assertEqual(code, 1)
        self.assertEqual(told, [])


class FakeSpark:
    def __init__(self):
        self.ran = []
        self.catalog = type("C", (), {"tableExists": lambda _, name: True})()

    def sql(self, text):
        self.ran.append(text)

    def table(self, name):
        return type("T", (), {"count": lambda _: 0})()


@needs_database
class MirrorTests(unittest.TestCase):
    extra = ""

    def setUp(self):
        fresh_database()
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute(MIRROR.format(extra=self.extra))
        self.addCleanup(self.drop_mirror)
        self.owner = admin_client(app)

    def drop_mirror(self):
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS public.plaid_transactions, public.plaid_balances")

    def mirror(self, sql, *rows):
        with psycopg.connect(PG, autocommit=True) as conn:
            for row in rows or [None]:
                conn.execute(sql, row)

    def balance(self, account_id, mask, kind, subtype, current, available, when, bank="Test Bank"):
        self.mirror("""INSERT INTO public.plaid_balances (account_id, item_label, account_name, mask, type, subtype, current,
                                                          available, iso_currency_code, as_of)
                       VALUES (%s, %s, 'Made-up account', %s, %s, %s, %s, %s, 'USD', %s)""",
                    (account_id, bank, mask, kind, subtype, current, available, when))

    def txn(self, tid, account_id, amount, category, when, day="2026-03-02", pending=False, **extra):
        cols = ["transaction_id", "account_id", "item_label", "date", "name", "merchant_name", "amount", "iso_currency_code",
                "category", "pending", "synced_at", *extra]
        values = [tid, account_id, "Test Bank", day, "MADE UP MERCHANT", "Made Up Merchant", amount, "USD", category, pending,
                  when, *extra.values()]
        self.mirror(f"""INSERT INTO public.plaid_transactions ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})
                        ON CONFLICT (transaction_id) DO UPDATE SET amount = EXCLUDED.amount, pending = EXCLUDED.pending,
                        synced_at = EXCLUDED.synced_at""", tuple(values))

    def sync(self, **body):
        response = self.owner.post("/api/v1/money/plaid-mirror/sync", json=body)
        return response.status_code, response.json

    def rows(self):
        with db.tx() as conn:
            return {r["external_id"]: r for r in conn.execute(
                "SELECT external_id, amount, is_pending, kind, category, movement_type FROM finance.transactions")}

    def test_missing_synced_tables_are_reported_not_loaded(self):
        self.drop_mirror()
        status, reply = self.sync()
        self.assertEqual(status, 409)
        self.assertIn("public.plaid_transactions", reply["error"])
        self.assertFalse(self.owner.get("/api/v1/money/plaid-mirror").json["available"])
        diag = self.owner.get("/api/admin/diagnostics").json
        self.assertIn("plaid_transactions", next(s for s in diag["syncs"] if s["source"] == "plaid_mirror")["error"])

    def test_loads_only_what_changed_and_drops_pending_charges_plaid_dropped(self):
        self.balance("chk", "1111", "depository", "checking", 500, 480, at("2026-03-02"))
        self.balance("card", "2222", "credit", "credit card", 120.5, None, at("2026-03-02"))
        self.balance("nomask", None, "depository", "savings", 10, 10, at("2026-03-02"))
        self.txn("t1", "card", 12.5, "FOOD_AND_DRINK_COFFEE", at("2026-03-02"))
        self.txn("t2", "chk", -2000, "INCOME_WAGES", at("2026-03-02"))
        self.txn("p1", "card", 30, "GENERAL_MERCHANDISE", at("2026-03-02"), pending=True)
        self.txn("t9", "nomask", 5, "BANK_FEES", at("2026-03-02"))

        status, preview = self.sync(dry_run=True)
        self.assertEqual(status, 200)
        self.assertEqual(preview["preview"]["transactions"]["new"], 3)
        self.assertEqual(self.rows(), {})

        status, first = self.sync()
        self.assertEqual(status, 200)
        self.assertEqual((first["accounts"], first["transactions"], first["transactions_without_account"]), (2, 3, 1))
        self.assertEqual(len(first["skipped_accounts"]), 1)
        rows = self.rows()
        self.assertEqual(set(rows), {"t1", "p1", "t2"})
        self.assertEqual((rows["t1"]["category"], rows["t2"]["movement_type"]), ("Dining", "Payroll deposit"))
        self.assertTrue(rows["p1"]["is_pending"])
        with db.tx() as conn:
            balances = {(r["mask"], r["balance_kind"]): float(r["balance"]) for r in conn.execute(
                """SELECT a.mask, b.balance_kind, b.balance FROM finance.balance_snapshots b JOIN finance.accounts a USING (account_id)""")}
        self.assertEqual(balances, {("1111", "reported"): 500, ("1111", "available"): 480, ("2222", "reported"): 120.5})

        status, again = self.sync()
        self.assertTrue(again.get("nothing_new"))

        # The next day: the pending charge settles under a new id and Plaid drops the pending one.
        self.mirror("DELETE FROM public.plaid_transactions WHERE transaction_id = 'p1'")
        self.txn("s1", "card", 32.5, "GENERAL_MERCHANDISE", at("2026-03-03"), day="2026-03-03")
        self.balance("card", "2222", "credit", "credit card", 153, None, at("2026-03-03"))
        status, later = self.sync()
        self.assertEqual((later["transactions"], later["balances"], later["pending_removed"]), (1, 1, 1))
        rows = self.rows()
        self.assertEqual(set(rows), {"t1", "t2", "s1"})
        with db.tx() as conn:
            owed = conn.execute("""SELECT l.balance FROM finance.latest_balances l JOIN finance.accounts a USING (account_id)
                                   WHERE a.mask = '2222'""").fetchone()
        self.assertEqual(float(owed["balance"]), 153)

        mirror = self.owner.get("/api/v1/money/plaid-mirror").json
        self.assertEqual((mirror["transactions"]["rows"], mirror["transactions"]["not_loaded"]), (4, 0))
        self.assertEqual(mirror["last_sync"]["transactions"], 1)

    def test_retirement_accounts_load_with_their_plan(self):
        self.balance("roth", "3333", "investment", "roth", 9000, None, at("2026-03-02"), bank="Fidelity")
        self.balance("k", "4444", "investment", "401k", 52000, None, at("2026-03-02"), bank="Fidelity")
        self.balance("hsa", "5555", "investment", "hsa", 1200, None, at("2026-03-02"), bank="Fidelity")
        status, reply = self.sync()
        self.assertEqual(status, 200, reply)
        with db.tx() as conn:
            got = {r["mask"]: (r["institution"], r["account_type"], r["retirement_type"]) for r in conn.execute(
                "SELECT mask, institution, account_type, retirement_type FROM finance.accounts")}
        self.assertEqual(got, {"3333": ("Fidelity", "retirement", "roth_ira"), "4444": ("Fidelity", "retirement", "401k"),
                               "5555": ("Fidelity", "brokerage", None)})

    def test_scheduled_jobs_sync_with_an_api_token(self):
        self.balance("card", "2222", "credit", "credit card", 80, None, at("2026-03-02"))
        self.txn("t1", "card", 20, "FOOD_AND_DRINK", at("2026-03-02"))
        token = self.owner.post("/api/v1/api-tokens", json={"name": "Lakebase refresh"}).json["token"]
        client = app.test_client()
        self.assertEqual(client.post("/api/ingest/v1/finance/plaid-mirror", json={}).status_code, 401)
        reply = client.post("/api/ingest/v1/finance/plaid-mirror", json={}, headers={"X-VirtuWill-Token": token})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json["transactions"], 1)
        code = job.run(only="app", post=lambda body: (lambda r: (r.status_code, r.json))(
            client.post("/api/ingest/v1/finance/plaid-mirror", json=body, headers={"X-VirtuWill-Token": token})))
        self.assertEqual(code, 0)
        self.assertEqual(set(self.rows()), {"t1"})


@needs_database
class MirrorWithPendingIdsTests(MirrorTests):
    """When the serving table also keeps Plaid's pending_transaction_id, a settled charge updates its pending row in place,
    with the same id the direct Plaid job gives it."""
    extra = ", pending_transaction_id TEXT"

    def test_loads_only_what_changed_and_drops_pending_charges_plaid_dropped(self):
        self.balance("card", "2222", "credit", "credit card", 80, None, at("2026-03-02"))
        self.txn("p1", "card", 30, "GENERAL_MERCHANDISE", at("2026-03-02"), pending=True)
        self.sync()
        self.mirror("DELETE FROM public.plaid_transactions WHERE transaction_id = 'p1'")
        self.txn("s1", "card", 32.5, "GENERAL_MERCHANDISE", at("2026-03-03"), day="2026-03-03", pending_transaction_id="p1")
        status, later = self.sync()
        self.assertEqual(later["pending_removed"], 0)
        rows = self.rows()
        self.assertEqual(set(rows), {"p1"})
        self.assertEqual((float(rows["p1"]["amount"]), rows["p1"]["is_pending"]), (32.5, False))


if __name__ == "__main__":
    unittest.main()
