"""Chase exports: the Spending Summary report and the monthly card statement."""
import re
from datetime import date, datetime

from . import document_info, empty_bundle

# Chase's spending categories → the categories this site uses.
CATEGORIES = {
    "FOOD_AND_DRINK": "Dining", "GROCERIES": "Groceries", "SHOPPING": "Shopping", "TRAVEL": "Travel",
    "GAS": "Fuel", "BILLS_AND_UTILITIES": "Bills", "ENTERTAINMENT": "Entertainment",
    "HEALTH_AND_WELLNESS": "Health", "PERSONAL": "Personal", "FEES_AND_ADJUSTMENTS": "Fees",
    "HOME": "Home", "EDUCATION": "Education", "PROFESSIONAL_SERVICES": "Professional",
    "AUTOMOTIVE": "Fuel", "GIFTS_AND_DONATIONS": "Personal",
}

MONEY = r"-?\$?-?[\d,]+\.\d{2}"


def money(text):
    """'$1,234.56', '-$5.00', '$-116.46', '1,200.00' → float."""
    t = text.replace("$", "").replace(",", "").strip()
    negative = t.startswith("-") or text.strip().startswith("-")
    return round(-abs(float(t.lstrip("-"))) if negative else float(t), 2)


def card(mask, name="Chase card"):
    return {"mask": mask, "institution": "Chase", "name": f"{name} {mask}", "account_type": "credit_card"}


def is_card_payment(description):
    return bool(re.search(r"\b(payment thank you|autopay|payment - thank|online payment)\b", description, re.I))


# ── Spending Summary report ───────────────────────────────────────────────────
# "Jan 01, 2026 to Jan 31, 2026 Spending Report 1234", then per category:
# "Transaction Date Posted Date Description Amount" rows like
# "Jan 02, 2026 Jan 04, 2026 Corner Cafe $12.50".
REPORT_HEAD = re.compile(r"(\w{3} \d{2}, \d{4}) to (\w{3} \d{2}, \d{4}) Spending Report (\d{4})")
REPORT_ROW = re.compile(r"^(\w{3} \d{2}, \d{4}) (\w{3} \d{2}, \d{4}) (.+?) (-?\$-?[\d,]+\.\d{2})$")


def _mdy(text):
    return datetime.strptime(text, "%b %d, %Y").date()


def spending_report(path, content, pages):
    text = "\n".join(pages)
    head = REPORT_HEAD.search(text)
    start, end, mask = _mdy(head[1]), _mdy(head[2]), head[3]
    bundle = empty_bundle(document_info(path, content, "transaction_export", "pdf", "Chase", "chase_spending_report",
                                        account_mask=mask, period_start=start.isoformat(), period_end=end.isoformat(),
                                        document_date=end.isoformat()))
    bundle["accounts"].append(card(mask))
    category, reported_total = None, {}
    for page_no, page in enumerate(pages, 1):
        for line in page.splitlines():
            line = line.strip()
            if re.fullmatch(r"[A-Z][A-Z_]+", line) and line in CATEGORIES or re.fullmatch(r"[A-Z]+(_[A-Z]+)+", line):
                category = line
                continue
            if line.startswith("Total ") and category and re.fullmatch(r"Total " + MONEY, line):
                reported_total[category] = money(line[6:])
                continue
            m = REPORT_ROW.match(line)
            if m and category:
                amount = money(m[4])
                bundle["transactions"].append({
                    "account_mask": mask, "transacted_on": _mdy(m[1]).isoformat(), "posted_on": _mdy(m[2]).isoformat(),
                    "description": m[3].strip(), "amount": amount, "kind": "expense",
                    "category": CATEGORIES.get(category, "Review"), "source_category": category, "source_row": f"p. {page_no}"})
    # The report prints a total per category: a mismatch means a row was misread.
    checks = {}
    for cat, total in reported_total.items():
        parsed = round(sum(t["amount"] for t in bundle["transactions"] if t["source_category"] == cat), 2)
        checks[cat] = {"reported": total, "parsed": parsed, "ok": abs(parsed - total) < 0.005}
    bundle["document"]["checks"] = checks
    return bundle


spending_report.matches = lambda text: bool(REPORT_HEAD.search(text)) and "Transaction Date Posted Date" in text


# ── Monthly card statement ────────────────────────────────────────────────────
PERIOD = re.compile(r"Opening/Closing Date (\d{2}/\d{2}/\d{2}) - (\d{2}/\d{2}/\d{2})")
ACCOUNT = re.compile(r"Account Number: X{4} X{4} X{4} (\d{4})")
SUMMARY = {
    "opening_balance": r"Previous Balance (" + MONEY + ")",
    "total_credits": r"Payment, Credits (" + MONEY + ")",
    "purchases": r"Purchases \+?(" + MONEY + ")",
    "fees_charged": r"Fees Charged \+?(" + MONEY + ")",
    "interest_charged": r"Interest Charged \+?(" + MONEY + ")",
    "closing_balance": r"New Balance (" + MONEY + ")",
    "credit_limit": r"Credit Access Line (\$[\d,]+(?:\.\d{2})?)",
    "minimum_due": r"Minimum Payment Due: (" + MONEY + ")",
}
DUE = re.compile(r"Payment Due Date: (\d{2}/\d{2}/\d{2})")
ACTIVITY_ROW = re.compile(r"^(\d{2}/\d{2}) (.+?) (-?[\d,]+\.\d{2})$")


def _short(text):
    return datetime.strptime(text, "%m/%d/%y").date()


def card_statement(path, content, pages):
    text = "\n".join(pages)
    period = PERIOD.search(text)
    opening, closing = _short(period[1]), _short(period[2])
    mask = ACCOUNT.search(text)[1]
    values = {}
    for key, pattern in SUMMARY.items():
        m = re.search(pattern, text)
        values[key] = money(m[1]) if m else None
    due = DUE.search(text)
    bundle = empty_bundle(document_info(path, content, "statement", "pdf", "Chase", "chase_card_statement",
                                        account_mask=mask, period_start=opening.isoformat(), period_end=closing.isoformat(),
                                        document_date=closing.isoformat()))
    bundle["accounts"].append(card(mask))
    bundle["statements"].append({
        "account_mask": mask, "period_start": opening.isoformat(), "period_end": closing.isoformat(),
        "opening_balance": values["opening_balance"], "closing_balance": values["closing_balance"],
        # On a card, money in is payments and credits; money out is purchases, fees and interest.
        "total_credits": abs(values["total_credits"] or 0),
        "total_debits": round((values["purchases"] or 0) + (values["fees_charged"] or 0) + (values["interest_charged"] or 0), 2),
        "fees_charged": values["fees_charged"], "interest_charged": values["interest_charged"],
        "minimum_due": values["minimum_due"], "payment_due_on": _short(due[1]).isoformat() if due else None,
        "credit_limit": values["credit_limit"]})
    bundle["balances"] += [
        {"account_mask": mask, "as_of": opening.isoformat(), "balance": values["opening_balance"], "kind": "statement_opening"},
        {"account_mask": mask, "as_of": closing.isoformat(), "balance": values["closing_balance"], "kind": "statement_closing"}]

    section, active = None, False
    for page_no, page in enumerate(pages, 1):
        for line in page.splitlines():
            line = line.strip()
            squeezed = re.sub(r"(.)\1", r"\1", line)          # bold headings print each letter twice
            if "ACCOUNT ACTIVITY" in squeezed or "ACCOUNT ACTIVITY" in line:
                active = True
                continue
            if not active:
                continue
            if re.match(r"^\d{4} Totals Year-to-Date|^INTEREST CHARGES|SHOP WITH POINTS", squeezed):
                active = False
                continue
            if line in ("PAYMENTS AND OTHER CREDITS", "PURCHASE", "PURCHASES", "FEES CHARGED", "INTEREST CHARGED", "CASH ADVANCES"):
                section = line
                continue
            m = ACTIVITY_ROW.match(line)
            if not m:
                continue
            month, day_ = (int(x) for x in m[1].split("/"))
            # Statements span a year end: a month after the closing month belongs to the year before.
            year = closing.year - (1 if month > closing.month else 0)
            posted = date(year, month, day_)
            amount = money(m[3])
            description = m[2].strip()
            payment = section == "PAYMENTS AND OTHER CREDITS" and is_card_payment(description)
            bundle["transactions"].append({
                "account_mask": mask, "transacted_on": posted.isoformat(), "posted_on": posted.isoformat(),
                "description": description, "amount": amount,
                "kind": "card_payment" if payment else "expense",
                "category": None if payment else ("Fees" if section in ("FEES CHARGED", "INTEREST CHARGED") else None),
                "source_row": f"p. {page_no}"})
    # Check the parse against the statement's own totals.
    credits = round(-sum(t["amount"] for t in bundle["transactions"] if t["amount"] < 0), 2)
    debits = round(sum(t["amount"] for t in bundle["transactions"] if t["amount"] > 0), 2)
    stmt = bundle["statements"][0]
    bundle["document"]["checks"] = {
        "credits": {"reported": stmt["total_credits"], "parsed": credits, "ok": abs(credits - stmt["total_credits"]) < 0.005},
        "debits": {"reported": stmt["total_debits"], "parsed": debits, "ok": abs(debits - stmt["total_debits"]) < 0.005},
        "balance": {"reported": stmt["closing_balance"], "parsed": round((stmt["opening_balance"] or 0) + debits - credits, 2),
                    "ok": abs((stmt["opening_balance"] or 0) + debits - credits - (stmt["closing_balance"] or 0)) < 0.005},
    }
    return bundle


card_statement.matches = lambda text: bool(PERIOD.search(text) and ACCOUNT.search(text))
