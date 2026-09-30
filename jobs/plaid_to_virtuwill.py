"""Plaid → VirtuWill: a daily Databricks job that loads balances and transactions.

Each run asks Plaid only for what changed since the last run (/transactions/sync
with a saved cursor), maps it onto VirtuWill's finance model and posts it to
the app's ingest API (scripts/finance_feed.py does the sign-in). The cursor is
saved only after the app has taken the data, so a failed run is simply
retried by the next one. Balances come with the sync response: no paid
/accounts/balance/get calls unless --live-balances is given.

    python jobs/plaid_to_virtuwill.py              pull and load
    python jobs/plaid_to_virtuwill.py --dry-run    pull and show what would change; nothing saved
    python jobs/plaid_to_virtuwill.py --live-balances   also fetch real-time balances (billed per call by Plaid)

Settings, from environment variables or, in Databricks, the secret scope named
by VIRTUWILL_SECRET_SCOPE (default "virtuwill"; key = the name in lower case
with dashes, e.g. plaid-client-id):

    PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV (sandbox | production)
    PLAID_ACCESS_TOKENS   JSON {"Chase": "access-production-…", "Amex": "…"}; the names become institutions
    PLAID_STATE_PATH      where cursors are kept between runs (default /Volumes/workspace/virtuwill/plaid/state.json)
    PLAID_MASKS           optional JSON {"<plaid account_id>": "1234"} for accounts Plaid gives no four-digit mask
    VIRTUWILL_URL, VIRTUWILL_TOKEN, DATABRICKS_HOST, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET
                          as for scripts/finance_feed.py

Needs plaid-python (pip install plaid-python). The mapping (virtuwill/importers/plaid.py) needs nothing.
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CHUNK = 4000            # transactions per request; the API takes at most 5,000 records
SETTINGS = ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "PLAID_ACCESS_TOKENS", "PLAID_STATE_PATH", "PLAID_MASKS",
            "VIRTUWILL_URL", "VIRTUWILL_TOKEN", "DATABRICKS_HOST", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET",
            "DATABRICKS_SCOPE")

# ── Mapping Plaid onto VirtuWill's model: shared with the app's Lakebase mirror ─
# Loaded by path, so the job needs neither Flask nor the rest of the app.

_spec = importlib.util.spec_from_file_location("plaid_mapping", ROOT / "virtuwill" / "importers" / "plaid.py")
mapping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mapping)
bundle = mapping.bundle


def chunks(body):
    """The body split so no request carries more than CHUNK transactions; accounts and balances go with the first."""
    txns = body["transactions"]
    if len(txns) <= CHUNK:
        return [body]
    parts = []
    for i in range(0, len(txns), CHUNK):
        part = {**body, "transactions": txns[i:i + CHUNK]}
        if i:
            part["balances"] = []
        parts.append(part)
    return parts


# ── Settings, state and Plaid ────────────────────────────────────────────────

def load_settings():
    """Fill os.environ from the Databricks secret scope for anything not already set."""
    missing = [k for k in SETTINGS if not os.environ.get(k)]
    if not missing:
        return
    try:
        from pyspark.dbutils import DBUtils           # noqa: F401  (only on Databricks)
        from pyspark.sql import SparkSession
        dbutils = DBUtils(SparkSession.builder.getOrCreate())
    except Exception:
        return
    scope = os.environ.get("VIRTUWILL_SECRET_SCOPE", "virtuwill")
    for key in missing:
        try:
            os.environ[key] = dbutils.secrets.get(scope, key.lower().replace("_", "-"))
        except Exception:
            pass


def read_state(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"cursors": {}}


def write_state(path, state):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def plaid_client():
    import plaid
    from plaid.api import plaid_api
    env = os.environ.get("PLAID_ENV", "sandbox").lower()
    hosts = {"sandbox": plaid.Environment.Sandbox, "production": plaid.Environment.Production}
    if env not in hosts:
        sys.exit(f"PLAID_ENV must be sandbox or production (got {env!r})")
    return plaid_api.PlaidApi(plaid.ApiClient(plaid.Configuration(
        host=hosts[env], api_key={"clientId": os.environ["PLAID_CLIENT_ID"], "secret": os.environ["PLAID_SECRET"]})))


def _plain(obj):
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _request(module, name, **kw):
    """A plaid-python request object, or the plain arguments where the library isn't installed (tests)."""
    try:
        return getattr(__import__(f"plaid.model.{module}", fromlist=[name]), name)(**kw)
    except ImportError:
        return kw


def pull(client, access_token, cursor, live_balances=False):
    """Everything changed since cursor: (accounts, added + modified, removed ids, next cursor)."""
    changed, removed, accounts = [], [], {}
    while True:
        kw = {"access_token": access_token, "count": 500}
        if cursor:
            kw["cursor"] = cursor
        resp = _plain(client.transactions_sync(_request("transactions_sync_request", "TransactionsSyncRequest", **kw)))
        changed += [_plain(t) for t in resp.get("added", []) + resp.get("modified", [])]
        removed += [_plain(t)["transaction_id"] for t in resp.get("removed", [])]
        for a in resp.get("accounts") or []:
            accounts[_plain(a)["account_id"]] = _plain(a)
        cursor = resp["next_cursor"]
        if not resp["has_more"]:
            break
    if live_balances:
        resp = _plain(client.accounts_balance_get(_request("accounts_balance_get_request", "AccountsBalanceGetRequest",
                                                           access_token=access_token)))
        accounts = {a["account_id"]: a for a in map(_plain, resp["accounts"])}
    elif not accounts:
        resp = _plain(client.accounts_get(_request("accounts_get_request", "AccountsGetRequest", access_token=access_token)))
        accounts = {a["account_id"]: a for a in map(_plain, resp["accounts"])}
    return list(accounts.values()), changed, removed, cursor


def run(dry_run=False, live_balances=False, client=None, post=None):
    load_settings()
    tokens = json.loads(os.environ.get("PLAID_ACCESS_TOKENS") or "{}")
    if not tokens:
        sys.exit("Set PLAID_ACCESS_TOKENS to a JSON object of {institution: access_token}")
    overrides = json.loads(os.environ.get("PLAID_MASKS") or "{}")
    state_path = os.environ.get("PLAID_STATE_PATH") or "/Volumes/workspace/virtuwill/plaid/state.json"
    state = read_state(state_path)
    client = client or plaid_client()
    if post is None:
        from finance_feed import call
        post = lambda body: call("/api/ingest/v1/finance", body)       # noqa: E731
    today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Chicago"))).date().isoformat()
    summary, failed = [], False
    for institution, access_token in tokens.items():
        cursor = state["cursors"].get(institution)
        accounts, changed, removed, next_cursor = pull(client, access_token, cursor, live_balances)
        body, skipped = bundle(institution, accounts, changed, today, overrides)
        line = {"institution": institution, "accounts": len(body["accounts"]), "transactions": len(body["transactions"]),
                "removed_by_plaid": len(removed), "skipped": skipped}
        ok = True
        for part in chunks(body):
            if not (part["balances"] or part["transactions"]):
                continue
            status, reply = post({**part, "dry_run": True} if dry_run else part)
            if status >= 400:
                ok, failed = False, True
                line["error"] = reply.get("error") if isinstance(reply, dict) else str(reply)
                break
            report = reply.get("report") or (reply.get("preview") or {}).get("counts")
            line.setdefault("loaded", []).append(report)
        if ok and not dry_run:
            state["cursors"][institution] = next_cursor          # only once the app has everything
            write_state(state_path, state)
        summary.append(line)
    print(json.dumps(summary, indent=2, default=str))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="show what would change; load nothing, keep the cursors")
    parser.add_argument("--live-balances", action="store_true", help="real-time balances (a billed Plaid call per bank)")
    args = parser.parse_args(argv)
    return run(dry_run=args.dry_run, live_balances=args.live_balances)


if __name__ == "__main__":
    sys.exit(main())
