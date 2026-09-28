"""Plaid, served from the lakehouse: load the synced Plaid tables into the finance model.

The lakehouse keeps Plaid's balances and transactions (prod.silver.plaid_*, see
lakehouse/plaid_serving.sql). Lakebase synced tables mirror them into this
database, read-only, as PLAID_MIRROR_SCHEMA.plaid_transactions (one row per
transaction, current state) and .plaid_balances (every balance pulled). A sync
reads what changed since the last one and loads it through the same staging
and matching as Money › Imports, so the Money screens need nothing new:

- transactions synced since the last run, matched on the bank's id;
- balances pulled since the last run, one reading per account, day and kind;
- pending charges Plaid has since dropped (a pending charge that settled under
  a new id, or one that was voided) are removed.

    GET  /api/v1/money/plaid-mirror                what's in the mirror, how far it has been loaded
    POST /api/v1/money/plaid-mirror/sync           load what changed ({"dry_run": true} to preview)
    GET/POST /api/ingest/v1/finance/plaid-mirror   the same for a scheduled job, with an API token

The daily job (jobs/plaid_lakebase_refresh.py) calls the POST after it has
refreshed the synced tables.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from psycopg import sql

from . import db
from .api_tokens import token_required
from .auth import admin_required
from .importers import ExtractError, empty_bundle
from .importers import load as loader
from .importers import plaid

log = logging.getLogger(__name__)
bp = Blueprint("plaid_mirror", __name__)
SOURCE = "plaid_mirror"
TABLES = ("plaid_transactions", "plaid_balances")


class MirrorUnavailable(Exception):
    """The synced tables are missing, or this app can't read them."""


def schema():
    return os.environ.get("PLAID_MIRROR_SCHEMA", "public")


def _table(name):
    return sql.Identifier(schema(), name)


def _check_tables(conn):
    """The synced tables' columns, by table; raises MirrorUnavailable if one is missing or unreadable."""
    columns = {}
    for name in TABLES:
        qualified = f"{schema()}.{name}"
        found = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS found", (qualified,)).fetchone()["found"]
        if not found:
            raise MirrorUnavailable(f"{qualified} isn't in this database yet: create its synced table (docs/plaid-lakebase.md)")
        if not conn.execute("SELECT has_table_privilege(%s, 'SELECT') AS ok", (qualified,)).fetchone()["ok"]:
            raise MirrorUnavailable(f"The app can't read {qualified}: grant it SELECT (docs/plaid-lakebase.md)")
        columns[name] = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (schema(), name))}
    return columns


def _plain(value):
    """Numbers as floats, so the bundle can be stored as JSON."""
    return float(value) if value is not None else None


def _account(row):
    """A plaid_balances row in the shape Plaid's API gives an account."""
    return {"account_id": row["account_id"], "mask": row.get("mask"), "name": row.get("account_name"),
            "official_name": row.get("official_name"), "type": row.get("type"), "subtype": row.get("subtype"),
            "balances": {"current": _plain(row.get("current")), "available": _plain(row.get("available")),
                         "iso_currency_code": row.get("iso_currency_code")}}


def _transaction(row):
    """A plaid_transactions row in the shape Plaid's API gives a transaction."""
    category = row.get("personal_finance_category") or row.get("category")
    return {**row, "amount": _plain(row["amount"]), "personal_finance_category": plaid.personal_finance_category(category)}


def _state(conn):
    return conn.execute("SELECT * FROM finance.plaid_mirror").fetchone() or {"transactions_through": None, "balances_through": None}


def _iso(value):
    return value.isoformat() if value else None


def status(conn):
    """What the mirror holds and how far it has been loaded."""
    state = _state(conn)
    out = {"schema": schema(), "available": True, "loaded_through": {"transactions": _iso(state["transactions_through"]),
                                                                   "balances": _iso(state["balances_through"])}}
    try:
        _check_tables(conn)
    except MirrorUnavailable as e:
        return out | {"available": False, "error": str(e)}
    t = conn.execute(sql.SQL("SELECT COUNT(*) AS n, MAX(synced_at) AS newest FROM {}").format(_table("plaid_transactions"))).fetchone()
    b = conn.execute(sql.SQL("SELECT COUNT(*) AS n, COUNT(DISTINCT account_id) AS accounts, MAX(as_of) AS newest FROM {}")
                     .format(_table("plaid_balances"))).fetchone()
    waiting = conn.execute(sql.SQL("SELECT COUNT(*) AS n FROM {} WHERE %s::timestamptz IS NULL OR synced_at > %s")
                           .format(_table("plaid_transactions")),
                           (state["transactions_through"], state["transactions_through"])).fetchone()["n"]
    last = conn.execute("SELECT report, synced_at FROM virtuwill.sync_reports WHERE source = %s", (SOURCE,)).fetchone()
    return out | {"transactions": {"rows": t["n"], "newest": _iso(t["newest"]), "not_loaded": waiting},
                  "balances": {"rows": b["n"], "accounts": b["accounts"], "newest": _iso(b["newest"])},
                  "last_sync": {"at": last["synced_at"].isoformat(), **last["report"]} if last else None}


def sync(conn, dry_run=False, overrides=None):
    """Load what changed in the mirror since the last sync. A dry run reports what would change and keeps nothing."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('virtuwill_plaid_mirror'))")
    columns = _check_tables(conn)
    overrides = overrides if overrides is not None else json.loads(os.environ.get("PLAID_MASKS") or "{}")
    state = _state(conn)
    t_since, b_since = state["transactions_through"], state["balances_through"]
    txn_table, bal_table = _table("plaid_transactions"), _table("plaid_balances")

    # Accounts, from each one's latest balance row: its mask, name, type and bank.
    masks, accounts, skipped = {}, [], []
    for row in conn.execute(sql.SQL("SELECT DISTINCT ON (account_id) * FROM {} ORDER BY account_id, as_of DESC").format(bal_table)):
        account, mask = plaid.account_row(row.get("item_label") or row.get("institution_id") or "Plaid", _account(row), overrides)
        if not account:
            skipped.append(mask)
            continue
        masks[row["account_id"]] = mask
        accounts.append(account)

    # Balances pulled since the last sync, as the day they were pulled (in the app's time zone).
    balances, b_newest = [], b_since
    for row in conn.execute(sql.SQL("""SELECT *, as_of::date AS as_of_day, as_of::timestamptz AS as_of_tz FROM {} WHERE %s::timestamptz IS NULL OR as_of > %s
                                       ORDER BY as_of""").format(bal_table), (b_since, b_since)):
        b_newest = max(filter(None, (b_newest, row["as_of_tz"])))
        if row["account_id"] in masks:
            balances += plaid.balance_rows(_account(row), masks[row["account_id"]], row["as_of_day"].isoformat())

    # Transactions synced since the last sync. Pending ones first, so a settled charge in the same batch has the last word.
    transactions, t_newest, unmatched = [], t_since, 0
    rows = conn.execute(sql.SQL("""SELECT *, synced_at::timestamptz AS synced_tz FROM {}
                                WHERE %s::timestamptz IS NULL OR synced_at > %s ORDER BY synced_at""")
                        .format(txn_table), (t_since, t_since)).fetchall()
    for row in sorted(rows, key=lambda r: not r.get("pending")):
        t_newest = max(filter(None, (t_newest, row["synced_tz"])))
        if row["account_id"] not in masks:
            unmatched += 1
            continue
        transactions.append(plaid.transaction_row(_transaction(row), masks[row["account_id"]]))

    stale = _stale_pending(conn, columns["plaid_transactions"], txn_table, masks)
    report = {"accounts": len(accounts), "balances": len(balances), "transactions": len(transactions),
              "pending_removed": len(stale), "skipped_accounts": skipped,
              "transactions_without_account": unmatched, "dry_run": bool(dry_run)}

    if balances or transactions:
        bundle = _bundle(accounts, balances, transactions)
        if dry_run:
            report["preview"] = loader.preview(conn, bundle)["counts"]
        else:
            import_id, _ = loader.stage(conn, bundle, submitted_by="lakebase:plaid")
            report |= {"import_id": import_id, "loaded": loader.commit(conn, import_id)}
    else:
        report["nothing_new"] = True
    if dry_run:
        return report
    if stale:
        conn.execute("DELETE FROM finance.transactions WHERE transaction_id = ANY(%s)", (stale,))
    conn.execute("""INSERT INTO finance.plaid_mirror (id, transactions_through, balances_through) VALUES (true, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET transactions_through = EXCLUDED.transactions_through,
                                                   balances_through = EXCLUDED.balances_through, updated_at = now()""",
                 (t_newest, b_newest))
    _record(conn, {k: v for k, v in report.items() if k != "dry_run"})
    return report


def _stale_pending(conn, columns, txn_table, masks):
    """Pending charges loaded from Plaid that the mirror no longer has: settled under a new id, or voided.
    Only rows no receipt points at; nothing when the mirror is empty (a sync still starting up)."""
    if not masks:
        return []
    pick = "pending_transaction_id" if "pending_transaction_id" in columns else "NULL"
    ids = set()
    for r in conn.execute(sql.SQL("SELECT transaction_id, {} AS pending_transaction_id FROM {}").format(sql.SQL(pick), txn_table)):
        ids.update(filter(None, (r["transaction_id"], r["pending_transaction_id"])))
    if not ids:
        return []
    account_ids = [a for a in (loader.account_id_for(conn, m) for m in set(masks.values())) if a]
    return [r["transaction_id"] for r in conn.execute(
        """SELECT t.transaction_id FROM finance.transactions t
           WHERE t.is_pending AND t.external_id IS NOT NULL AND t.account_id = ANY(%s) AND NOT (t.external_id = ANY(%s))
             AND NOT EXISTS (SELECT 1 FROM finance.receipt_payments p WHERE p.transaction_id = t.transaction_id)""",
        (account_ids, sorted(ids)))]


def _bundle(accounts, balances, transactions):
    content = json.dumps({"accounts": accounts, "balances": balances, "transactions": transactions}, sort_keys=True).encode()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bundle = empty_bundle({"filename": f"Lakebase · Plaid · {stamp}", "sha256": hashlib.sha256(content).hexdigest(),
                           "doc_type": "transaction_export", "file_format": "other", "institution": "", "parser": "lakebase-plaid"})
    bundle.update(accounts=accounts, balances=balances, transactions=transactions)
    return bundle


def _record(conn, report):
    conn.execute("""INSERT INTO virtuwill.sync_reports (source, report) VALUES (%s, %s)
                    ON CONFLICT (source) DO UPDATE SET report = EXCLUDED.report, synced_at = now()""", (SOURCE, db.jsonb(report)))


def _run(dry_run):
    try:
        with db.tx() as conn:
            return jsonify(sync(conn, dry_run)), 200
    except (MirrorUnavailable, ExtractError) as error:
        failure, code = str(error), 409 if isinstance(error, MirrorUnavailable) else 400
    if not dry_run:
        with db.tx() as conn:
            _record(conn, {"error": failure})
    return jsonify({"error": failure}), code


def _dry_run():
    return bool((request.get_json(silent=True) or {}).get("dry_run"))


@bp.route("/api/v1/money/plaid-mirror")
@admin_required
def mirror_status():
    with db.tx() as conn:
        return jsonify(status(conn))


@bp.route("/api/v1/money/plaid-mirror/sync", methods=["POST"])
@admin_required
def mirror_sync():
    return _run(_dry_run())


@bp.route("/api/ingest/v1/finance/plaid-mirror")
@token_required("finance:read")
def mirror_status_for_jobs():
    with db.tx() as conn:
        return jsonify(status(conn))


@bp.route("/api/ingest/v1/finance/plaid-mirror", methods=["POST"])
@token_required("finance:write")
def mirror_sync_for_jobs():
    return _run(_dry_run())
