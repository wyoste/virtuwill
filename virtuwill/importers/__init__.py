"""Read files exported from bank, card, payroll and store portals into one
structured format, the *finance bundle*, and load bundles into Lakebase.

    extract(path)  → bundle (dict)     raw export → structured data
    to_csv(bundle, folder)              bundle → one CSV per record type
    from_files(paths) → bundle          bundle JSON, or the CSVs to_csv writes

The bundle format ("virtuwill-finance/1"):

    document      the source file: name, sha256, type, institution, period, parser
    accounts      [{mask, institution, name, account_type}]
    statements    [{account_mask, period_start, period_end, opening_balance, closing_balance,
                    total_credits, total_debits, fees_charged, interest_charged,
                    minimum_due, payment_due_on, credit_limit}]
    balances      [{account_mask, as_of, balance, kind}]
    transactions  [{account_mask, transacted_on, posted_on, description, amount, kind,
                    category, source_row}]            amount > 0 is money leaving the account
    paychecks     [{advice_number, pay_date, period_start, period_end, employer, gross,
                    pre_tax, post_tax, taxes, net, lines: [...], deposits: [{account_mask, amount}]}]
    receipts      [{receipt_id, purchased_on, merchant, store_location, regular_total, savings,
                    net, tax, total, payment_text, account_mask, items: [...]}]

Only the last four digits of an account are ever kept; routing numbers,
addresses and employee ids in the source files are dropped.
"""
import hashlib
from pathlib import Path

FORMAT = "virtuwill-finance/1"
SECTIONS = ("accounts", "statements", "balances", "transactions", "paychecks", "receipts")


class ExtractError(ValueError):
    """The file isn't one this tool knows how to read."""


def empty_bundle(document):
    return {"format": FORMAT, "document": document, **{s: [] for s in SECTIONS}}


def document_info(path, content, doc_type, file_format, institution, parser, **extra):
    return {"filename": Path(path).name, "sha256": hashlib.sha256(content).hexdigest(), "doc_type": doc_type,
            "file_format": file_format, "institution": institution, "parser": parser, **extra}


def extract(path, content=None):
    """Read one raw export (PDF or CSV) and return a finance bundle."""
    from . import chase, kroger, payroll, pdftext
    path = Path(path)
    content = content if content is not None else path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = pdftext.pages(content)
        text = "\n".join(pages)
        for parser in (chase.spending_report, chase.card_statement, payroll.earning_statements):
            if parser.matches(text):
                return parser(path, content, pages)
        raise ExtractError(f"{path.name}: not a PDF this tool recognises (Chase spending report, Chase card "
                           "statement, or payroll earning statement)")
    if suffix == ".csv":
        text = content.decode("utf-8-sig")
        for parser in (kroger.receipts_csv, kroger.items_csv):
            if parser.matches(text):
                return parser(path, content, text)
        from .canonical import from_csv_text
        bundle = from_csv_text(path, content, text)
        if bundle:
            return bundle
        raise ExtractError(f"{path.name}: not a CSV this tool recognises")
    if suffix == ".json":
        from .canonical import validate
        import json
        try:
            return validate(json.loads(content))
        except ValueError:
            raise ExtractError(f"{path.name}: not valid JSON") from None
    raise ExtractError(f"{path.name}: unsupported file type {suffix or '(none)'}")


def merge(bundles):
    """Combine bundles; receipts and their item lines from separate files are joined by receipt id."""
    from .kroger import attach_items
    if not bundles:
        raise ExtractError("No files")
    out = empty_bundle(bundles[0]["document"] if len(bundles) == 1 else
                       {"filename": " + ".join(b["document"]["filename"] for b in bundles), "parts": [b["document"] for b in bundles],
                        "sha256": hashlib.sha256("".join(sorted(b["document"]["sha256"] for b in bundles)).encode()).hexdigest(),
                        "doc_type": bundles[0]["document"]["doc_type"], "file_format": bundles[0]["document"]["file_format"],
                        "institution": bundles[0]["document"].get("institution", ""), "parser": "merged"})
    loose_items = []
    for b in bundles:
        for s in SECTIONS:
            out[s].extend(b.get(s) or [])
        loose_items.extend(b.get("receipt_items") or [])
    seen = set()
    out["accounts"] = [a for a in out["accounts"] if not (a["mask"] in seen or seen.add(a["mask"]))]
    if loose_items:
        attach_items(out, loose_items)
    return out
