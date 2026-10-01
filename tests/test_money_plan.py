"""The money plan edited in Money (the Finance tracker is retired): budgets, bills, pay and retirement.
All names and amounts here are made up."""
import unittest

from tests.support import admin_client, fresh_database, needs_database
from app import app
from virtuwill import db


@needs_database
class MoneyPlanTests(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.owner = admin_client(app)
        with db.tx() as conn:
            conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, mask, account_type)
                            VALUES ('test-checking', 'Test Bank', 'Everyday', '0003', 'checking')""")

    def add(self, kind, **body):
        return self.owner.post(f"/api/v1/money/plan/{kind}", json=body)

    def test_budgets_with_categories(self):
        r = self.add("budgets", name="Eating out", basis="fixed", amount_monthly=300, categories=["Dining", "Coffee"])
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual((r.json["budget_id"], r.json["categories"]), ("eating-out", ["Coffee", "Dining"]))
        # A category belongs to one budget: naming it in another moves it.
        treats = self.add("budgets", name="Treats", basis="per_paycheck", per_paycheck=50, paychecks_per_year=26, categories="Coffee").json
        self.assertEqual(treats["categories"], ["Coffee"])
        self.assertEqual(self.owner.get("/api/v1/money/plan/budgets").json[0]["categories"], ["Dining"])
        effective = {b["name"]: b["monthly_amount"] for b in self.owner.get("/api/v1/money/budgets").json["budgets"]}
        self.assertEqual(effective, {"Eating out": 300, "Treats": 108.33})
        edited = self.owner.put("/api/v1/money/plan/budgets/eating-out", json={"amount_monthly": 250, "note": "made up"}).json
        self.assertEqual((edited["amount_monthly"], edited["categories"]), (250, ["Dining"]))
        for bad, message in (({"name": "No amount", "basis": "fixed"}, "monthly amount"),
                             ({"name": "Half", "basis": "per_paycheck", "per_paycheck": 10}, "per paycheck"),
                             ({"basis": "fixed", "amount_monthly": 5}, "name")):
            r = self.add("budgets", **bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(message, r.json["error"])
        self.assertEqual(self.owner.delete("/api/v1/money/plan/budgets/treats").json, {"ok": True})
        self.assertIsNone(db.one("SELECT 1 FROM finance.budget_categories WHERE budget_id = 'treats'"))

    def test_bills_incomes_allocations_and_deposits(self):
        bill = self.add("bills", name="Streaming", amount=15.99, frequency="Monthly", due_day=12, category="Subscriptions",
                        account_id="test-checking").json
        self.assertEqual((bill["expense_id"], bill["status"]), ("streaming", "Active"))
        self.assertEqual(self.owner.get("/api/v1/money/budgets").json["bills"][0]["name"], "Streaming")
        self.assertEqual(self.add("bills", name="Ghost", category="Other", account_id="nope").status_code, 400)
        self.assertEqual(self.add("bills", name="Odd", category="Other", frequency="Daily").status_code, 400)
        self.assertEqual(self.owner.put("/api/v1/money/plan/bills/streaming", json={"status": "Canceled"}).json["status"], "Canceled")
        self.assertEqual(self.add("incomes", name="Side gig", amount=200, frequency="Monthly").status_code, 201)
        self.assertEqual(self.add("allocations", name="Travel jar", amount=100).json["cadence"], "Biweekly")
        first = self.add("deposits", destination_text="Everyday", account_id="test-checking", amount=1000).json
        second = self.add("deposits", destination_text="Savings", amount=250).json
        self.assertEqual((first["position"], second["position"]), (0, 1))
        self.assertEqual(self.owner.put("/api/v1/money/plan/deposits/1", json={"amount": 300}).json["amount"], 300)
        self.assertEqual(self.owner.delete("/api/v1/money/plan/deposits/x").status_code, 404)
        goals = self.owner.get("/api/v1/money/goals").json
        self.assertEqual([d["amount"] for d in goals["deposits"]], [1000, 300])
        self.assertEqual([a["name"] for a in goals["allocations"]], ["Travel jar"])

    def test_pay_and_retirement_plan_by_date(self):
        pay = self.add("pay", as_of="2026-01-02", net_pay=2000, gross_pay=3000, checks_per_year=26, anchor_date="2026-01-02")
        self.assertEqual(pay.status_code, 201, pay.json)
        self.assertEqual(self.add("pay", as_of="2026-01-02", net_pay=1, gross_pay=1, checks_per_year=26,
                                  anchor_date="2026-01-02").status_code, 409)
        self.assertEqual(self.owner.put("/api/v1/money/plan/pay/2026-01-02", json={"net_pay": 2100}).json["net_pay"], 2100)
        plan = self.add("retirement-plan", as_of="2026-01-02", employee_ytd=500, deferral_limit=24500, remaining_checks=20)
        self.assertEqual(plan.status_code, 201, plan.json)
        summary = self.owner.get("/api/v1/money/goals").json["retirement"]
        self.assertEqual((summary["employee_ytd"], summary["deferral_room"]), (500, 24000))

    def test_unknown_kinds_and_access(self):
        self.assertEqual(self.owner.get("/api/v1/money/plan/nope").status_code, 404)
        self.assertEqual(self.owner.put("/api/v1/money/plan/bills/nope", json={"amount": 1}).status_code, 404)
        self.assertEqual(app.test_client().get("/api/v1/money/plan/budgets").status_code, 401)
        options = self.owner.get("/api/v1/money/plan-options").json
        self.assertIn("Review", options["categories"])
        self.assertEqual([a["account_id"] for a in options["accounts"]], ["test-checking"])


if __name__ == "__main__":
    unittest.main()
