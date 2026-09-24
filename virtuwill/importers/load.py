"""Load a finance bundle into Lakebase: stage it (a preview of what would
change), then commit it.

Matching rules, the same for the preview and the commit:
- Accounts are found by their last four digits (and created when new).
- A bank transaction is the same as one already stored when the account and
  amount match and the dates are within 3 days (card statements print the
  purchase date, exports the posting date). It is then *seen again*: the new
  file is recorded as another source, and blanks (category, purchase date) are
  filled. A copy the Finance tracker made is *taken over*: it keeps its id,
  receipt links and your category, but belongs to the import from then on.
- A receipt is the same as one stored when the date and total match.
- Statements, balances and paychecks are keyed by account + period, account +
  date + kind, and deposit advice number.
Committing the same file twice does nothing.
"""
import hashlib
import re
from datetime import date, timedelta

from psycopg.types.json import Jsonb

from . import SECTIONS
from .canonical import validate

WINDOW = timedelta(days=3)
MOVEMENT_TYPES = {"card_payment": "Credit card payment", "transfer": "Internal transfer", "income": "Payroll deposit"}


class AlreadyCommitted(Exception):
    pass


def _d(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def account_id_for(conn, mask, info=None, create=False):
    """The account with these last four digits; created (when asked) from what the file says about it."""
    row = conn.execute("""SELECT account_id, account_type, institution, name FROM finance.accounts
                          WHERE mask = %s OR account_id = %s ORDER BY (account_id = %s) DESC LIMIT 1""",
                       (mask, "acct-" + mask, "acct-" + mask)).fetchone()
    info = info or {}
    if row:
        if create:
            # Fill in what was only guessed before (type 'other', no institution).
            if info.get("account_type") and row["account_type"] == "other":
                conn.execute("UPDATE finance.accounts SET account_type = %s WHERE account_id = %s", (info["account_type"], row["account_id"]))
            if info.get("institution") and not row["institution"]:
                conn.execute("UPDATE finance.accounts SET institution = %s WHERE account_id = %s", (info["institution"], row["account_id"]))
        return row["account_id"]
    if not create:
        return None
    account_id = "acct-" + mask
    conn.execute("""INSERT INTO finance.accounts (account_id, institution, name, mask, account_type)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING""",
                 (account_id, info.get("institution") or "", info.get("name") or f"Account {mask}", mask,
                  info.get("account_type") or "other"))
    return account_id


def _existing_transaction(conn, account_id, t, used):
    """The stored transaction this one repeats, if any: same account and amount, dates within 3 days."""
    if not account_id:
        return None
    day = _d(t.get("posted_on"))
    rows = conn.execute("""SELECT transaction_id, source, category, posted_on, transacted_on FROM finance.transactions
                           WHERE account_id = %s AND amount = %s AND posted_on BETWEEN %s AND %s""",
                        (account_id, t["amount"], day - WINDOW, day + WINDOW)).fetchall()
    rows = [r for r in rows if r["transaction_id"] not in used]
    if not rows:
        return None
    other = _d(t.get("transacted_on")) or day
    return min(rows, key=lambda r: min(abs((r["posted_on"] - day).days), abs((r["posted_on"] - other).days)))


def _existing_receipt(conn, r):
    return conn.execute("""SELECT receipt_id, source FROM finance.receipts
                           WHERE purchased_on = %s AND total = %s ORDER BY (source = 'import') DESC LIMIT 1""",
                        (r["purchased_on"], r["total"])).fetchone()


def preview(conn, bundle):
    """What committing would do, without changing anything."""
    bundle = validate(bundle)
    out = {"document": {k: bundle["document"].get(k) for k in ("filename", "doc_type", "institution", "parser",
                                                             "period_start", "period_end")},
           "checks": bundle["document"].get("checks") or {}, "warnings": [], "accounts": [], "counts": {}}
    committed = conn.execute("SELECT import_id FROM finance.staged_imports WHERE sha256 = %s AND status = 'committed'",
                             (bundle["document"]["sha256"],)).fetchone()
    if committed:
        out["warnings"].append(f"This file was already loaded (import #{committed['import_id']}); committing it again changes nothing.")
    failed = [k for k, v in out["checks"].items() if isinstance(v, dict) and not v.get("ok", True)]
    if failed:
        out["warnings"].append("Totals that don't match the document: " + ", ".join(failed[:8]))
    for a in bundle["accounts"]:
        found = account_id_for(conn, a["mask"])
        out["accounts"].append({"mask": a["mask"], "name": a.get("name"), "account_id": found, "new": found is None})
    used, tx = set(), {"new": 0, "seen": 0, "taken_over": 0}
    samples = {"new": [], "seen": [], "taken_over": []}
    for t in bundle["transactions"]:
        match = _existing_transaction(conn, account_id_for(conn, t["account_mask"]), t, used)
        kind = "new" if not match else ("taken_over" if match["source"] == "finance_tracker" else "seen")
        if match:
            used.add(match["transaction_id"])
        tx[kind] += 1
        if len(samples[kind]) < 5:
            samples[kind].append({"posted_on": t["posted_on"], "description": t["description"], "amount": t["amount"]})
    rc = {"new": 0, "seen": 0, "taken_over": 0}
    for r in bundle["receipts"]:
        match = _existing_receipt(conn, r)
        rc["new" if not match else ("seen" if match["source"] == "import" else "taken_over")] += 1
    pay = {"new": 0, "seen": 0}
    for p in bundle["paychecks"]:
        seen = conn.execute("SELECT 1 FROM finance.paychecks WHERE paycheck_id = %s", (_paycheck_id(p),)).fetchone()
        pay["seen" if seen else "new"] += 1
    out["counts"] = {"transactions": tx, "receipts": rc, "paychecks": pay,
                     "statements": len(bundle["statements"]), "balances": len(bundle["balances"])}
    out["samples"] = samples
    dates = sorted(t["posted_on"] for t in bundle["transactions"]) or sorted(p["pay_date"] for p in bundle["paychecks"]) \
        or sorted(r["purchased_on"] for r in bundle["receipts"])
    out["range"] = [dates[0], dates[-1]] if dates else None
    return out


def stage(conn, bundle, raw_asset_ids=()):
    bundle = validate(bundle)
    pv = preview(conn, bundle)
    row = conn.execute("""INSERT INTO finance.staged_imports (filename, sha256, parser, bundle, preview, raw_asset_ids)
                          VALUES (%s, %s, %s, %s, %s, %s) RETURNING import_id""",
                       (bundle["document"]["filename"], bundle["document"]["sha256"], bundle["document"].get("parser") or "",
                        Jsonb(bundle), Jsonb(pv), list(raw_asset_ids))).fetchone()
    return row["import_id"], pv


def _paycheck_id(p):
    return "adv-" + p["advice_number"] if p.get("advice_number") else "pay-" + p["pay_date"]


def _document(conn, bundle, import_id):
    doc = bundle["document"]
    row = conn.execute("SELECT document_id FROM finance.source_documents WHERE sha256 = %s", (doc["sha256"],)).fetchone()
    if row:
        return row["document_id"]
    mask = doc.get("account_mask")
    return conn.execute(
        """INSERT INTO finance.source_documents (original_filename, source_folder, sha256, doc_type, file_format, institution,
                                                 account_id, period_start, period_end, document_date, status, parsed_rows, notes)
           VALUES (%s, 'upload', %s, %s, %s, %s, %s, %s, %s, %s, 'parsed', %s, %s) RETURNING document_id""",
        (doc["filename"][:300], doc["sha256"], doc.get("doc_type") or "other", doc.get("file_format") or "other",
         doc.get("institution") or "", account_id_for(conn, mask) if mask else None, doc.get("period_start"),
         doc.get("period_end"), doc.get("document_date"),
         sum(len(bundle[s]) for s in SECTIONS), f"import #{import_id} · {doc.get('parser')}")).fetchone()["document_id"]


def _category(conn, name):
    if name:
        conn.execute("INSERT INTO finance.categories (category) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
    return name


def _merchant(description):
    """The bank's text without a trailing phone number or state code ("… 800-555-0100 CA")."""
    text = re.sub(r"\s+[\d-]{7,}\s+[A-Z]{2}$", "", description)
    return re.sub(r"\s{2,}", " ", text).strip()[:120] or description[:120]


def commit(conn, import_id):
    staged = conn.execute("SELECT * FROM finance.staged_imports WHERE import_id = %s FOR UPDATE", (import_id,)).fetchone()
    if not staged:
        raise LookupError("No such import")
    if staged["status"] != "staged":
        raise AlreadyCommitted(f"Import #{import_id} is {staged['status']}")
    bundle = validate(staged["bundle"])
    if conn.execute("SELECT 1 FROM finance.staged_imports WHERE sha256 = %s AND status = 'committed'",
                    (staged["sha256"],)).fetchone():
        conn.execute("UPDATE finance.staged_imports SET status = 'discarded', report = %s WHERE import_id = %s",
                     (Jsonb({"skipped": "already loaded"}), import_id))
        return {"skipped": "This file was already loaded."}
    document_id = _document(conn, bundle, import_id)
    report = {"transactions": {"new": 0, "seen": 0, "taken_over": 0}, "receipts": {"new": 0, "seen": 0, "taken_over": 0},
              "receipt_payments_matched": 0, "statements": 0, "balances": 0, "paychecks": 0, "accounts_created": 0}

    accounts = {}
    for a in bundle["accounts"]:
        existed = account_id_for(conn, a["mask"])
        accounts[a["mask"]] = account_id_for(conn, a["mask"], a, create=True)
        report["accounts_created"] += existed is None

    def acct(mask):
        if not mask:
            return None
        if mask not in accounts:
            accounts[mask] = account_id_for(conn, mask, {}, create=True)
        return accounts[mask]

    # Bank transactions
    used, occurrences = set(), {}
    for t in bundle["transactions"]:
        account_id = acct(t["account_mask"])
        match = _existing_transaction(conn, account_id, t, used)
        movement = MOVEMENT_TYPES.get(t["kind"])
        category = None if movement else _category(conn, t.get("category") or "Review")
        raw = {k: v for k, v in t.items() if k != "source_row"}
        if match:
            used.add(match["transaction_id"])
            kind = "taken_over" if match["source"] == "finance_tracker" else "seen"
            conn.execute(
                """UPDATE finance.transactions SET
                       source = CASE WHEN source = 'finance_tracker' THEN 'import' ELSE source END,
                       transacted_on = COALESCE(transacted_on, %s),
                       description_raw = CASE WHEN description_raw = '' OR source = 'finance_tracker' AND %s <> '' THEN %s ELSE description_raw END,
                       category = CASE WHEN kind = 'expense' AND (category IS NULL OR category = 'Review') AND %s::text IS NOT NULL
                                       THEN %s ELSE category END
                   WHERE transaction_id = %s""",
                (t.get("transacted_on"), t["description"], t["description"], category, category, match["transaction_id"]))
            transaction_id = match["transaction_id"]
        else:
            kind = "new"
            key = (account_id, t.get("transacted_on") or t["posted_on"], t["amount"], t["description"])
            occurrences[key] = occurrences.get(key, 0) + 1
            fingerprint = hashlib.sha256(repr((*key, occurrences[key])).encode()).hexdigest()[:20]
            transaction_id = f"imp-{account_id}-{fingerprint}"
            if movement:
                conn.execute("INSERT INTO finance.movement_types (movement_type, flow) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (movement, {"card_payment": "card_payment", "transfer": "transfer", "income": "income"}[t["kind"]]))
            conn.execute(
                """INSERT INTO finance.transactions (transaction_id, account_id, account_text, posted_on, transacted_on,
                                                     description_raw, merchant, amount, kind, category, movement_type, fingerprint, source, details)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'import', %s)
                   ON CONFLICT DO NOTHING""",
                (transaction_id, account_id, t["account_mask"], t["posted_on"], t.get("transacted_on"), t["description"],
                 _merchant(t["description"]), t["amount"], "movement" if movement else "expense", category, movement,
                 fingerprint, Jsonb({"source_category": t.get("source_category")} if t.get("source_category") else {})))
        report["transactions"][kind] += 1
        conn.execute("""INSERT INTO finance.transaction_sources (transaction_id, document_id, source_row, raw)
                        VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                     (transaction_id, document_id, t.get("source_row") or "", Jsonb(raw)))

    # Statements and balances
    statement_ids = {}
    for s in bundle["statements"]:
        account_id = acct(s["account_mask"])
        row = conn.execute(
            """INSERT INTO finance.statements (document_id, account_id, period_start, period_end, opening_balance, closing_balance,
                                               total_debits, total_credits, interest_charged, fees_charged, minimum_due,
                                               payment_due_on, credit_limit)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (account_id, period_end) DO NOTHING RETURNING statement_id""",
            (document_id, account_id, s["period_start"], s["period_end"], s.get("opening_balance"), s.get("closing_balance"),
             s.get("total_debits"), s.get("total_credits"), s.get("interest_charged"), s.get("fees_charged"),
             s.get("minimum_due"), s.get("payment_due_on"), s.get("credit_limit"))).fetchone()
        if row:
            statement_ids[(s["account_mask"], s["period_start"], s["period_end"])] = row["statement_id"]
            report["statements"] += 1
    for b in bundle["balances"]:
        account_id = acct(b["account_mask"])
        statement_id = next((sid for (mask, start, end), sid in statement_ids.items() if mask == b["account_mask"]
                             and b["as_of"] in (start, end)), None)
        if b["kind"].startswith("statement_") and not statement_id:
            continue                                  # its statement was loaded before
        exists = conn.execute("""SELECT 1 FROM finance.balance_snapshots WHERE account_id = %s AND as_of = %s
                                 AND balance_kind = %s AND source = 'import'""", (account_id, b["as_of"], b["kind"])).fetchone()
        if exists:
            continue
        conn.execute("""INSERT INTO finance.balance_snapshots (as_of, account_id, balance, balance_kind, statement_id, document_id, source)
                        VALUES (%s, %s, %s, %s, %s, %s, 'import')""",
                     (b["as_of"], account_id, b["balance"], b["kind"], statement_id, document_id))
        report["balances"] += 1

    # Paychecks
    for p in bundle["paychecks"]:
        paycheck_id = _paycheck_id(p)
        inserted = conn.execute(
            """INSERT INTO finance.paychecks (paycheck_id, pay_date, period_start, period_end, employer, gross, pre_tax, post_tax,
                                              taxes, net, gross_ytd, net_ytd, document_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (paycheck_id, p["pay_date"], p.get("period_start"), p.get("period_end"), p.get("employer") or "", p["gross"],
             p.get("pre_tax") or 0, p.get("post_tax") or 0, p.get("taxes") or 0, p["net"], p.get("gross_ytd"), p.get("net_ytd"),
             document_id)).rowcount
        if not inserted:
            continue
        report["paychecks"] += 1
        for i, l in enumerate(p.get("lines") or []):
            conn.execute("""INSERT INTO finance.paycheck_lines VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                         (paycheck_id, i, l["section"], l["name"], l.get("hours"), l.get("rate"), l.get("current"),
                          l.get("ytd_hours"), l.get("ytd")))
        for i, d in enumerate(p.get("deposits") or []):
            conn.execute("INSERT INTO finance.paycheck_splits VALUES (%s, %s, %s, %s, %s)",
                         (paycheck_id, i, acct(d["account_mask"]), d["account_mask"], d["amount"]))

    # Receipts, their lines, and the bank charge each one explains
    for r in bundle["receipts"]:
        match = _existing_receipt(conn, r)
        if match and match["source"] == "import":
            report["receipts"]["seen"] += 1
            continue
        receipt_id = match["receipt_id"] if match else r["receipt_id"]
        values = (r["purchased_on"], r.get("merchant") or "Store", r.get("store_location") or "", r.get("net") or r["total"],
                  r.get("tax") or 0, r["total"], r.get("savings") or 0, r.get("payment_text") or "", document_id,
                  Jsonb({"regular_total": r.get("regular_total"), "item_count": r.get("item_count"), "import_receipt_id": r["receipt_id"]}))
        if match:
            conn.execute("""UPDATE finance.receipts SET purchased_on = %s, merchant = %s, store_location = %s, net = %s, tax = %s,
                                   total = %s, savings = %s, payment_text = %s, document_id = %s, details = details || %s,
                                   source = 'import' WHERE receipt_id = %s""", (*values, receipt_id))
            report["receipts"]["taken_over"] += 1
        else:
            conn.execute("""INSERT INTO finance.receipts (purchased_on, merchant, store_location, net, tax, total, savings,
                                                          payment_text, document_id, details, receipt_id, source, category)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'import', 'Groceries')""", (*values, receipt_id))
            report["receipts"]["new"] += 1
        if r.get("items"):
            conn.execute("DELETE FROM finance.receipt_items WHERE receipt_id = %s", (receipt_id,))
            for item in r["items"]:
                category = item.get("item_category") or "Review"
                conn.execute("INSERT INTO finance.item_categories VALUES (%s, 'Groceries') ON CONFLICT DO NOTHING", (category,))
                conn.execute(
                    """INSERT INTO finance.receipt_items (receipt_id, line, item_name, quantity, unit, unit_price, amount, discount,
                                                          item_category, regular_price, store_brand, weighed)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (receipt_id, item["line"], item["item_name"], item.get("quantity") or 1, item.get("unit") or "ea",
                     item.get("unit_price"), item.get("amount") or 0, item.get("discount") or 0, category,
                     item.get("regular_price"), item.get("store_brand"), item.get("weighed")))
        # Match the receipt to its bank charge: same account and amount, within 3 days.
        if not conn.execute("SELECT 1 FROM finance.receipt_payments WHERE receipt_id = %s AND transaction_id IS NOT NULL",
                            (receipt_id,)).fetchone():
            conn.execute("DELETE FROM finance.receipt_payments WHERE receipt_id = %s", (receipt_id,))
            account_id = acct(r.get("account_mask"))
            charge = _existing_transaction(conn, account_id, {"amount": r["total"], "posted_on": r["purchased_on"]},
                                           {x["transaction_id"] for x in conn.execute(
                                               "SELECT transaction_id FROM finance.receipt_payments WHERE transaction_id IS NOT NULL")})
            conn.execute("""INSERT INTO finance.receipt_payments (receipt_id, tender_text, account_id, amount, transaction_id,
                                                                  match_method, match_score, matched_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN NULL ELSE now() END)""",
                         (receipt_id, r.get("payment_text") or "", account_id, r["total"],
                          charge["transaction_id"] if charge else None, "auto" if charge else None, 1 if charge else None,
                          charge["transaction_id"] if charge else None))
            report["receipt_payments_matched"] += bool(charge)

    conn.execute("""UPDATE finance.staged_imports SET status = 'committed', report = %s, document_id = %s, committed_at = now()
                    WHERE import_id = %s""", (Jsonb(report), document_id, import_id))
    return report


def load(conn, bundle):
    """Stage and commit in one step (the command-line loader)."""
    import_id, _ = stage(conn, bundle)
    return import_id, commit(conn, import_id)

