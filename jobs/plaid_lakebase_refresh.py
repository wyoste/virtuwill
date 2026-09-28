"""Plaid → lakehouse → Lakebase → VirtuWill: the steps after the daily Plaid pull.

Runs as the second task of the ingestion_plaid_financials job, after the
plaid_integration notebook has landed the pull in prod.bronze.raw_plaid_*:

  1. serving tables: make prod.silver.plaid_transactions / plaid_balances if they
     don't exist (lakehouse/plaid_serving_create.sql), then merge in what bronze
     has that they don't (plaid_serving_refresh.sql);
  2. synced tables: refresh each Lakebase synced table (triggered mode) and wait
     for it to finish;
  3. the app: ask VirtuWill to load what changed (POST /api/ingest/v1/finance/plaid-mirror).

    python jobs/plaid_lakebase_refresh.py              all three steps
    python jobs/plaid_lakebase_refresh.py --dry-run    steps 1-2, then ask the app what it would load; nothing loaded
    python jobs/plaid_lakebase_refresh.py --only app   one step: serving, sync or app

Settings, from environment variables or, in Databricks, a secret scope: --secret-scope,
else VIRTUWILL_SECRET_SCOPE, else "plaid_integration" (the notebook's scope). Keys are
the names in lower case, with underscores or dashes (virtuwill_token or virtuwill-token):

    PLAID_SYNCED_TABLES     comma-separated synced table names, as Unity Catalog shows them,
                            e.g. virtuwill_db.public.plaid_transactions,virtuwill_db.public.plaid_balances
    PLAID_SYNC_PIPELINE_IDS optional: the synced tables' pipeline ids, instead of looking them up
    VIRTUWILL_URL, VIRTUWILL_TOKEN, DATABRICKS_HOST, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET
                            as for scripts/finance_feed.py (the token needs finance:write)

Step 1 needs Spark (a Databricks notebook or job cluster), step 2 databricks-sdk,
step 3 only the standard library. See docs/plaid-lakebase.md.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

SQL_DIR = ROOT / "lakehouse"
SERVING_TABLES = ("prod.silver.plaid_transactions", "prod.silver.plaid_balances")
SETTINGS = ("PLAID_SYNCED_TABLES", "PLAID_SYNC_PIPELINE_IDS", "VIRTUWILL_URL", "VIRTUWILL_TOKEN", "DATABRICKS_HOST",
            "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_SCOPE")
SYNC_TIMEOUT = 45 * 60
DONE = {"COMPLETED", "FAILED", "CANCELED"}


def load_settings(scope=None):
    """Fill os.environ from a Databricks secret scope for anything not already set.
    Keys are the names in lower case, with dashes (virtuwill-token) or underscores (virtuwill_token)."""
    missing = [k for k in SETTINGS if not os.environ.get(k)]
    if not missing:
        return
    try:
        from pyspark.dbutils import DBUtils           # noqa: F401  (only on Databricks)
        from pyspark.sql import SparkSession
        dbutils = DBUtils(SparkSession.builder.getOrCreate())
    except Exception:
        return
    scope = scope or os.environ.get("VIRTUWILL_SECRET_SCOPE", "plaid_integration")
    for key in missing:
        for name in (key.lower().replace("_", "-"), key.lower()):
            try:
                os.environ[key] = dbutils.secrets.get(scope, name)
                break
            except Exception:
                pass


def statements(text):
    """The SQL statements in a file: comments dropped, split on the semicolon that ends a line."""
    text = re.sub(r"--[^\n]*", "", text)
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", text) if s.strip()]


# ── 1. Serving tables ────────────────────────────────────────────────────────

def serving(spark):
    """Make the tables if they're missing (idempotent), then merge in what bronze has that they don't."""
    for name in ("plaid_serving_create.sql", "plaid_serving_refresh.sql"):
        for sql in statements((SQL_DIR / name).read_text()):
            spark.sql(sql)
    return {"rows": {t: spark.table(t).count() for t in SERVING_TABLES}}


# ── 2. Synced tables ─────────────────────────────────────────────────────────

def pipeline_id(w, name):
    """The pipeline behind a synced table: Lakebase Autoscaling (postgres API), then Provisioned (database API)."""
    for lookup in (lambda: w.postgres.get_synced_table(name).status.pipeline_id,
                   lambda: w.postgres.get_synced_table(f"synced_tables/{name}").status.pipeline_id,
                   lambda: w.database.get_synced_database_table(name).data_synchronization_status.pipeline_id):
        try:
            found = lookup()
        except Exception:
            continue
        if found:
            return found
    raise SystemExit(f"Can't find the pipeline for synced table {name}. Check the name (as Unity Catalog shows it), "
                     "or set PLAID_SYNC_PIPELINE_IDS to the pipeline ids shown on each synced table's page.")


def refresh(w, pipeline_ids, sleep=time.sleep, timeout=SYNC_TIMEOUT):
    """Start one update per pipeline, then wait for all of them. Returns {pipeline_id: final state}."""
    updates = {pid: w.pipelines.start_update(pid).update_id for pid in pipeline_ids}
    states, waited = {}, 0
    while len(states) < len(updates):
        for pid, update_id in updates.items():
            if pid in states:
                continue
            state = w.pipelines.get_update(pid, update_id).update.state
            state = getattr(state, "value", state)
            if state in DONE:
                states[pid] = state
        if len(states) < len(updates):
            if waited >= timeout:
                states.update({pid: "TIMED_OUT" for pid in updates if pid not in states})
                break
            sleep(15)
            waited += 15
    return states


def sync_tables(w=None):
    if w is None:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
    ids = [p.strip() for p in os.environ.get("PLAID_SYNC_PIPELINE_IDS", "").split(",") if p.strip()]
    if not ids:
        names = [n.strip() for n in os.environ.get("PLAID_SYNCED_TABLES", "").split(",") if n.strip()]
        if not names:
            raise SystemExit("Set PLAID_SYNCED_TABLES (or PLAID_SYNC_PIPELINE_IDS)")
        ids = [pipeline_id(w, n) for n in names]
    return refresh(w, ids)


# ── 3. The app ───────────────────────────────────────────────────────────────

def tell_app(dry_run=False, post=None):
    if post is None:
        from finance_feed import call
        post = lambda body: call("/api/ingest/v1/finance/plaid-mirror", body)       # noqa: E731
    return post({"dry_run": dry_run})


def run(only=None, dry_run=False, spark=None, workspace=None, post=None, secret_scope=None):
    load_settings(secret_scope)
    summary, failed = {}, False
    if only in (None, "serving"):
        if spark is None:
            from pyspark.sql import SparkSession
            spark = SparkSession.builder.getOrCreate()
        summary["serving"] = serving(spark)
    if only in (None, "sync"):
        states = sync_tables(workspace)
        summary["sync"] = states
        if any(s != "COMPLETED" for s in states.values()):
            failed = True
            summary["error"] = "A synced table didn't refresh; the app was not asked to load."
    if only in (None, "app") and not failed:
        status, reply = tell_app(dry_run, post)
        summary["app"] = reply
        failed = status >= 400
    print(json.dumps(summary, indent=2, default=str))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="ask the app what it would load; load nothing")
    parser.add_argument("--only", choices=("serving", "sync", "app"), help="run one step")
    parser.add_argument("--secret-scope", help="where the settings are kept (default: VIRTUWILL_SECRET_SCOPE, else plaid_integration)")
    args = parser.parse_args(argv)
    return run(only=args.only, dry_run=args.dry_run, secret_scope=args.secret_scope)


if __name__ == "__main__":
    sys.exit(main())
