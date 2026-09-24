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
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from . import db
from .auth import admin_required
from .util import in_calendar, number, parse_date, slug

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

    Names that end in the same four digits are one account ('Chase 7237' and
    'VISA 7237 $76.57'); every spelling is kept as an alias.
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
