"""Merging account records that are one account, and the aliases that keep later loads on it.
Every sponsor, name, number and amount here is made up."""
import unittest
from datetime import date

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import accounts, db, finance, migrate
from virtuwill.importers import load


def setup_accounts(conn, far_apart=False):
    # Earlier imports: friendly names, no digits. A later pull: long names with digits.
    conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, mask, account_type, retirement_type) VALUES
        ('bec-401-a', '', 'BEC 401(a)', NULL, 'retirement', '401a'),
        ('bec-403-b', '', 'BEC 403(b)', NULL, 'retirement', '403b'),
        ('widget-401-k', '', 'Widget 401(k)', NULL, 'retirement', '401k'),
        ('roth-ira', '', 'Roth IRA', NULL, 'retirement', 'roth_ira'),
        ('acct-1001', 'Plan Co', 'BIG EXAMPLE COMPANY, INC. QUALIFIED RETIREMENT PLAN FMTC TRUSTEE', '1001', 'retirement', '401k'),
        ('acct-1002', 'Plan Co', 'BIG EXAMPLE COMPANY, INC. 403(B) RETIREMENT PLAN', '1002', 'retirement', '403b'),
        ('acct-1003', 'Plan Co', 'WIDGET 401K SAVINGS PLAN', '1003', 'retirement', '401k'),
        ('acct-1004', 'Plan Co', 'Health Savings Account', '1004', 'savings', NULL)""")
    rows = [("bec-401-a", "2026-03-01", 4000), ("acct-1001", "2026-03-10", 4010),
            ("bec-403-b", "2026-03-01", 27000), ("acct-1002", "2026-03-10", 27100),
            ("widget-401-k", "2026-03-01", 47000), ("acct-1003", "2026-03-10", 90000 if far_apart else 47200)]
    for account, day, balance in rows:
        conn.execute("""INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, source)
                        VALUES (%s, %s, %s, 'reported', 'manual')""", (day, account, balance))


@needs_database
class AccountMergeTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)

    def test_names_that_name_the_same_account(self):
        same = accounts.same_sponsor
        self.assertTrue(same("BEC 403(b)", "BIG EXAMPLE COMPANY, INC. 403(B) RETIREMENT PLAN"))     # initials
        self.assertTrue(same("Widget 401(k)", "WIDGET 401K SAVINGS PLAN"))
        self.assertFalse(same("Widget 401(k)", "GADGET 401K SAVINGS PLAN"))
        self.assertFalse(same("Roth IRA", "Roth IRA"))                    # no sponsor to go on: never merged by name
        self.assertEqual([accounts.plan_code(n) for n in ("BEC 403(b)", "401K SAVINGS", "Roth IRA", "Brokerage")],
                         ["403b", "401k", "roth_ira", None])

    def test_cleanup_merges_the_certain_pairs_and_keeps_later_loads_on_them(self):
        with db.tx() as conn:
            setup_accounts(conn)
            conn.execute("INSERT INTO finance.savings_goals (goal_id, name, target, account_id) VALUES ('g', 'Made up', 1, 'acct-1003')")
            merged = accounts.cleanup(conn)
        self.assertEqual(sorted((m["merged_account_id"], m["into_account_id"]) for m in merged),
                         [("acct-1001", "bec-401-a"), ("acct-1002", "bec-403-b"), ("acct-1003", "widget-401-k")])
        with db.tx() as conn:
            kept = {r["account_id"]: r for r in conn.execute("SELECT account_id, name, mask FROM finance.accounts")}
            # The friendly name stays; the digits come over; the extra records are gone; the HSA and Roth stay alone.
            self.assertEqual({k: (v["name"], v["mask"]) for k, v in kept.items()},
                             {"bec-401-a": ("BEC 401(a)", "1001"), "bec-403-b": ("BEC 403(b)", "1002"),
                              "widget-401-k": ("Widget 401(k)", "1003"), "roth-ira": ("Roth IRA", None),
                              "acct-1004": ("Health Savings Account", "1004")})
            # Each account's balances are now one history, latest last.
            bal = {r["account_id"]: float(r["balance"]) for r in finance.balances_on(conn, date(2026, 3, 15))}
            self.assertEqual(bal, {"bec-401-a": 4010, "bec-403-b": 27100, "widget-401-k": 47200})
            self.assertEqual(conn.execute("SELECT account_id FROM finance.savings_goals").fetchone()["account_id"], "widget-401-k")
            aliases = {(r["kind"], r["alias"]): r["account_id"] for r in conn.execute("SELECT * FROM finance.account_aliases")}
            self.assertEqual(aliases[("name", "WIDGET 401K SAVINGS PLAN")], "widget-401-k")
            # A later pull with those digits lands on the kept record, never a new one.
            self.assertEqual(load.account_id_for(conn, "1002", {"name": "BIG EXAMPLE COMPANY, INC. 403(B)"}, create=True), "bec-403-b")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM finance.accounts").fetchone()["n"], 5)
            log = conn.execute("SELECT reason, merged_by, rows_moved FROM finance.account_merges WHERE merged_account_id = 'acct-1003'").fetchone()
            self.assertEqual((log["merged_by"], log["rows_moved"]["finance.balance_snapshots"]), ("cleanup", 1))
            self.assertIn("same sponsor and plan", log["reason"])

    def test_uncertain_pairs_are_only_suggested(self):
        with db.tx() as conn:
            setup_accounts(conn, far_apart=True)
            merged = accounts.cleanup(conn)
        self.assertNotIn("acct-1003", [m["merged_account_id"] for m in merged])          # balances far apart
        suggested = self.owner.get("/api/v1/money/account-merges").json["suggestions"]
        self.assertEqual([(s["merge"], s["into"], s["certain"]) for s in suggested], [("acct-1003", "widget-401-k", False)])
        self.assertIn("far apart", suggested[0]["why"])
        # The owner can merge it by hand.
        r = self.owner.post("/api/v1/money/accounts/acct-1003/merge", json={"into": "widget-401-k"})
        self.assertEqual((r.status_code, r.json["merged_by"]), (201, "owner"))
        self.assertEqual(self.owner.get("/api/v1/money/account-merges").json["suggestions"], [])

    def test_the_cleanup_runs_once(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM virtuwill.migrations WHERE name = 'account_cleanup_v1'")
            setup_accounts(conn)
            migrate._account_cleanup(conn)
            conn.execute("""INSERT INTO finance.accounts (account_id, name, mask, account_type, retirement_type)
                            VALUES ('acct-2002', 'BIG EXAMPLE COMPANY 403(B) PLAN', '2002', 'retirement', '403b')""")
            migrate._account_cleanup(conn)          # recorded: a later duplicate is left for review
            self.assertTrue(conn.execute("SELECT 1 FROM finance.accounts WHERE account_id = 'acct-2002'").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM finance.account_merges").fetchone()["n"], 3)

    def test_refusals_and_aliases(self):
        with db.tx() as conn:
            conn.execute("""INSERT INTO finance.accounts (account_id, name, mask, account_type) VALUES
                            ('card', 'Made-up card', '3001', 'credit_card'), ('chk', 'Made-up checking', '3002', 'checking')""")
        post = lambda a, into: self.owner.post(f"/api/v1/money/accounts/{a}/merge", json={"into": into})
        self.assertEqual(post("card", "chk").status_code, 400)
        self.assertEqual(post("chk", "chk").status_code, 400)
        self.assertEqual(post("nope", "chk").status_code, 404)
        self.assertEqual(self.owner.post("/api/v1/money/account-aliases", json={"kind": "mask", "alias": "12", "account_id": "chk"}).status_code, 400)
        self.assertEqual(self.owner.post("/api/v1/money/account-aliases", json={"kind": "mask", "alias": "4004", "account_id": "chk"}).status_code, 201)
        with db.tx() as conn:
            self.assertEqual(load.account_id_for(conn, "4004"), "chk")       # a replaced card number, say
        self.assertEqual(self.owner.delete("/api/v1/money/account-aliases", json={"kind": "mask", "alias": "4004"}).json, {"ok": True})
        self.assertEqual(app.test_client().get("/api/v1/money/account-merges").status_code, 401)


if __name__ == "__main__":
    unittest.main()
