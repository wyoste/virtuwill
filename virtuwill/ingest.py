"""Ingest API: scheduled jobs (e.g. a Claude task) push balances and transactions.

Every route needs an API token (see api_tokens.py) and never uses the browser
session. Loads go through the same staging and matching as Money › Imports, so
they show up there too, labelled with the token that sent them.

    GET  /api/ingest/v1                    what this API takes (for the job to read)
    GET  /api/ingest/v1/finance/status     accounts, latest balances, last transaction per account, recent loads
    POST /api/ingest/v1/finance            JSON: balances and/or transactions (or a full finance bundle)
    POST /api/ingest/v1/finance/files      raw exports (PDF/CSV/JSON), as in Money › Imports

Sending the same thing twice changes nothing; overlapping runs are matched, by
the bank's transaction id (external_id) when given.
"""
import hashlib
import json
from datetime import date, datetime, timezone

from flask import Blueprint, g, jsonify, request

from . import db, media
from .api_tokens import token_required
from .importers import FORMAT, ExtractError, empty_bundle
from .importers import load as loader
from .importers.canonical import BALANCE_KINDS, KINDS
from .util import plain

bp = Blueprint("ingest", __name__)
MAX_JSON = 5 * 1024 * 1024
MAX_RECORDS = 5000

DESCRIPTION = {
    "about": "Push account balances and transactions into VirtuWill. Amounts: positive = money leaving the account "
             "(a purchase, a payment out); negative = money coming in (a refund, a deposit, a card payment received). "
             "Accounts are identified by their last four digits only.",
    "auth": "Send the token in the X-VirtuWill-Token header (or Authorization: Bearer <token>).",
    "endpoints": {
        "GET /api/ingest/v1/finance/status": "What's loaded: each account's latest balance and last transaction date. "
                                             "Send only what's newer, with a few days' overlap — overlap is matched, not doubled.",
        "POST /api/ingest/v1/finance": "JSON body described below. Add \"dry_run\": true to see what would change without loading.",
        "POST /api/ingest/v1/finance/files": "multipart/form-data with one or more 'files' (PDF statements, CSV exports, "
                                            ".finance.json). Same formats as Money › Imports.",
    },
    "body": {
        "source": "optional label for this feed, e.g. 'claude-daily' (shown in Money › Imports)",
        "accounts": "optional: [{mask: '1234', institution: 'Chase', name: 'Sapphire', account_type: 'credit_card'|'checking'|"
                    "'savings'|'retirement'|'loan'|'other'}] — accounts are created on first sight",
        "balances": "[{account_mask: '1234', as_of: 'YYYY-MM-DD', balance: 1234.56, kind: 'reported'|'available'}] — "
                    "for a card or loan, the amount owed as a positive number",
        "transactions": "[{account_mask: '1234', posted_on: 'YYYY-MM-DD', transacted_on?: 'YYYY-MM-DD', description: 'COFFEE SHOP', "
                        "amount: 4.5, kind?: 'expense'|'refund'|'card_payment'|'transfer'|'income', category?: 'Dining', "
                        "external_id?: 'the bank's transaction id', pending?: false}]",
        "dry_run": "optional, default false",
    },
    "limits": {"json_bytes": MAX_JSON, "records_per_request": MAX_RECORDS},
}


@bp.route("/api/ingest/v1")
@token_required("finance:read")
def describe():
    return jsonify(DESCRIPTION | {"token": g.api_token["name"], "scopes": g.api_token["scopes"]})


@bp.route("/api/ingest/v1/finance/status")
@token_required("finance:read")
def status():
    with db.tx() as conn:
        accounts = conn.execute("""
            SELECT a.mask, a.name, a.institution, a.account_type, a.is_active,
                   b.as_of AS balance_as_of, b.balance, b.balance_kind,
                   (SELECT MAX(posted_on) FROM finance.transactions t WHERE t.account_id = a.account_id) AS last_transaction_on,
                   (SELECT COUNT(*) FROM finance.transactions t WHERE t.account_id = a.account_id) AS transactions
            FROM finance.accounts a LEFT JOIN finance.latest_balances b USING (account_id)
            WHERE a.mask IS NOT NULL ORDER BY a.mask""").fetchall()
        loads = conn.execute("""SELECT import_id, filename, status, submitted_by, created_at, report
                                FROM finance.staged_imports WHERE submitted_by LIKE 'api:%%'
                                ORDER BY import_id DESC LIMIT 10""").fetchall()
        return jsonify({"accounts": [plain(r) for r in accounts], "recent_loads": [plain(r) for r in loads],
                        "today": date.today().isoformat()})


def _bundle_from(body):
    """The request as a finance bundle: a full bundle as sent, or the short form wrapped in one."""
    if body.get("format") == FORMAT:
        bundle = {k: v for k, v in body.items() if k != "dry_run"}
        bundle.setdefault("document", {})
    else:
        unknown = set(body) - {"source", "accounts", "balances", "transactions", "dry_run"}
        if unknown:
            raise ExtractError(f"Unknown fields: {', '.join(sorted(unknown))}. See GET /api/ingest/v1.")
        bundle = empty_bundle({})
        for section in ("accounts", "balances", "transactions"):
            rows = body.get(section) or []
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ExtractError(f"'{section}' must be a list of objects")
            bundle[section] = rows
        # Every account a balance or transaction names exists, even if 'accounts' didn't list it.
        listed = {str(a.get("mask")) for a in bundle["accounts"]}
        for r in bundle["balances"] + bundle["transactions"]:
            mask = str(r.get("account_mask") or "")
            if mask and mask not in listed:
                bundle["accounts"].append({"mask": mask, "institution": "", "name": f"Account {mask}", "account_type": None})
                listed.add(mask)
        for b in bundle["balances"]:
            b.setdefault("kind", "reported")
            if b["kind"] not in BALANCE_KINDS - {"statement_opening", "statement_closing"}:
                raise ExtractError("Balance kind must be 'reported' or 'available' (statement balances come with statements)")
        for t in bundle["transactions"]:
            if t.get("kind", "expense") not in KINDS:
                raise ExtractError(f"Transaction kind must be one of {', '.join(sorted(KINDS))}")
    records = sum(len(bundle.get(s) or []) for s in ("balances", "transactions", "statements", "receipts", "paychecks"))
    if records == 0:
        raise ExtractError("Nothing to load: send balances and/or transactions")
    if records > MAX_RECORDS:
        raise ExtractError(f"Too many records in one request ({records}); send at most {MAX_RECORDS}")
    # The same content always hashes the same, so a repeated push is recognised and changes nothing.
    content = json.dumps({s: bundle.get(s) or [] for s in ("accounts", "balances", "transactions", "statements", "receipts", "paychecks")},
                         sort_keys=True, default=str).encode()
    source = str(body.get("source") or bundle["document"].get("filename") or "api").strip()[:60] or "api"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bundle["document"] = {"filename": f"API · {source} · {stamp}", "sha256": hashlib.sha256(content).hexdigest(),
                          "doc_type": "transaction_export", "file_format": "other", "institution": "",
                          "parser": "api", **{k: v for k, v in bundle["document"].items() if k in ("period_start", "period_end", "checks")}}
    return bundle


def _load(bundle, dry_run, raw_asset_ids=()):
    submitted_by = f"api:{g.api_token['name']}"
    with db.tx() as conn:
        if dry_run:
            return {"dry_run": True, "preview": loader.preview(conn, bundle)}
        import_id, preview = loader.stage(conn, bundle, raw_asset_ids, submitted_by=submitted_by)
        report = loader.commit(conn, import_id)
        return {"import_id": import_id, "preview": preview, "report": report}


@bp.route("/api/ingest/v1/finance", methods=["POST"])
@token_required("finance:write")
def push_finance():
    if (request.content_length or 0) > MAX_JSON:
        return jsonify({"error": f"The body is larger than {MAX_JSON // (1024 * 1024)} MB; split it"}), 413
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Send a JSON object. See GET /api/ingest/v1 for the shape."}), 400
    try:
        bundle = _bundle_from(body)
        result = _load(bundle, bool(body.get("dry_run")))
    except ExtractError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result), 200 if result.get("dry_run") or result["report"].get("skipped") else 201


@bp.route("/api/ingest/v1/finance/files", methods=["POST"])
@token_required("finance:write")
def push_files():
    from .money_imports import _bundle, _uploaded
    try:
        files = _uploaded()
        bundle = _bundle(files)
    except ExtractError as e:
        return jsonify({"error": str(e)}), 400
    dry_run = request.form.get("dry_run", "").lower() in ("1", "true", "yes")
    assets = []
    if not dry_run:
        with db.tx() as conn:
            sha = bundle["document"]["sha256"][:16]
            assets = [media.register(conn, f"private/finance/{sha}/{name}", content, visibility="private") for name, content in files]
    try:
        result = _load(bundle, dry_run, assets)
    except ExtractError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result), 200 if result.get("dry_run") or result["report"].get("skipped") else 201
