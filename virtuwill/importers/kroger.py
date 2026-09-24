"""Grocery receipt exports: a receipts CSV (one row per receipt) and an items CSV
(one row per line), joined by the receipt's file id."""
import csv
import io
import re

from . import document_info, empty_bundle
from .chase import money

RECEIPT_COLUMNS = {"date", "store", "order_total", "payment", "drive_file_id"}
ITEM_COLUMNS = {"date", "store", "item", "line_total", "drive_file_id"}
TENDER = re.compile(r"(VISA|MASTERCARD|AMEX|DISCOVER|DEBIT|CREDIT)\s+(\d{4})", re.I)


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def _header(text):
    return set(next(csv.reader(io.StringIO(text)), []))


def _num(value):
    try:
        return money(value) if value is not None and str(value).strip() else None
    except ValueError:
        return None


def _store(text):
    merchant, _, location = (text or "").partition(" - ")
    return merchant.strip(), location.strip()


def receipts_csv(path, content, text):
    rows = _rows(text)
    bundle = empty_bundle(document_info(path, content, "receipt", "csv", "Kroger", "grocery_receipts_csv"))
    masks = {}
    for i, r in enumerate(rows, 2):
        merchant, location = _store(r.get("store"))
        tender = TENDER.search(r.get("payment") or "")
        if tender:
            masks[tender[2]] = tender[1].upper()
        bundle["receipts"].append({
            "receipt_id": "rcpt-" + (r.get("drive_file_id") or f"{r['date']}-{r.get('order_total')}"),
            "purchased_on": r["date"], "merchant": merchant, "store_location": location,
            "item_count": int(float(r["item_count"])) if r.get("item_count") else None,
            "regular_total": _num(r.get("regular_total")), "savings": _num(r.get("savings")),
            "net": _num(r.get("items_net")), "tax": _num(r.get("sales_tax")), "total": _num(r.get("order_total")),
            "payment_text": r.get("payment") or "", "account_mask": tender[2] if tender else None,
            "source_row": f"row {i}", "items": []})
    bundle["accounts"] = [{"mask": m, "institution": "", "name": f"{kind.title()} {m}",
                           "account_type": "checking" if kind == "DEBIT" else "credit_card"} for m, kind in sorted(masks.items())]
    if bundle["receipts"]:
        days = sorted(r["purchased_on"] for r in bundle["receipts"])
        bundle["document"].update(period_start=days[0], period_end=days[-1], document_date=days[-1])
    return bundle


receipts_csv.matches = lambda text: RECEIPT_COLUMNS <= _header(text)


def items_csv(path, content, text):
    rows = _rows(text)
    bundle = empty_bundle(document_info(path, content, "receipt", "csv", "Kroger", "grocery_items_csv"))
    bundle["receipt_items"] = []
    line_numbers = {}
    for r in rows:
        receipt_id = "rcpt-" + r["drive_file_id"]
        line_numbers[receipt_id] = line_numbers.get(receipt_id, 0) + 1
        bundle["receipt_items"].append({
            "receipt_id": receipt_id, "line": line_numbers[receipt_id], "purchased_on": r.get("date"),
            "item_name": r["item"], "item_category": r.get("category") or "Review",
            "store_brand": (r.get("store_brand") or "").upper() == "Y",
            "quantity": _num(r.get("qty")) or 1, "unit": r.get("unit") or "ea",
            "unit_price": _num(r.get("unit_price")), "regular_price": _num(r.get("regular_price")),
            "discount": _num(r.get("discount")) or 0, "amount": _num(r.get("line_total")) or 0,
            "weighed": (r.get("weighed") or "").upper() == "Y"})
    return bundle


items_csv.matches = lambda text: ITEM_COLUMNS <= _header(text) and "order_total" not in _header(text)


def attach_items(bundle, items):
    """Put item lines under their receipts; lines for receipts not in the bundle are reported."""
    by_id = {r["receipt_id"]: r for r in bundle["receipts"]}
    orphans = []
    for item in items:
        receipt = by_id.get(item["receipt_id"])
        if receipt is None:
            orphans.append(item)
            continue
        receipt["items"].append({k: v for k, v in item.items() if k not in ("receipt_id", "purchased_on")})
    # Each receipt's lines should add up to its net total (items after savings, before tax).
    checks = {}
    for r in bundle["receipts"]:
        if r["items"] and r["net"] is not None:
            parsed = round(sum(i["amount"] for i in r["items"]), 2)
            checks[r["receipt_id"]] = {"reported": r["net"], "parsed": parsed, "ok": abs(parsed - r["net"]) < 0.02}
    bundle["document"]["checks"] = checks
    if orphans:
        bundle["document"]["unmatched_item_lines"] = len(orphans)
