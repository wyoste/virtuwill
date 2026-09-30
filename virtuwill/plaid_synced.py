"""Plaid, from the Lakebase synced tables into the finance model — automatically.

The lakehouse's bronze Plaid tables (prod.bronze.raw_plaid_balances and
raw_plaid_transactions, landed by the plaid_integration notebook) are mirrored
into this database's bronze schema by Lakebase synced tables, read-only (TABLES). Every
PLAID_SYNC_MINUTES the app reads what landed in them since the last load and
loads it into finance.accounts, finance.transactions and
finance.balance_snapshots, through the same staging and matching as
Money › Imports (virtuwill/importers/plaid_load.py). How far it has read is
saved in the same transaction, so a failed load is simply retried next time.

    PLAID_SYNC_MINUTES          how often to check (0, the default, turns the loop off)
    PLAID_MASKS                 optional JSON {"<plaid account_id>": "1234"} for accounts with no four-digit mask

    GET  /api/v1/money/plaid-sync        what the synced tables hold, how far they've been loaded
    POST /api/v1/money/plaid-sync        load now ({"dry_run": true} to preview)
"""
import json
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, request
from psycopg import sql

from . import db
from .auth import admin_required
from .importers import ExtractError, plaid_load

log = logging.getLogger(__name__)
bp = Blueprint("plaid_synced", __name__)


class SyncedTablesUnavailable(Exception):
    """The synced tables are missing, or this app can't read them."""


# The synced copies of prod.bronze.raw_plaid_balances / raw_plaid_transactions, in Lakebase's bronze schema.
TABLES = {"balances": ("bronze", "plaid_balance"), "transactions": ("bronze", "plaid_transaction")}


def _columns(conn):
    """Each synced table's columns; raises SyncedTablesUnavailable if one is missing or unreadable."""
    out = {}
    for kind, (schema, name) in TABLES.items():
        qualified = f"{schema}.{name}"
        if not conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (sql.Identifier(schema, name).as_string(conn),)).fetchone()["ok"]:
            raise SyncedTablesUnavailable(f"{qualified} isn't in this database: check the synced table's name "
                                          "(docs/plaid-lakebase.md).")
        if not conn.execute("SELECT has_table_privilege(%s, 'SELECT') AS ok", (sql.Identifier(schema, name).as_string(conn),)).fetchone()["ok"]:
            raise SyncedTablesUnavailable(f"The app can't read {qualified}: grant its role SELECT (docs/plaid-lakebase.md).")
        out[kind] = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (schema, name))}
    return out


def _stamp(columns):
    """When a row landed: Auto Loader's _ingested_at, or the pull's own time if that column wasn't synced."""
    return sql.SQL("_ingested_at::timestamptz" if "_ingested_at" in columns else "_pulled_at::timestamptz")


def read(conn, since, columns):
    """What landed since the last load: (accounts, balances, transactions, removed ids, through, replaced pending ids)."""
    bal, txn = (sql.Identifier(*TABLES[k]) for k in ("balances", "transactions"))
    b_at, t_at = _stamp(columns["balances"]), _stamp(columns["transactions"])
    through = {"balances": conn.execute(sql.SQL("SELECT MAX({}) AS t FROM {}").format(b_at, bal)).fetchone()["t"],
               "transactions": conn.execute(sql.SQL("SELECT MAX({}) AS t FROM {}").format(t_at, txn)).fetchone()["t"]}
    window = sql.SQL("{at} > COALESCE(%(since)s::timestamptz, '-infinity') AND {at} <= %(until)s::timestamptz")
    pick = sql.SQL("""account_id, item_label, institution_id, account_name, official_name, mask,
                      NULLIF(type, 'None') AS type, NULLIF(subtype, 'None') AS subtype,
                      current, available, iso_currency_code, _pulled_at""")
    # Every account, from its latest pull: masks and names for transactions whose balances didn't change.
    accounts = conn.execute(sql.SQL("""SELECT DISTINCT ON (account_id) {pick} FROM {bal} WHERE account_id IS NOT NULL
                                       ORDER BY account_id, _pulled_at::timestamptz DESC, {at} DESC""")
                            .format(pick=pick, bal=bal, at=b_at)).fetchall()
    balances = conn.execute(sql.SQL("SELECT {pick} FROM {bal} WHERE account_id IS NOT NULL AND {window}")
                            .format(pick=pick, bal=bal, window=window.format(at=b_at)),
                            {"since": since["balances"], "until": through["balances"]}).fetchall()
    # Each transaction touched since the last load, at its latest sync (added, modified or removed).
    latest = conn.execute(sql.SQL("""SELECT DISTINCT ON (transaction_id) * FROM {txn}
                                     WHERE transaction_id IN (SELECT transaction_id FROM {txn} WHERE {window})
                                       AND {at} <= %(until)s::timestamptz
                                     ORDER BY transaction_id, _pulled_at::timestamptz DESC, {at} DESC""")
                          .format(txn=txn, window=window.format(at=t_at), at=t_at),
                          {"since": since["transactions"], "until": through["transactions"]}).fetchall()
    removed = {r["transaction_id"] for r in latest if r.get("_sync_op") == "removed"}
    # Pending ids a posted charge has taken over (only when the notebook keeps pending_transaction_id).
    replaced = set()
    if "pending_transaction_id" in columns["transactions"] and removed:
        replaced = {r["p"] for r in conn.execute(sql.SQL("""SELECT DISTINCT pending_transaction_id AS p FROM {txn}
                                                             WHERE pending_transaction_id = ANY(%s)""").format(txn=txn),
                                                 (sorted(removed),))}
    return accounts, balances, [r for r in latest if r.get("_sync_op") != "removed"], removed, through, replaced


def sync(conn, dry_run=False, reload=False):
    """Load what landed in the synced tables since the last load. Returns a report; a dry run changes nothing."""
    if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext('virtuwill_plaid_bronze')) AS ok").fetchone()["ok"]:
        return {"skipped": "Another load is running."}
    columns = _columns(conn)
    state = plaid_load.state(conn)
    since = {k: None if reload else state[f"{k}_through"] for k in ("balances", "transactions")}
    accounts, balances, transactions, removed, through, replaced = read(conn, since, columns)
    return plaid_load.apply(conn, accounts, balances, transactions, removed, through=through, dry_run=dry_run,
                            replaced=replaced,
                            overrides=json.loads(os.environ.get("PLAID_MASKS") or "{}"),
                            tz=os.environ.get("APP_TIMEZONE", "America/Chicago"))


def status(conn):
    state = plaid_load.state(conn)
    out = {"tables": {k: ".".join(v) for k, v in TABLES.items()},
           "loaded_through": {k: state[f"{k}_through"].isoformat() if state[f"{k}_through"] else None
                              for k in ("balances", "transactions")},
           "every_minutes": _minutes()}
    try:
        columns = _columns(conn)
    except SyncedTablesUnavailable as e:
        return out | {"available": False, "error": str(e)}
    for kind, (schema, name) in TABLES.items():
        at = _stamp(columns[kind])
        row = conn.execute(sql.SQL("""SELECT COUNT(*) AS rows, MAX({at}) AS newest,
                                             COUNT(*) FILTER (WHERE {at} > COALESCE(%s::timestamptz, '-infinity')) AS not_loaded
                                      FROM {t}""").format(at=at, t=sql.Identifier(schema, name)),
                           (state[f"{kind}_through"],)).fetchone()
        out[kind] = {"rows": row["rows"], "newest": row["newest"].isoformat() if row["newest"] else None,
                     "not_loaded": row["not_loaded"]}
    last = conn.execute("SELECT report, synced_at FROM virtuwill.sync_reports WHERE source = %s", (plaid_load.SOURCE,)).fetchone()
    return out | {"available": True, "last_load": {"at": last["synced_at"].isoformat(), **last["report"]} if last else None}


def run_once(dry_run=False, reload=False):
    """One load in its own transaction; a failure is recorded for Diagnostics and returned, not raised."""
    try:
        with db.tx() as conn:
            report = sync(conn, dry_run, reload)
            if dry_run:
                conn.rollback()
            return report, None
    except (SyncedTablesUnavailable, ExtractError) as error:
        failure = str(error)
    if not dry_run:
        with db.tx() as conn:
            plaid_load.record(conn, {"error": failure})
    return None, failure


# ── Automatic: a background loop in each worker (the advisory lock lets one load at a time) ──

_started = False


def _minutes():
    try:
        return max(0, float(os.environ.get("PLAID_SYNC_MINUTES", "0")))
    except ValueError:
        return 0


def _loop(minutes):
    time.sleep(30)                                  # let the app finish starting
    while True:
        try:
            report, error = run_once()
            if error:
                log.warning("Plaid sync: %s", error)
            elif report and not report.get("nothing_new") and not report.get("skipped"):
                log.info("Plaid sync: %s", {k: report.get(k) for k in ("accounts", "balances", "transactions", "removed")})
        except Exception:
            log.exception("Plaid sync failed")
        time.sleep(minutes * 60)


def start():
    """Start the loop once per process, when PLAID_SYNC_MINUTES is set and a database is attached."""
    global _started
    minutes = _minutes()
    if _started or not minutes or not db.configured():
        return
    _started = True
    threading.Thread(target=_loop, args=(minutes,), name="plaid-sync", daemon=True).start()


@bp.route("/api/v1/money/plaid-sync")
@admin_required
def plaid_sync_status():
    with db.tx() as conn:
        return jsonify(status(conn))


@bp.route("/api/v1/money/plaid-sync", methods=["POST"])
@admin_required
def plaid_sync_now():
    body = request.get_json(silent=True) or {}
    report, error = run_once(bool(body.get("dry_run")), bool(body.get("reload")))
    if error:
        return jsonify({"error": error}), 409
    return jsonify(report)
