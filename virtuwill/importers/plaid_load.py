"""Load Plaid rows from the lakehouse's bronze tables into the finance model.

jobs/plaid_bronze_to_lakebase.py reads prod.bronze.raw_plaid_balances and
raw_plaid_transactions and hands the rows here, with a connection to the
app's Lakebase database. Everything goes through the same staging and
matching as Money › Imports, in one transaction:

- accounts (from each account's latest balance row), created on first sight;
- balances: one reading per account, day and kind, the latest pull of the day winning;
- transactions: matched on Plaid's id, so a re-sent or modified one updates in place;
- transactions Plaid removed (a pending charge that settled under a new id, or
  one that was voided) are deleted, unless a receipt points at them.

No Flask here: the job imports this on a Databricks cluster.
"""
import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import empty_bundle, plaid
from . import load as loader

SOURCE = "plaid_bronze"


def account_from_row(row):
    """A raw_plaid_balances row in the shape Plaid's API gives an account."""
    return {"account_id": row["account_id"], "mask": row.get("mask"), "name": row.get("account_name"),
            "official_name": row.get("official_name"), "type": row.get("type"), "subtype": row.get("subtype"),
            "balances": {"current": row.get("current"), "available": row.get("available"),
                         "iso_currency_code": row.get("iso_currency_code")}}


def transaction_from_row(row):
    """A raw_plaid_transactions row in the shape Plaid's API gives a transaction."""
    category = row.get("personal_finance_category") or row.get("category")
    return {**row, "personal_finance_category": plaid.personal_finance_category(category)}


def local_day(pulled_at, tz):
    """The calendar day (in the app's time zone) of a pull stamped like '2026-09-28T17:03:46.123+00:00'."""
    stamp = pulled_at if isinstance(pulled_at, datetime) else datetime.fromisoformat(str(pulled_at).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(ZoneInfo(tz)).date().isoformat()


def state(conn):
    return conn.execute("SELECT * FROM finance.plaid_bronze_load").fetchone() or \
        {"balances_through": None, "transactions_through": None}


def apply(conn, accounts, balances, transactions, removed, *, through=None, overrides=None, tz="America/Chicago",
          dry_run=False):
    """Load one batch. accounts: the latest raw_plaid_balances row per account; balances: the balance rows
    ingested since the last load; transactions: the current state of each transaction changed since then;
    removed: the ids of transactions whose latest state is 'removed'.
    through: {"balances": ts, "transactions": ts}, how far bronze has now been read (saved with the load).
    Returns a report; a dry run changes nothing."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('virtuwill_plaid_bronze'))")
    overrides = overrides or {}

    masks, account_rows, skipped = {}, [], []
    for row in accounts:
        account, mask = plaid.account_row(row.get("item_label") or row.get("institution_id") or "Plaid", account_from_row(row),
                                          overrides)
        if not account:
            skipped.append(mask)
            continue
        masks[row["account_id"]] = mask
        account_rows.append(account)

    balance_rows = []
    for row in sorted(balances, key=lambda r: str(r.get("_pulled_at"))):       # the day's last pull wins
        if row["account_id"] in masks:
            balance_rows += plaid.balance_rows(account_from_row(row), masks[row["account_id"]],
                                               local_day(row["_pulled_at"], tz))

    transaction_rows, unmatched = [], 0
    for row in sorted(transactions, key=lambda r: not r.get("pending")):    # pending first; a settled twin has the last word
        if row.get("account_id") not in masks:
            unmatched += 1
            continue
        transaction_rows.append(plaid.transaction_row(transaction_from_row(row), masks[row["account_id"]]))

    gone = _removable(conn, removed, masks)
    report = {"accounts": len(account_rows), "balances": len(balance_rows), "transactions": len(transaction_rows),
              "removed": len(gone), "skipped_accounts": skipped, "transactions_without_account": unmatched}
    if balance_rows or transaction_rows:
        bundle = _bundle(account_rows, balance_rows, transaction_rows)
        if dry_run:
            report["preview"] = loader.preview(conn, bundle)["counts"]
        else:
            import_id, _ = loader.stage(conn, bundle, submitted_by="lakehouse:plaid")
            report |= {"import_id": import_id, "loaded": loader.commit(conn, import_id)}
    report["nothing_new"] = not (balance_rows or transaction_rows or gone)
    if dry_run:
        return report | {"dry_run": True}
    if gone:
        conn.execute("DELETE FROM finance.transactions WHERE transaction_id = ANY(%s)", (gone,))
    through = through or {}
    conn.execute("""INSERT INTO finance.plaid_bronze_load (id, balances_through, transactions_through) VALUES (true, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        balances_through = COALESCE(EXCLUDED.balances_through, finance.plaid_bronze_load.balances_through),
                        transactions_through = COALESCE(EXCLUDED.transactions_through, finance.plaid_bronze_load.transactions_through),
                        updated_at = now()""",
                 (through.get("balances"), through.get("transactions")))
    record(conn, report)
    return report


def record(conn, report):
    """The latest load's outcome, shown in the workspace's Settings › Diagnostics."""
    conn.execute("""INSERT INTO virtuwill.sync_reports (source, report) VALUES (%s, %s)
                    ON CONFLICT (source) DO UPDATE SET report = EXCLUDED.report, synced_at = now()""",
                 (SOURCE, Jsonb(report)))


def _removable(conn, removed, masks):
    """The stored rows for transactions Plaid removed, on Plaid's accounts, that no receipt points at."""
    if not removed:
        return []
    account_ids = [a for a in (loader.account_id_for(conn, m) for m in set(masks.values())) if a]
    if not account_ids:
        return []
    return [r["transaction_id"] for r in conn.execute(
        """SELECT t.transaction_id FROM finance.transactions t
           WHERE t.account_id = ANY(%s) AND t.external_id = ANY(%s)
             AND NOT EXISTS (SELECT 1 FROM finance.receipt_payments p WHERE p.transaction_id = t.transaction_id)""",
        (account_ids, sorted(removed)))]


def _bundle(accounts, balances, transactions):
    content = json.dumps({"accounts": accounts, "balances": balances, "transactions": transactions},
                         sort_keys=True, default=str).encode()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bundle = empty_bundle({"filename": f"Lakehouse · Plaid · {stamp}", "sha256": hashlib.sha256(content).hexdigest(),
                           "doc_type": "transaction_export", "file_format": "other", "institution": "",
                           "parser": "lakehouse-plaid"})
    bundle.update(accounts=accounts, balances=balances, transactions=transactions)
    return bundle
