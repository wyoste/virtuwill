"""Finance imports: extracting portal exports into a bundle, staging, committing,
and what Today and the Money overview show from it. All data here is made up."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db, finance
from virtuwill.importers import ExtractError, extract, merge
from virtuwill.importers import chase, kroger
from virtuwill.importers.canonical import from_csv_text, to_csv, validate

REPORT_PAGES = ["""Jan 01, 2026 to Jan 31, 2026 Spending Report 4321
FOOD_AND_DRINK
Transaction Date Posted Date Description Amount
Jan 02, 2026 Jan 03, 2026 Corner Cafe $12.50
Jan 05, 2026 Jan 06, 2026 Taco Stand $8.25
Total $20.75
GROCERIES
Transaction Date Posted Date Description Amount
Jan 07, 2026 Jan 08, 2026 Green Market $40.00
Jan 09, 2026 Jan 10, 2026 Green Market $-5.00
Total $35.00"""]

RECEIPTS_CSV = """date,store,order_total,items_net,sales_tax,savings,item_count,payment,drive_file_id
2026-01-07,Green Market - Main St,40.00,37.50,2.50,3.00,2,VISA 4321 $40.00,f1
"""
ITEMS_CSV = """date,store,item,category,qty,unit,unit_price,regular_price,discount,line_total,store_brand,weighed,drive_file_id
2026-01-07,Green Market - Main St,Apples,Produce,2,lb,2.50,2.50,0,5.00,N,Y,f1
2026-01-07,Green Market - Main St,Rice,Pantry,1,ea,32.50,35.50,3.00,32.50,Y,N,f1
"""
TRANSACTIONS_CSV = """account_mask,transacted_on,posted_on,description,amount,kind,category,source_row
4321,2026-01-14,2026-01-15,Book Shop,30.00,expense,Shopping,1
4321,2026-01-20,2026-01-20,Payment Thank You,-100.00,card_payment,,2
"""


class ExtractTests(unittest.TestCase):
    def test_spending_report_reads_rows_and_checks_category_totals(self):
        bundle = chase.spending_report("report.pdf", b"x", REPORT_PAGES)
        self.assertEqual(len(bundle["transactions"]), 4)
        self.assertEqual({t["category"] for t in bundle["transactions"]}, {"Dining", "Groceries"})
        self.assertEqual(bundle["transactions"][3]["amount"], -5.0)
        self.assertTrue(all(c["ok"] for c in bundle["document"]["checks"].values()))
        self.assertEqual(bundle["accounts"][0]["mask"], "4321")

    def test_a_misread_row_fails_its_total(self):
        pages = [REPORT_PAGES[0].replace("Total $20.75", "Total $21.75")]
        checks = chase.spending_report("report.pdf", b"x", pages)["document"]["checks"]
        self.assertFalse(checks["FOOD_AND_DRINK"]["ok"])

    def test_receipts_and_their_lines_join_and_add_up(self):
        bundle = merge([extract("receipts.csv", RECEIPTS_CSV.encode()), extract("items.csv", ITEMS_CSV.encode())])
        receipt = bundle["receipts"][0]
        self.assertEqual((receipt["merchant"], receipt["store_location"], receipt["account_mask"]), ("Green Market", "Main St", "4321"))
        self.assertEqual([i["item_name"] for i in receipt["items"]], ["Apples", "Rice"])
        self.assertTrue(bundle["document"]["checks"]["rcpt-f1"]["ok"])

    def test_canonical_csv_round_trips(self):
        bundle = extract("transactions.csv", TRANSACTIONS_CSV.encode())
        with tempfile.TemporaryDirectory() as folder:
            paths = to_csv(bundle, folder)
            self.assertEqual({p.name for p in paths}, {"accounts.csv", "transactions.csv"})
            text = Path(folder, "transactions.csv").read_text()
        again = from_csv_text("transactions.csv", text.encode(), text)
        self.assertEqual([(t["posted_on"], t["amount"], t["kind"]) for t in again["transactions"]],
                         [("2026-01-15", 30.0, "expense"), ("2026-01-20", -100.0, "card_payment")])

    def test_bad_input_is_explained(self):
        with self.assertRaisesRegex(ExtractError, "not a CSV"):
            extract("x.csv", b"a,b\n1,2\n")
        with self.assertRaisesRegex(ExtractError, "not valid JSON"):
            extract("x.json", b"{nope")
        with self.assertRaisesRegex(ExtractError, "last four digits"):
            extract("t.csv", TRANSACTIONS_CSV.replace("4321,2026-01-14", "43,2026-01-14").encode())
        bundle = extract("t.csv", TRANSACTIONS_CSV.encode())
        bundle["transactions"][0]["kind"] = "gift"
        with self.assertRaisesRegex(ExtractError, "kind must be"):
            validate(bundle)


def upload(client, *files):
    return client.post("/api/v1/money/imports", content_type="multipart/form-data",
                       data={"files": [(io.BytesIO(content.encode()), name) for name, content in files]})


@needs_database
class ImportApiTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)

    def test_owner_only(self):
        visitor = app.test_client()
        for path in ("/api/v1/money/imports", "/api/v1/money/balances"):
            self.assertEqual(visitor.get(path).status_code, 401)
        self.assertEqual(upload(visitor, ("t.csv", TRANSACTIONS_CSV)).status_code, 401)

    def test_stage_commit_and_a_second_load_changes_nothing(self):
        staged = upload(self.owner, ("transactions.csv", TRANSACTIONS_CSV))
        self.assertEqual(staged.status_code, 201, staged.json)
        self.assertEqual(staged.json["preview"]["counts"]["transactions"], {"new": 2, "seen": 0, "taken_over": 0})
        with db.tx() as conn:   # nothing is loaded until it's committed
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM finance.transactions").fetchone()["n"], 0)
        done = self.owner.post(f"/api/v1/money/imports/{staged.json['import_id']}/commit")
        self.assertEqual(done.json["report"]["transactions"]["new"], 2)
        self.assertEqual(self.owner.post(f"/api/v1/money/imports/{staged.json['import_id']}/commit").status_code, 409)

        again = upload(self.owner, ("transactions.csv", TRANSACTIONS_CSV))
        self.assertTrue(any("already loaded" in w for w in again.json["preview"]["warnings"]))
        self.assertIn("skipped", self.owner.post(f"/api/v1/money/imports/{again.json['import_id']}/commit").json["report"])
        with db.tx() as conn:
            rows = conn.execute("SELECT kind, movement_type FROM finance.transactions ORDER BY amount").fetchall()
            self.assertEqual([(r["kind"], r["movement_type"]) for r in rows], [("movement", "Credit card payment"), ("expense", None)])
            # The uploaded file is kept, privately.
            asset = conn.execute("SELECT visibility FROM core.media_assets WHERE path LIKE 'private/finance/%'").fetchone()
            self.assertEqual(asset["visibility"], "private")

    def test_discard_and_download(self):
        staged = upload(self.owner, ("receipts.csv", RECEIPTS_CSV), ("items.csv", ITEMS_CSV)).json
        self.assertEqual(staged["preview"]["counts"]["receipts"]["new"], 1)
        bundle = self.owner.get(f"/api/v1/money/imports/{staged['import_id']}/bundle")
        self.assertEqual(json.loads(bundle.data)["receipts"][0]["items"][1]["item_name"], "Rice")
        zipped = self.owner.get(f"/api/v1/money/imports/{staged['import_id']}/bundle?format=csv")
        self.assertEqual(zipped.mimetype, "application/zip")
        self.assertEqual(self.owner.delete(f"/api/v1/money/imports/{staged['import_id']}").status_code, 200)
        self.assertEqual(self.owner.delete(f"/api/v1/money/imports/{staged['import_id']}").status_code, 409)
        self.assertEqual(self.owner.post("/api/v1/money/extract", content_type="multipart/form-data",
                                         data={"files": [(io.BytesIO(b"%PDF nope"), "x.exe")]}).status_code, 400)

    def test_an_import_takes_over_the_trackers_copy_and_the_tracker_does_not_recreate_it(self):
        state = {"transactions": [{"id": "t1", "date": "2026-01-14", "amount": 30, "account": "Chase 4321",
                                   "merchant": "Book Shop", "category": "Books"}]}
        with db.tx() as conn:
            finance.project(conn, state)
        staged = upload(self.owner, ("transactions.csv", TRANSACTIONS_CSV)).json
        self.assertEqual(staged["preview"]["counts"]["transactions"]["taken_over"], 1)
        self.owner.post(f"/api/v1/money/imports/{staged['import_id']}/commit")
        with db.tx() as conn:
            finance.project(conn, state)   # the tracker saves again
            rows = conn.execute("SELECT transaction_id, source, category FROM finance.transactions WHERE amount = 30").fetchall()
        self.assertEqual([(r["transaction_id"], r["source"], r["category"]) for r in rows], [("t1", "import", "Books")])

    def test_balances_estimate_only_when_payments_are_loaded(self):
        charges_only = TRANSACTIONS_CSV.splitlines()[:2]
        statement = {"format": "virtuwill-finance/1", "document": {"filename": "s.json", "sha256": "a" * 64, "parser": "test"},
                     "accounts": [{"mask": "4321", "institution": "Test", "name": "Card 4321", "account_type": "credit_card"}],
                     "balances": [{"account_mask": "4321", "as_of": "2026-01-10", "balance": 200, "kind": "reported"}]}
        for name, content in (("s.json", json.dumps(statement)), ("charges.csv", "\n".join(charges_only) + "\n")):
            self.owner.post(f"/api/v1/money/imports/{upload(self.owner, (name, content)).json['import_id']}/commit")
        card = self.owner.get("/api/v1/money/balances").json["balances"][0]
        self.assertEqual((card["balance"], card["outflows_since"], card["estimate_complete"]), (200, 30, False))
        self.owner.post(f"/api/v1/money/imports/{upload(self.owner, ('all.csv', TRANSACTIONS_CSV)).json['import_id']}/commit")
        card = self.owner.get("/api/v1/money/balances").json["balances"][0]
        self.assertEqual((card["estimated_balance"], card["estimate_complete"]), (130, True))   # 200 + 30 charged − 100 paid

        bad = self.owner.post("/api/v1/money/balances", json={"account_id": card["account_id"], "balance": "lots"})
        self.assertEqual(bad.status_code, 400)
        ok = self.owner.post("/api/v1/money/balances", json={"account_id": card["account_id"], "balance": 55.5, "as_of": "2026-01-25"})
        card = ok.json["balances"][0]
        self.assertEqual((card["balance"], card["transactions_since"], card["balance_source"]), (55.5, 0, "manual"))

    def test_today_and_overview_carry_balances_spend_and_the_mode(self):
        self.owner.post(f"/api/v1/money/imports/{upload(self.owner, ('t.csv', TRANSACTIONS_CSV)).json['import_id']}/commit")
        spend = self.owner.get("/api/v1/today?date=2026-01-15").json["money"]["spend"]
        self.assertEqual((spend["day"], spend["month"], len(spend["days"])), (30, 30, 14))
        self.assertEqual(self.owner.get("/api/v1/money/overview").json["mode"], "full")
        self.assertEqual(self.owner.put("/api/settings", json={"money.overview_mode": "charts"}).status_code, 400)
        self.owner.put("/api/settings", json={"money.overview_mode": "goals"})
        self.assertEqual(self.owner.get("/api/v1/money/overview").json["mode"], "goals")
        self.assertNotIn("money.overview_mode", app.test_client().get("/api/settings").json)


if __name__ == "__main__":
    unittest.main()
