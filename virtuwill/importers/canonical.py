"""The finance bundle as files: one JSON document, or one CSV per record type.

CSV files (what `to_csv` writes and `from_csv_text` reads back):

    accounts.csv           mask, institution, name, account_type
    statements.csv         account_mask, period_start, period_end, opening_balance, closing_balance, …
    balances.csv           account_mask, as_of, balance, kind
    transactions.csv       account_mask, transacted_on, posted_on, description, amount, kind, category, source_row,
                           external_id, pending
    receipts.csv           receipt_id, purchased_on, merchant, store_location, …, account_mask
    receipt_items.csv      receipt_id, line, item_name, item_category, quantity, unit, unit_price, …, amount
    paychecks.csv          advice_number, pay_date, period_start, period_end, employer, gross, …, net
    paycheck_lines.csv     advice_number, section, name, hours, rate, current, ytd_hours, ytd
    paycheck_deposits.csv  advice_number, account_mask, amount

Amounts use the site's sign: positive is money leaving the account.
"""
import csv
import io
from datetime import date
from pathlib import Path

from . import FORMAT, SECTIONS, ExtractError, document_info, empty_bundle

COLUMNS = {
    "accounts": ["mask", "institution", "name", "account_type"],
    "statements": ["account_mask", "period_start", "period_end", "opening_balance", "closing_balance", "total_credits",
                   "total_debits", "fees_charged", "interest_charged", "minimum_due", "payment_due_on", "credit_limit"],
    "balances": ["account_mask", "as_of", "balance", "kind"],
    "transactions": ["account_mask", "transacted_on", "posted_on", "description", "amount", "kind", "category", "source_row",
                     "external_id", "pending"],
    "receipts": ["receipt_id", "purchased_on", "merchant", "store_location", "item_count", "regular_total", "savings",
                 "net", "tax", "total", "payment_text", "account_mask"],
    "receipt_items": ["receipt_id", "line", "item_name", "item_category", "store_brand", "quantity", "unit", "unit_price",
                      "regular_price", "discount", "amount", "weighed"],
    "paychecks": ["advice_number", "pay_date", "period_start", "period_end", "employer", "gross", "pre_tax", "post_tax",
                  "taxes", "net", "gross_ytd", "net_ytd"],
    "paycheck_lines": ["advice_number", "section", "name", "hours", "rate", "current", "ytd_hours", "ytd"],
    "paycheck_deposits": ["advice_number", "account_mask", "amount"],
}
NUMERIC = {"opening_balance", "closing_balance", "total_credits", "total_debits", "fees_charged", "interest_charged",
           "minimum_due", "credit_limit", "balance", "amount", "regular_total", "savings", "net", "tax", "total",
           "quantity", "unit_price", "regular_price", "discount", "gross", "pre_tax", "post_tax", "taxes",
           "gross_ytd", "net_ytd", "hours", "rate", "current", "ytd_hours", "ytd"}
KINDS = {"expense", "card_payment", "transfer", "income", "refund"}
BALANCE_KINDS = {"statement_opening", "statement_closing", "running", "reported", "available"}


def _date(value, where):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ExtractError(f"{where}: '{value}' is not a date (YYYY-MM-DD)") from None


def _number(value, where, required=True):
    if value in (None, ""):
        if required:
            raise ExtractError(f"{where}: a number is required")
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "")), 4)
    except ValueError:
        raise ExtractError(f"{where}: '{value}' is not a number") from None


def _mask(value, where):
    text = str(value or "").strip()
    if not (len(text) == 4 and text.isdigit()):
        raise ExtractError(f"{where}: account_mask must be the account's last four digits")
    return text


def validate(bundle):
    """Check a bundle's shape and values; returns it with dates and numbers normalised."""
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ExtractError(f"Not a finance bundle (expected format '{FORMAT}')")
    doc = bundle.get("document") or {}
    if not doc.get("sha256") or not doc.get("filename"):
        raise ExtractError("The bundle's document needs a filename and sha256")
    for section in SECTIONS:
        if not isinstance(bundle.get(section, []), list):
            raise ExtractError(f"'{section}' must be a list")
        bundle.setdefault(section, [])
    for i, a in enumerate(bundle["accounts"], 1):
        a["mask"] = _mask(a.get("mask"), f"accounts[{i}]")
    for i, t in enumerate(bundle["transactions"], 1):
        where = f"transactions[{i}]"
        t["account_mask"] = _mask(t.get("account_mask"), where)
        t["posted_on"] = _date(t.get("posted_on"), where)
        t["transacted_on"] = _date(t["transacted_on"], where) if t.get("transacted_on") else None
        t["amount"] = _number(t.get("amount"), where)
        t["kind"] = t.get("kind") or "expense"
        if t["kind"] not in KINDS:
            raise ExtractError(f"{where}: kind must be one of {', '.join(sorted(KINDS))}")
        t["description"] = str(t.get("description") or "").strip()
        # The bank's own id for the transaction, when the source has one: loads match on it exactly.
        external = t.get("external_id")
        t["external_id"] = str(external).strip()[:120] if external not in (None, "") else None
        pending = t.get("pending")
        t["pending"] = pending is True or str(pending).lower() in ("true", "1", "yes")
    for i, s in enumerate(bundle["statements"], 1):
        where = f"statements[{i}]"
        s["account_mask"] = _mask(s.get("account_mask"), where)
        s["period_start"], s["period_end"] = _date(s.get("period_start"), where), _date(s.get("period_end"), where)
    for i, b in enumerate(bundle["balances"], 1):
        where = f"balances[{i}]"
        b["account_mask"] = _mask(b.get("account_mask"), where)
        b["as_of"] = _date(b.get("as_of"), where)
        b["balance"] = _number(b.get("balance"), where)
        b["kind"] = b.get("kind") or "reported"
        if b["kind"] not in BALANCE_KINDS:
            raise ExtractError(f"{where}: kind must be one of {', '.join(sorted(BALANCE_KINDS))}")
    for i, r in enumerate(bundle["receipts"], 1):
        where = f"receipts[{i}]"
        if not r.get("receipt_id"):
            raise ExtractError(f"{where}: receipt_id is required")
        r["purchased_on"] = _date(r.get("purchased_on"), where)
        r["total"] = _number(r.get("total"), where)
        r["account_mask"] = _mask(r["account_mask"], where) if r.get("account_mask") else None
        r.setdefault("items", [])
    for i, p in enumerate(bundle["paychecks"], 1):
        where = f"paychecks[{i}]"
        p["pay_date"] = _date(p.get("pay_date"), where)
        p["net"] = _number(p.get("net"), where)
        p["gross"] = _number(p.get("gross"), where)
        p.setdefault("lines", []), p.setdefault("deposits", [])
        for d in p["deposits"]:
            d["account_mask"] = _mask(d.get("account_mask"), where + " deposit")
            d["amount"] = _number(d.get("amount"), where + " deposit")
    return bundle


# ── CSV ──────────────────────────────────────────────────────────────────────

def tables(bundle):
    """The bundle as flat rows per CSV file."""
    out = {name: [] for name in COLUMNS}
    for section in ("accounts", "statements", "balances", "transactions"):
        out[section] = bundle.get(section) or []
    for r in bundle.get("receipts") or []:
        out["receipts"].append(r)
        out["receipt_items"] += [{"receipt_id": r["receipt_id"], **i} for i in r.get("items") or []]
    for p in bundle.get("paychecks") or []:
        out["paychecks"].append(p)
        out["paycheck_lines"] += [{"advice_number": p["advice_number"], **l} for l in p.get("lines") or []]
        out["paycheck_deposits"] += [{"advice_number": p["advice_number"], **d} for d in p.get("deposits") or []]
    return out


def to_csv(bundle, folder):
    """Write one CSV per non-empty record type into `folder`; returns the paths."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in tables(bundle).items():
        if not rows:
            continue
        path = folder / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS[name], extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLUMNS[name]})
        written.append(path)
    return written


def _kind_of(header):
    header = [h.strip() for h in header]
    for name, columns in COLUMNS.items():
        if header[:len(columns)] == columns[:len(header)] and set(columns[:3]) <= set(header):
            return name
    return None


def from_csv_text(path, content, text):
    """A canonical CSV (e.g. transactions.csv) as a bundle, or None if it isn't one."""
    reader = csv.DictReader(io.StringIO(text))
    kind = _kind_of(reader.fieldnames or [])
    if not kind or kind in ("receipt_items", "paycheck_lines", "paycheck_deposits"):
        return None
    rows = [{k: (None if v == "" else v) for k, v in r.items()} for r in reader]
    for r in rows:
        for k in list(r):
            if k in NUMERIC and r[k] is not None:
                r[k] = float(r[k])
    bundle = empty_bundle(document_info(path, content, "transaction_export" if kind == "transactions" else "other",
                                        "csv", "", "canonical_csv"))
    bundle[kind] = rows
    if kind == "transactions":
        bundle["accounts"] = [{"mask": m, "institution": "", "name": f"Account {m}", "account_type": None}
                              for m in sorted({r["account_mask"] for r in rows if r.get("account_mask")})]
    return validate(bundle)
