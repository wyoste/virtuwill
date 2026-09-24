"""Finance: accounts, the journal's account template and balances, and the
Finance tracker's data projected into the finance schema.

Until Money screens replace it, the embedded Finance tracker is the editing
format. Every save copies its records here: rows with source 'finance_tracker'
are replaced, and the plan tables (budgets, bills, pay, allocations, goals,
retirement plan, shopping list) are rewritten from the tracker. Accounts are
only ever created, never overwritten, so names and types set by hand stick.
"""
import collections
import hashlib
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from . import db
from .auth import admin_required
from .util import in_calendar, number, parse_date, plain, slug

bp = Blueprint("finance", __name__)
SOURCE = "finance_tracker"
FREQUENCIES = {"Weekly", "Biweekly", "Monthly", "Annual"}
CARD_WORDS = ("VISA", "MASTERCARD", "AMEX", "DISCOVER", "CREDIT")


# ── Accounts ─────────────────────────────────────────────────────────────────

def _guess_type(text, hint=None):
    upper = text.upper()
    if hint:
        return hint
    if upper.startswith(CARD_WORDS) or "CARD" in upper:
        return "credit_card"
    if "SAVINGS" in upper or "HYSA" in upper:
        return "savings"
    if "CHECKING" in upper or "DEBIT" in upper:
        return "checking"
    return "other"


def _retirement_type(name):
    lower = name.lower()
    if "roth" in lower:
        return "roth_ira"
    if "ira" in lower:
        return "traditional_ira"
    for plan in ("403(b)", "401(a)", "401(k)"):
        if plan in lower:
            return plan.replace("(", "").replace(")", "")
    return "401k"


def account_for(conn, text, hint=None):
    """The account a source's name refers to, created on first sight.

    Names that end in the same four digits are one account ('Chase 1234' and
    'VISA 1234 $10.00'); every spelling is kept as an alias.
    """
    text = re.sub(r"\s+\$[\d,.]+$", "", str(text or "")).strip()
    if not text:
        return None
    row = conn.execute("SELECT account_id FROM finance.account_aliases WHERE alias = %s", (text,)).fetchone()
    if row:
        return row["account_id"]
    masks = re.findall(r"(?<!\d)(\d{4})(?!\d)", text)
    if masks and hint != "retirement":
        account_id = f"acct-{masks[-1]}"
        name = re.sub(r"(?<!\d)\d{4}(?!\d)", "", text).strip(" ·-") or text
        institution = "" if name.upper().startswith(CARD_WORDS + ("DEBIT", "CHECKING", "SAVINGS")) else name.split()[0]
        conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, mask, account_type)
                        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING""",
                     (account_id, institution, name, masks[-1], _guess_type(text)))
    else:
        account_id = slug(text)
        retirement = _retirement_type(text) if hint == "retirement" else None
        conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, account_type, retirement_type, purpose)
                        VALUES (%s, '', %s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING""",
                     (account_id, text, _guess_type(text, hint), retirement, "Retirement" if retirement else ""))
    conn.execute("INSERT INTO finance.account_aliases VALUES (%s, %s) ON CONFLICT DO NOTHING", (text, account_id))
    return account_id


def _journal_account(conn, institution, name):
    row = conn.execute("""SELECT account_id FROM finance.accounts
                          WHERE lower(institution) = lower(%s) AND lower(name) = lower(%s)""",
                       (institution, name)).fetchone()
    return row["account_id"] if row else None


def write_journal_balances(conn, entry_id, day, accounts):
    """An entry's account list, kept as balance snapshots on the entry's date."""
    conn.execute("DELETE FROM finance.balance_snapshots WHERE source = 'journal' AND source_ref = %s", (entry_id,))
    for position, account in enumerate(accounts):
        institution = str(account.get("institution") or "").strip()
        name = str(account.get("name") or "").strip()
        balance = number(account.get("balance"))
        if not institution and not name and balance is None:
            continue
        conn.execute(
            """INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, institution_text,
                                                      account_text, position, source, source_ref)
               VALUES (%s, %s, %s, 'journal', %s, %s, %s, 'journal', %s)""",
            (day, _journal_account(conn, institution, name), balance, institution, name, position, entry_id))


def accounts_template(conn):
    return [{"institution": r["institution"], "name": r["name"]} for r in conn.execute(
        "SELECT institution, name FROM finance.accounts WHERE show_in_journal ORDER BY journal_position, name")]


def set_accounts_template(conn, rows):
    conn.execute("UPDATE finance.accounts SET show_in_journal = false, journal_position = NULL")
    for position, row in enumerate(rows or []):
        institution = str(row.get("institution") or "").strip()
        name = str(row.get("name") or "").strip()
        if not institution and not name:
            continue
        account_id = _journal_account(conn, institution, name)
        if not account_id:
            account_id = slug(f"{institution} {name}")
            conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, account_type)
                            VALUES (%s, %s, %s, 'other') ON CONFLICT (account_id) DO NOTHING""",
                         (account_id, institution, name or institution))
        conn.execute("UPDATE finance.accounts SET show_in_journal = true, journal_position = %s WHERE account_id = %s",
                     (position, account_id))


@bp.route("/api/accounts-template", methods=["GET"])
@admin_required
def template_get():
    with db.tx() as conn:
        return jsonify(accounts_template(conn))


@bp.route("/api/accounts-template", methods=["POST"])
@admin_required
def template_post():
    data = request.get_json(force=True) or []
    if not isinstance(data, list):
        return jsonify({"error": "Expected a list of accounts"}), 400
    with db.tx() as conn:
        set_accounts_template(conn, data)
    return jsonify({"ok": True})


# ── Finance tracker projection ───────────────────────────────────────────────

def _document(conn, source_text, account_id):
    """The statement a tracker row names ('file.pdf, p. 5'), registered by name."""
    name, _, page = str(source_text).partition(", ")
    name = name.strip()
    if not name:
        return None, ""
    fmt = "csv" if name.lower().endswith(".csv") else "pdf" if name.lower().endswith(".pdf") else "other"
    row = conn.execute(
        """INSERT INTO finance.source_documents (original_filename, doc_type, file_format, account_id, status)
           VALUES (%s, %s, %s, %s, 'parsed')
           ON CONFLICT (source_folder, original_filename) WHERE sha256 IS NULL
           DO UPDATE SET account_id = COALESCE(finance.source_documents.account_id, EXCLUDED.account_id)
           RETURNING document_id""",
        (name, "transaction_export" if fmt == "csv" else "statement", fmt, account_id)).fetchone()
    return row["document_id"], page


def _pool_members(note):
    match = re.search(r"pool:\s*([^.]*)", note or "", re.I)
    if not match:
        return []
    return [p.strip() for p in re.split(r",\s*|\s+and\s+", match.group(1)) if p.strip()]


def _category(conn, name, group=None):
    conn.execute("""INSERT INTO finance.categories (category, category_group, needs_review) VALUES (%s, %s, %s)
                    ON CONFLICT (category) DO UPDATE SET category_group = COALESCE(EXCLUDED.category_group, finance.categories.category_group)""",
                 (name, group, name == "Review"))


def project(conn, state):
    """Replace the tracker's rows in the finance schema; returns a sync report."""
    report = {"skipped": collections.Counter(), "syncedAt": datetime.now(timezone.utc).isoformat()}
    counts = collections.Counter()

    def skip(kind):
        report["skipped"][kind] += 1

    conn.execute("DELETE FROM finance.receipts WHERE source = %s", (SOURCE,))
    conn.execute("DELETE FROM finance.transactions WHERE source = %s", (SOURCE,))
    conn.execute("DELETE FROM finance.balance_snapshots WHERE source = %s", (SOURCE,))
    for table in ("budgets", "recurring_expenses", "pay_profile", "paycheck_deposits", "other_incomes",
                  "savings_goals", "allocations", "retirement_plan", "shopping_list", "item_catalog"):
        conn.execute(f"DELETE FROM finance.{table}")

    budgets = state.get("budgets") or []
    pooled = {c: "Discretionary" for b in budgets for c in _pool_members(b.get("note"))}
    for name in sorted({t.get("category") for t in state.get("transactions") or [] if t.get("category")}
                       | set(pooled) | {e.get("category") for e in state.get("expenses") or [] if e.get("category")}
                       | {b.get("category") for b in budgets if b.get("category") and not _pool_members(b.get("note"))}):
        _category(conn, name, pooled.get(name))

    # Bank activity: expenses and movements.
    seen = collections.Counter()
    claimed = set()
    for t in (state.get("transactions") or []) + (state.get("movements") or []):
        day = parse_date(t.get("date"))
        amount = number(t.get("amount"))
        if not t.get("id") or not in_calendar(day) or amount is None:
            skip("transactions")
            continue
        movement = t.get("kind") == "movement"
        account_id = account_for(conn, t.get("account"))
        key = (account_id, day.isoformat(), amount, t.get("merchant"))
        seen[key] += 1
        fingerprint = hashlib.sha256(repr((*key, seen[key])).encode()).hexdigest()[:20]
        if movement:
            conn.execute("""INSERT INTO finance.movement_types (movement_type, flow, confirmed) VALUES (%s, %s, %s)
                            ON CONFLICT DO NOTHING""",
                         (t.get("classification") or "Unclassified",
                          "unconfirmed" if "unconfirmed" in str(t.get("classification")).lower() else "transfer",
                          "unconfirmed" not in str(t.get("classification")).lower()))
        else:
            _category(conn, t.get("category") or "Review")
        # The same bank transaction loaded from a statement or export belongs to that import now.
        # Each imported row stands in for at most one tracker row (its own id first, if it took that one over).
        imported = conn.execute("""SELECT transaction_id FROM finance.transactions WHERE source = 'import' AND account_id = %s
                                   AND amount = %s AND posted_on BETWEEN %s AND %s AND NOT (transaction_id = ANY(%s))
                                   ORDER BY (transaction_id = %s) DESC, abs(posted_on - %s::date) LIMIT 1""",
                                (account_id, amount, day - timedelta(days=3), day + timedelta(days=3), list(claimed),
                                 t["id"], day)).fetchone()
        if imported:
            claimed.add(imported["transaction_id"])
            skip("transactions (already imported from a statement or export)")
            continue
        inserted = conn.execute(
            """INSERT INTO finance.transactions (transaction_id, account_id, account_text, posted_on, description_raw, merchant,
                                                 amount, kind, category, movement_type, classification, fingerprint, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (t["id"], account_id, t.get("account") or "", day, t.get("merchant") or "", t.get("merchant") or "", amount,
             "movement" if movement else "expense", None if movement else (t.get("category") or "Review"),
             (t.get("classification") or "Unclassified") if movement else None,
             None if movement else t.get("classification"), fingerprint, SOURCE)).rowcount
        if not inserted:
            skip("transactions (already imported)")
            continue
        counts["transactions"] += 1
        if t.get("source"):
            document, page = _document(conn, t["source"], account_id)
            if document:
                conn.execute("""INSERT INTO finance.transaction_sources (transaction_id, document_id, source_row)
                                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", (t["id"], document, page))

    # Receipts, their lines, and the bank charge each one explains
    # (same amount within a cent, within 3 days, both Kroger — the tracker's rule).
    bank = conn.execute("""SELECT transaction_id, posted_on, amount, merchant FROM finance.transactions
                           WHERE kind = 'expense' AND source = %s""", (SOURCE,)).fetchall()
    used = set()
    catalog = state.get("catalog") or {}
    for r in state.get("receipts") or []:
        day = parse_date(r.get("date"))
        if not r.get("id") or not in_calendar(day) or number(r.get("total")) is None:
            skip("receipts")
            continue
        merchant, _, location = str(r.get("store") or "").partition(" - ")
        net, tax, total = number(r.get("net")) or 0, number(r.get("tax")) or 0, number(r.get("total"))
        if abs(net + tax - total) > 0.01:
            skip("receipts (totals don't add up)")
            continue
        if conn.execute("""SELECT 1 FROM finance.receipts WHERE source = 'import'
                           AND (receipt_id = %s OR (purchased_on = %s AND total = %s))""", (r["id"], day, total)).fetchone():
            skip("receipts (already imported)")
            continue
        conn.execute("""INSERT INTO finance.receipts (receipt_id, purchased_on, merchant, store_location, category, net, tax,
                                                      total, savings, payment_text, source)
                        VALUES (%s, %s, %s, %s, 'Groceries', %s, %s, %s, %s, %s, %s)""",
                     (r["id"], day, merchant.strip(), location.strip(), net, tax, total, number(r.get("savings")) or 0,
                      r.get("payment") or "", SOURCE))
        match = next((b["transaction_id"] for b in bank if b["transaction_id"] not in used
                      and abs(float(b["amount"]) - total) < 0.011 and abs((b["posted_on"] - day).days) <= 3
                      and re.search("kroger", b["merchant"], re.I) and re.search("kroger", merchant, re.I)), None)
        if match:
            used.add(match)
        conn.execute("""INSERT INTO finance.receipt_payments (receipt_id, tender_text, account_id, amount, transaction_id,
                                                              match_method, match_score, matched_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                     (r["id"], r.get("payment") or "", account_for(conn, r.get("payment")), total, match,
                      "auto" if match else None, 1 if match else None, datetime.now(timezone.utc) if match else None))
        counts["receipts"] += 1
    receipts = {r["receipt_id"] for r in conn.execute("SELECT receipt_id FROM finance.receipts WHERE source = %s", (SOURCE,))}
    for category in sorted(set(catalog.values()) | {i.get("category") for i in state.get("items") or [] if i.get("category")}):
        conn.execute("INSERT INTO finance.item_categories VALUES (%s, 'Groceries') ON CONFLICT DO NOTHING", (category,))
    for item, category in catalog.items():
        conn.execute("INSERT INTO finance.item_catalog (item_name, item_category) VALUES (%s, %s) ON CONFLICT DO NOTHING", (item, category))
    for i in state.get("items") or []:
        if i.get("receiptId") not in receipts or not i.get("line") or not number(i.get("qty")):
            skip("receipt lines")
            continue
        conn.execute("""INSERT INTO finance.receipt_items (receipt_id, line, item_name, quantity, unit, amount, discount,
                                                           item_category, planned)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                     (i["receiptId"], i["line"], i.get("item") or "", number(i.get("qty")), i.get("unit") or "ea",
                      number(i.get("amount")) or 0, number(i.get("discount")) or 0, i.get("category") or "Review",
                      i.get("planned") if i.get("planned") in ("Planned", "Unplanned") else "Unknown"))
        counts["receipt lines"] += 1

    # Plan: budgets, bills, pay, allocations, goals.
    grocery = state.get("groceryBudget") or {}
    for b in budgets:
        members = _pool_members(b.get("note")) or [b.get("category")]
        name = b.get("category") or "Budget"
        if name == "Groceries" and number(grocery.get("perCheck")) and number(grocery.get("cycles")):
            conn.execute("""INSERT INTO finance.budgets (budget_id, name, basis, per_paycheck, paychecks_per_year, note)
                            VALUES (%s, %s, 'per_paycheck', %s, %s, %s)""",
                         (b.get("id") or slug(name), name, number(grocery["perCheck"]), int(number(grocery["cycles"])), b.get("note") or ""))
        else:
            amount = number(b.get("amount"))
            conn.execute("""INSERT INTO finance.budgets (budget_id, name, basis, amount_monthly, note) VALUES (%s, %s, %s, %s, %s)""",
                         (b.get("id") or slug(name), name, "fixed" if amount is not None else "unset", amount, b.get("note") or ""))
        for category in members:
            _category(conn, category)
            conn.execute("INSERT INTO finance.budget_categories VALUES (%s, %s) ON CONFLICT DO NOTHING", (b.get("id") or slug(name), category))
    expenses = state.get("expenses") or []
    for budget_id, category in (("b-sub", "Subscriptions"), ("b-ins", "Insurance")):
        if any(e.get("category") == category for e in expenses) and not conn.execute(
                "SELECT 1 FROM finance.budget_categories WHERE category = %s", (category,)).fetchone():
            conn.execute("INSERT INTO finance.budgets (budget_id, name, basis, note) VALUES (%s, %s, 'recurring_expenses', %s)",
                         (budget_id, category, "Sum of current recurring bills in this category."))
            conn.execute("INSERT INTO finance.budget_categories VALUES (%s, %s)", (budget_id, category))
    for e in expenses:
        if e.get("frequency") not in FREQUENCIES or not e.get("id"):
            skip("recurring bills")
            continue
        _category(conn, e.get("category") or "Subscriptions")
        day = number(e.get("day"))
        conn.execute("""INSERT INTO finance.recurring_expenses (expense_id, name, amount, frequency, due_day, status, category, note)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                     (e["id"], e.get("name") or "", number(e.get("amount")), e["frequency"],
                      int(day) if day and 1 <= day <= 31 else None,
                      e.get("status") if e.get("status") in ("Active", "Unconfirmed", "Canceled", "Expiring") else "Unconfirmed",
                      e.get("category") or "Subscriptions", e.get("note") or ""))
    payroll = state.get("payroll") or {}
    pay_day = parse_date(payroll.get("date"))
    if in_calendar(pay_day) and number(payroll.get("net")) is not None and parse_date(payroll.get("anchor")):
        conn.execute("INSERT INTO finance.pay_profile VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                     (pay_day, number(payroll.get("net")), number(payroll.get("gross")) or 0, int(number(payroll.get("frequency")) or 26),
                      parse_date(payroll["anchor"]), number(payroll.get("grossYtd")), number(payroll.get("netYtd")), number(payroll.get("bonusYtd"))))
    for position, d in enumerate(state.get("deposits") or []):
        conn.execute("INSERT INTO finance.paycheck_deposits VALUES (%s, %s, %s, %s)",
                     (position, d.get("name") or "", account_for(conn, d.get("name")), number(d.get("amount")) or 0))
    for i, income in enumerate(state.get("incomes") or []):
        if number(income.get("amount")) is None:
            skip("other incomes")
            continue
        conn.execute("INSERT INTO finance.other_incomes VALUES (%s, %s, %s, %s, %s)",
                     (income.get("id") or f"income-{i}", income.get("name") or "Income", number(income["amount"]),
                      income.get("frequency") if income.get("frequency") in FREQUENCIES else "Monthly", income.get("note") or ""))
    for a in state.get("allocations") or []:
        conn.execute("INSERT INTO finance.allocations (allocation_id, name, amount, cadence, note) VALUES (%s, %s, %s, %s, %s)",
                     (a.get("id") or slug(a.get("name")), a.get("name") or "", number(a.get("amount")) or 0,
                      a.get("cadence") if a.get("cadence") in FREQUENCIES else "Biweekly", a.get("note") or ""))
    allocations = {r["name"]: r["allocation_id"] for r in conn.execute("SELECT name, allocation_id FROM finance.allocations")}
    for g in state.get("goals") or []:
        goal_id = g.get("id") or slug(g.get("name"))
        conn.execute("""INSERT INTO finance.savings_goals (goal_id, name, target, contribution_per_check, due_on, allocation_id, note)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                     (goal_id, g.get("name") or "", number(g.get("target")), number(g.get("contribution")) or 0,
                      parse_date(g.get("due")), allocations.get(g.get("allocation")), g.get("note") or ""))
        day = parse_date(g.get("date"))
        if in_calendar(day):
            conn.execute("""INSERT INTO finance.balance_snapshots (as_of, goal_id, balance, balance_kind, source)
                            VALUES (%s, %s, %s, 'reported', %s)""", (day, goal_id, number(g.get("balance")), SOURCE))
    for r in state.get("retirement") or []:
        day = parse_date(r.get("date"))
        if not in_calendar(day) or not r.get("name"):
            skip("retirement balances")
            continue
        conn.execute("""INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, source, source_ref)
                        VALUES (%s, %s, %s, 'reported', %s, %s)""",
                     (day, account_for(conn, r["name"], "retirement"), number(r.get("balance")), SOURCE, r.get("id")))
        counts["retirement balances"] += 1
    plan = state.get("retirementPlan") or {}
    plan_day = parse_date(plan.get("date"))
    if in_calendar(plan_day):
        conn.execute("INSERT INTO finance.retirement_plan VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     (plan_day, number(plan.get("employee")), number(plan.get("employer")), number(plan.get("employeeYtd")),
                      number(plan.get("employerYtd")), number(plan.get("limit")), number(plan.get("otherDeferrals")),
                      int(number(plan.get("remainingChecks"))) if number(plan.get("remainingChecks")) is not None else None,
                      number(plan.get("iraPerCheck")), number(plan.get("iraLimit")), number(plan.get("iraActual"))))
    for s in state.get("shopping") or []:
        conn.execute("INSERT INTO finance.shopping_list VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (s.get("id") or slug(s.get("name")), s.get("name") or "", s.get("quantity") or "", number(s.get("cap")),
                      s.get("note") or "", bool(s.get("buy", True)), bool(s.get("done"))))

    report.update(counts)
    report["receipts matched to bank"] = len(used)
    report["skipped"] = dict(report["skipped"])
    return report


# ── Workspace API (v1): Money screens ────────────────────────────────────────
# Read-only until the Money import and editing screens replace the Finance
# tracker (which still owns these rows and re-projects them on every save).

def _month(value):
    """'2026-08' → first day of that month, or None."""
    day = parse_date(f"{value}-01") if value and len(str(value)) == 7 else None
    return day if in_calendar(day) else None


def _rows(conn, sql, *args):
    return [plain(r) for r in conn.execute(sql, args)]


def balances(conn):
    """Every active account's latest known balance and what has posted since (finance.current_balances)."""
    return _rows(conn, """SELECT * FROM finance.current_balances
                          WHERE is_active AND (balance IS NOT NULL OR transactions_since > 0)
                          ORDER BY is_liability, account_type, name""")


def spend_summary(conn, day):
    """Spending on a day, its week (Monday start) and month, and the last 14 days."""
    q = lambda sql, *a: float(conn.execute(sql, a).fetchone()["s"] or 0)
    week_start = day - timedelta(days=day.isoweekday() - 1)
    month_start = day.replace(day=1)
    budget = conn.execute("SELECT COALESCE(SUM(monthly_amount), 0) AS s FROM finance.effective_budgets").fetchone()["s"]
    return {
        "day": q("SELECT SUM(amount) AS s FROM finance.spending WHERE day = %s", day),
        "week": q("SELECT SUM(amount) AS s FROM finance.spending WHERE day BETWEEN %s AND %s", week_start, day),
        "month": q("SELECT SUM(amount) AS s FROM finance.spending WHERE day BETWEEN %s AND %s", month_start, day),
        "month_budget": float(budget or 0),
        "days": _rows(conn, """SELECT c.day, COALESCE(d.amount, 0) AS amount, COALESCE(d.transactions, 0) AS transactions
                               FROM core.calendar c LEFT JOIN finance.daily_spending d USING (day)
                               WHERE c.day BETWEEN %s AND %s ORDER BY c.day""", day - timedelta(days=13), day),
    }


@bp.route("/api/v1/money/balances", methods=["GET", "POST"])
@admin_required
def balances_route():
    """GET: accounts with balances. POST {account_id, balance, as_of?}: record a balance read off a portal or app."""
    with db.tx() as conn:
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            balance, day = number(data.get("balance")), parse_date(data.get("as_of")) or datetime.now().date()
            account = conn.execute("SELECT account_id FROM finance.accounts WHERE account_id = %s", (data.get("account_id"),)).fetchone()
            if not account or balance is None or not in_calendar(day) or abs(balance) > 1e9:
                return jsonify({"error": "An account, a balance and a date (YYYY-MM-DD) are required"}), 400
            conn.execute("""INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, source)
                            VALUES (%s, %s, %s, 'reported', 'manual')""", (day, account["account_id"], balance))
        return jsonify({"balances": balances(conn),
                        "accounts": _rows(conn, """SELECT account_id, name, institution, mask, account_type FROM finance.accounts
                                                   WHERE is_active ORDER BY name""")})


@bp.route("/api/v1/money/overview")
@admin_required
def money_overview():
    with db.tx() as conn:
        through = conn.execute("SELECT MAX(posted_on) AS d FROM finance.transactions").fetchone()["d"]
        month = conn.execute("""SELECT COALESCE(MAX(month_start), date_trunc('month', current_date)::date) AS m
                                FROM finance.budget_vs_actual WHERE actual IS NOT NULL AND actual <> 0""").fetchone()["m"]
        mode = conn.execute("SELECT value FROM core.settings WHERE key = 'money.overview_mode'").fetchone()
        today = conn.execute("SELECT current_date AS d").fetchone()["d"]
        return jsonify({
            "mode": mode["value"] if mode else "full",
            "through": through.isoformat() if through else None,
            "month": month.isoformat(),
            "balances": balances(conn),
            "spend": spend_summary(conn, min(today, through) if through else today),
            "budgets": _rows(conn, "SELECT * FROM finance.budget_vs_actual WHERE month_start = %s ORDER BY name", month),
            "goals": _rows(conn, "SELECT * FROM finance.goal_progress ORDER BY name"),
            "retirement": plain(conn.execute("SELECT * FROM finance.retirement_summary").fetchone() or {}),
            "cashFlow": _rows(conn, "SELECT * FROM finance.pay_period_cash_flow ORDER BY period_start DESC LIMIT 6"),
            "spendingByCategory": _rows(conn, """SELECT category, category_group, amount, transactions FROM finance.monthly_spending
                                                 WHERE month_start = %s ORDER BY amount DESC""", month),
            "monthlySpending": _rows(conn, """SELECT month_start, SUM(amount) AS amount FROM finance.monthly_spending
                                              GROUP BY 1 ORDER BY 1 DESC LIMIT 12"""),
            "topMerchants": _rows(conn, """SELECT merchant, COUNT(*) AS transactions, SUM(amount) AS amount FROM finance.spending
                                           WHERE day > current_date - 90 GROUP BY 1 ORDER BY 3 DESC LIMIT 8"""),
            "pay": _rows(conn, "SELECT * FROM finance.monthly_pay ORDER BY month_start DESC LIMIT 12"),
            "lastPaycheck": plain(conn.execute("""SELECT p.*, (SELECT jsonb_agg(jsonb_build_object('account_mask', s.account_mask,
                                                          'amount', s.amount) ORDER BY s.position) FROM finance.paycheck_splits s
                                                          WHERE s.paycheck_id = p.paycheck_id) AS splits
                                                   FROM finance.paychecks p ORDER BY pay_date DESC LIMIT 1""").fetchone() or {}),
            "groceries": _rows(conn, """SELECT item_category, SUM(amount) AS amount, SUM(lines) AS lines FROM finance.item_spending
                                        WHERE month_start > current_date - 180 GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""),
        })


@bp.route("/api/v1/money/months")
@admin_required
def money_months():
    with db.tx() as conn:
        return jsonify([r["m"].strftime("%Y-%m") for r in conn.execute(
            """SELECT DISTINCT date_trunc('month', d)::date AS m FROM (
                   SELECT posted_on AS d FROM finance.transactions UNION SELECT purchased_on FROM finance.receipts) x
               WHERE d IS NOT NULL ORDER BY m DESC""")])


@bp.route("/api/v1/money/transactions")
@admin_required
def money_transactions():
    month = _month(request.args.get("month"))
    where, args = [], []
    if month:
        where.append("t.posted_on >= %s AND t.posted_on < (%s::date + INTERVAL '1 month')"); args += [month, month]
    for key, column in (("account", "t.account_id"), ("category", "t.category"), ("kind", "t.kind")):
        if request.args.get(key):
            where.append(f"{column} = %s"); args.append(request.args[key])
    if request.args.get("q"):
        where.append("(t.merchant ILIKE %s OR t.description_raw ILIKE %s)"); args += ["%" + request.args["q"] + "%"] * 2
    with db.tx() as conn:
        rows = _rows(conn, f"""
            SELECT t.transaction_id, t.posted_on, t.merchant, t.description_raw, t.amount, t.kind, t.category,
                   t.movement_type, t.is_pending, t.account_id, a.name AS account_name, a.institution,
                   COALESCE((SELECT jsonb_agg(jsonb_build_object('receipt_id', p.receipt_id, 'merchant', r.merchant,
                                                                'total', r.total, 'purchased_on', r.purchased_on))
                             FROM finance.receipt_payments p JOIN finance.receipts r USING (receipt_id)
                             WHERE p.transaction_id = t.transaction_id), '[]') AS receipts,
                   COALESCE((SELECT jsonb_agg(jsonb_build_object('line', l.line, 'item_name', l.item_name,
                                                                'item_category', l.item_category, 'amount', l.allocated_amount)
                                              ORDER BY l.receipt_id, l.line)
                             FROM finance.transaction_line_items l WHERE l.transaction_id = t.transaction_id), '[]') AS lines,
                   COALESCE((SELECT jsonb_agg(jsonb_build_object('file', d.original_filename, 'row', s.source_row,
                                                                'reported_balance', s.reported_balance))
                             FROM finance.transaction_sources s JOIN finance.source_documents d USING (document_id)
                             WHERE s.transaction_id = t.transaction_id), '[]') AS sources
            FROM finance.transactions t LEFT JOIN finance.accounts a USING (account_id)
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY t.posted_on DESC, t.transaction_id LIMIT 1000""", *args)
        return jsonify({
            "transactions": rows,
            "accounts": _rows(conn, "SELECT account_id, name, institution FROM finance.accounts WHERE is_active ORDER BY name"),
            "categories": [r["category"] for r in conn.execute("SELECT category FROM finance.categories ORDER BY category")],
        })


@bp.route("/api/v1/money/receipts")
@admin_required
def money_receipts():
    month = _month(request.args.get("month"))
    with db.tx() as conn:
        return jsonify(_rows(conn, """
            SELECT r.receipt_id, r.purchased_on, r.merchant, r.store_location, r.category, r.net, r.tax, r.total, r.savings,
                   r.payment_text, m.match_status, m.matched_amount, m.transaction_ids,
                   COALESCE((SELECT jsonb_agg(jsonb_build_object('line', i.line, 'item_name', i.item_name, 'quantity', i.quantity,
                                                                'unit', i.unit, 'amount', i.amount, 'discount', i.discount,
                                                                'item_category', i.item_category, 'planned', i.planned)
                                              ORDER BY i.line)
                             FROM finance.receipt_items i WHERE i.receipt_id = r.receipt_id), '[]') AS items
            FROM finance.receipts r LEFT JOIN finance.receipt_matching m USING (receipt_id)
            WHERE %s::date IS NULL OR (r.purchased_on >= %s AND r.purchased_on < (%s::date + INTERVAL '1 month'))
            ORDER BY r.purchased_on DESC, r.receipt_id""", month, month, month))


@bp.route("/api/v1/money/accounts")
@admin_required
def money_accounts():
    with db.tx() as conn:
        accounts = _rows(conn, """SELECT a.*, t.is_liability, l.balance, l.as_of, l.balance_kind
                                  FROM finance.accounts a JOIN finance.account_types t USING (account_type)
                                  LEFT JOIN finance.latest_balances l USING (account_id)
                                  ORDER BY a.is_active DESC, a.account_type, a.name""")
        history = {}
        for r in conn.execute("""SELECT account_id, as_of, balance, balance_kind, source FROM finance.balance_timeline
                                 ORDER BY account_id, as_of"""):
            history.setdefault(r["account_id"], []).append(plain(r))
        for a in accounts:
            a["history"] = history.get(a["account_id"], [])[-36:]
        return jsonify({"accounts": accounts,
                        "statements": _rows(conn, "SELECT * FROM finance.statement_reconciliation ORDER BY period_end DESC")})


@bp.route("/api/v1/money/budgets")
@admin_required
def money_budgets():
    month = _month(request.args.get("month"))
    with db.tx() as conn:
        if not month:
            month = conn.execute("""SELECT COALESCE(MAX(month_start), date_trunc('month', current_date)::date) AS m
                                    FROM finance.budget_vs_actual WHERE actual <> 0""").fetchone()["m"]
        return jsonify({
            "month": month.isoformat(),
            "budgets": _rows(conn, """SELECT e.*, v.actual, v.remaining, v.over_budget FROM finance.effective_budgets e
                                      LEFT JOIN finance.budget_vs_actual v ON v.budget_id = e.budget_id AND v.month_start = %s
                                      ORDER BY e.name""", month),
            "bills": _rows(conn, "SELECT * FROM finance.recurring_monthly ORDER BY due_day NULLS LAST, name"),
            "spending": _rows(conn, "SELECT * FROM finance.monthly_spending WHERE month_start = %s ORDER BY amount DESC", month),
        })


@bp.route("/api/v1/money/goals")
@admin_required
def money_goals():
    with db.tx() as conn:
        return jsonify({
            "goals": _rows(conn, "SELECT * FROM finance.goal_progress ORDER BY name"),
            "retirement": plain(conn.execute("SELECT * FROM finance.retirement_summary").fetchone() or {}),
            "retirementAccounts": _rows(conn, """SELECT l.* FROM finance.latest_balances l JOIN finance.accounts a USING (account_id)
                                                 WHERE a.retirement_type IS NOT NULL ORDER BY l.name"""),
            "pay": _rows(conn, "SELECT * FROM finance.pay_profile"),
            "deposits": _rows(conn, "SELECT * FROM finance.paycheck_deposits"),
            "allocations": _rows(conn, "SELECT * FROM finance.allocations"),
        })
