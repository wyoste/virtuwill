"""Payroll Earning Statement reports: one or more pay statements per PDF.

Each statement page reads like:

    Pay Date: 1/16/2026 · Pay Period: 1/3/2026 - 1/16/2026 · Deposit Advice #: 100000123
    Employer Name: <employer> …
    Earnings 80.00 $4,000.00 80.00 $4,000.00                section total: current, then year to date
    Regular 72.00 50.0000 $3,600.00 72.00 $3,600.00         line: hours, rate, current, YTD hours, YTD
    Vacation Pay 16.00 $800.00                              year-to-date only (nothing this period)
    …
    Net Pay $2,900.00 $2,900.00
    Direct Deposit <routing> XXXXX1234 $1,000.00

Kept: dates, amounts, line items, and each deposit's account last four.
Dropped: address, employee number, routing numbers.
"""
import re
from datetime import datetime

from . import document_info, empty_bundle

SECTIONS = {"Earnings": "earnings", "Taxable Benefits": "taxable_benefits", "Memo Information": "memo",
            "Pre-Tax Deductions": "pre_tax", "Post-Tax Deductions": "post_tax", "Taxes": "taxes",
            "Net Pay": "net_pay"}
PAY_DATE = re.compile(r"Pay Date: (\d{1,2}/\d{1,2}/\d{4})")
PERIOD = re.compile(r"Pay Period: (\d{1,2}/\d{1,2}/\d{4}) - (\d{1,2}/\d{1,2}/\d{4})")
ADVICE = re.compile(r"Deposit Advice #: (\d+)")
EMPLOYER = re.compile(r"Employer Name: (.+?)(?: Federal| State|$)", re.M)
DEPOSIT = re.compile(r"^Direct Deposit \d+ X+(\d{4}) \$([\d,]+\.\d{2})$")
NUMBER = re.compile(r"\$?[\d,]+(?:\.\d+)?")


def _day(text):
    return datetime.strptime(text, "%m/%d/%Y").date().isoformat()


def _num(text):
    return round(float(text.replace("$", "").replace(",", "")), 4)


def _line(line):
    """'Regular 72.00 67.1755 $4,836.64 1,374.00 $92,262.81' → name and numbers.

    Dollar amounts are the money columns: two means current and year to date;
    one means the line has only a year-to-date amount this period.
    """
    tokens = line.split()
    numbers = []
    while tokens and NUMBER.fullmatch(tokens[-1]):
        numbers.insert(0, tokens.pop())
    name = " ".join(tokens)
    if not name or not numbers:
        return None
    dollars = [_num(n) for n in numbers if n.startswith("$")]
    plain = [_num(n) for n in numbers if not n.startswith("$")]
    if not dollars:
        return None
    current, ytd = (dollars[0], dollars[1]) if len(dollars) >= 2 else (None, dollars[0])
    hours = rate = ytd_hours = None
    if current is not None:
        hours, rate, ytd_hours = (plain + [None, None, None])[:3] if len(plain) == 3 else \
                                 ((plain[0], None, plain[1]) if len(plain) == 2 else (None, None, plain[0] if plain else None))
    else:
        ytd_hours = plain[0] if plain else None
    return {"name": name, "hours": hours, "rate": rate, "current": current, "ytd_hours": ytd_hours, "ytd": ytd}


def _statement(page):
    lines = [l.strip() for l in page.splitlines() if l.strip()]
    pay_date, period, advice = PAY_DATE.search(page), PERIOD.search(page), ADVICE.search(page)
    employer = EMPLOYER.search(page)
    check = {"advice_number": advice[1] if advice else None, "pay_date": _day(pay_date[1]),
             "period_start": _day(period[1]), "period_end": _day(period[2]),
             "employer": (employer[1].strip() if employer else ""), "lines": [], "deposits": []}
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Hours/Units Rate Amount")) + 1
    except StopIteration:
        return None
    section = None
    totals = {}
    for line in lines[start:]:
        if line.startswith("Routing #"):
            continue
        d = DEPOSIT.match(line)
        if d:
            check["deposits"].append({"account_mask": d[1], "amount": _num(d[2])})
            continue
        if line.startswith("Accruals & Balances"):
            break
        parsed = _line(line)
        if not parsed:
            continue
        if parsed["name"] in SECTIONS:
            section = SECTIONS[parsed["name"]]
            totals[section] = parsed
            continue
        if section and section != "net_pay":
            check["lines"].append({"section": section, **parsed})
    get = lambda s: (totals.get(s) or {}).get("current") or 0
    check.update(gross=get("earnings"), pre_tax=get("pre_tax"), post_tax=get("post_tax"), taxes=get("taxes"),
                 net=get("net_pay"), gross_ytd=(totals.get("earnings") or {}).get("ytd"),
                 net_ytd=(totals.get("net_pay") or {}).get("ytd"))
    # Net pay should equal gross − pre-tax − taxes − post-tax, and the deposits should add up to it.
    expected = round(check["gross"] - check["pre_tax"] - check["taxes"] - check["post_tax"], 2)
    check["checks"] = {"net": {"reported": check["net"], "parsed": expected, "ok": abs(expected - check["net"]) < 0.02},
                       "deposits": {"reported": check["net"], "parsed": round(sum(x["amount"] for x in check["deposits"]), 2),
                                    "ok": abs(sum(x["amount"] for x in check["deposits"]) - check["net"]) < 0.02}}
    return check


def earning_statements(path, content, pages):
    bundle = empty_bundle(document_info(path, content, "pay_stub", "pdf", "", "payroll_earning_statement"))
    seen = set()
    for page in pages:
        if "Net Pay" not in page or "Hours/Units Rate Amount" not in page:
            continue                                 # supplemental pages repeat details only
        check = _statement(page)
        if check and (check["advice_number"], check["pay_date"]) not in seen:
            seen.add((check["advice_number"], check["pay_date"]))
            bundle["paychecks"].append(check)
    bundle["paychecks"].sort(key=lambda c: c["pay_date"])
    if bundle["paychecks"]:
        first, last = bundle["paychecks"][0], bundle["paychecks"][-1]
        bundle["document"].update(institution=last["employer"], period_start=first["period_start"],
                                  period_end=last["period_end"], document_date=last["pay_date"])
    masks = sorted({d["account_mask"] for c in bundle["paychecks"] for d in c["deposits"]})
    bundle["accounts"] = [{"mask": m, "institution": "", "name": f"Account {m}", "account_type": None} for m in masks]
    bundle["document"]["checks"] = {
        c["pay_date"]: {"ok": c["checks"]["net"]["ok"] and c["checks"]["deposits"]["ok"]} for c in bundle["paychecks"]}
    return bundle


earning_statements.matches = lambda text: bool(PAY_DATE.search(text) and ADVICE.search(text) and "Net Pay" in text)
