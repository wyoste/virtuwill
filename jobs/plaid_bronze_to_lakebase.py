"""Plaid bronze → VirtuWill's Lakebase: a Databricks job task that loads the day's pull into the finance tables.

Runs after the plaid_integration notebook in the ingestion_plaid_financials job.
It reads prod.bronze.raw_plaid_balances and raw_plaid_transactions with Spark,
takes only what Auto Loader landed since the last run, and writes it straight
into the app's Lakebase database (finance.accounts, finance.transactions,
finance.balance_snapshots) through the app's own matching
(virtuwill/importers/plaid_load.py). The whole load is one transaction, and how
far bronze has been read is saved with it, so a failed run is simply picked up
by the next one.

It signs in to Lakebase as whoever the job runs as, with a short-lived token
from the Databricks SDK: no secrets to keep.

    python jobs/plaid_bronze_to_lakebase.py --instance virtuwill-db       Lakebase Provisioned
    python jobs/plaid_bronze_to_lakebase.py --endpoint projects/…/endpoints/…   Lakebase Autoscaling
    … --dry-run       what would change; nothing written, nothing marked as read
    … --reload        read all of bronze again (loads are matched, so nothing doubles)

Other options: --catalog prod, --schema bronze, --database databricks_postgres,
--timezone America/Chicago, --masks '{"<plaid account_id>": "1234"}' for accounts
Plaid gives no four-digit mask. Needs psycopg (pip install "psycopg[binary]").
See docs/plaid-lakebase.md.
"""
import argparse
import json
import sys
import types
import uuid
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPOCH = "1970-01-01 00:00:00"


def importers():
    """virtuwill.importers without the rest of the app (no Flask on the cluster)."""
    if "virtuwill" not in sys.modules:
        package = types.ModuleType("virtuwill")
        package.__path__ = [str(ROOT / "virtuwill")]
        sys.modules["virtuwill"] = package
    from virtuwill.importers import plaid_load
    return plaid_load


# ── Reading bronze (Spark) ───────────────────────────────────────────────────

def _stamp(value):
    """A Spark timestamp (read with the session in UTC) as an aware datetime, and back as SQL text."""
    return value.replace(tzinfo=timezone.utc) if value else None


def _since(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f") if value else EPOCH


def read_bronze(spark, catalog, schema, since):
    """What landed since the last load: (accounts, balances, transactions, removed ids, through)."""
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    bal, txn = f"{catalog}.{schema}.raw_plaid_balances", f"{catalog}.{schema}.raw_plaid_transactions"
    rows = lambda sql, **args: [r.asDict() for r in spark.sql(sql, args=args).collect()]      # noqa: E731
    columns = """account_id, item_label, institution_id, account_name, official_name, mask,
                 NULLIF(type, 'None') AS type, NULLIF(subtype, 'None') AS subtype,
                 current, available, limit_amt, iso_currency_code, _pulled_at"""
    # How far bronze goes now; rows landing while this runs wait for the next run.
    through = {"balances": _stamp(spark.sql(f"SELECT MAX(_ingested_at) AS t FROM {bal}").first()["t"]),
               "transactions": _stamp(spark.sql(f"SELECT MAX(_ingested_at) AS t FROM {txn}").first()["t"])}
    window = "_ingested_at > CAST(:since AS TIMESTAMP) AND _ingested_at <= CAST(:until AS TIMESTAMP)"
    # Every account, from its latest pull: masks and names for transactions whose balances didn't change.
    accounts = rows(f"""SELECT {columns} FROM {bal} WHERE account_id IS NOT NULL
                        QUALIFY ROW_NUMBER() OVER (PARTITION BY account_id
                                                   ORDER BY CAST(_pulled_at AS TIMESTAMP) DESC, _ingested_at DESC) = 1""")
    balances = rows(f"""SELECT {columns} FROM {bal}
                        WHERE account_id IS NOT NULL AND {window}""",
                    since=_since(since["balances"]), until=_since(through["balances"]))
    # Each transaction touched since the last load, at its latest sync (added, modified or removed).
    latest = rows(f"""SELECT * FROM {txn}
                      WHERE transaction_id IN (SELECT transaction_id FROM {txn} WHERE {window})
                        AND _ingested_at <= CAST(:until AS TIMESTAMP)
                      QUALIFY ROW_NUMBER() OVER (PARTITION BY transaction_id
                                                 ORDER BY CAST(_pulled_at AS TIMESTAMP) DESC, _ingested_at DESC) = 1""",
                  since=_since(since["transactions"]), until=_since(through["transactions"]))
    removed = {r["transaction_id"] for r in latest if r.get("_sync_op") == "removed"}
    transactions = [r for r in latest if r.get("_sync_op") != "removed"]
    return accounts, balances, transactions, removed, through


# ── Lakebase ─────────────────────────────────────────────────────────────────

def connect(w, instance=None, endpoint=None, database="databricks_postgres", host=None, user=None):
    """A connection to the app's Lakebase database, as the identity this job runs as."""
    import psycopg
    from psycopg.rows import dict_row
    if endpoint:                                    # Autoscaling: projects/<p>/branches/<b>/endpoints/<e>
        host = host or w.postgres.get_endpoint(endpoint).status.hosts.host
        password = w.postgres.generate_database_credential(endpoint=endpoint).token
    elif instance:                                  # Provisioned
        host = host or w.database.get_database_instance(instance).read_write_dns
        password = w.database.generate_database_credential(request_id=str(uuid.uuid4()), instance_names=[instance]).token
    else:
        raise SystemExit("Name the Lakebase database: --instance <name> (Provisioned) or --endpoint <name> (Autoscaling)")
    return psycopg.connect(host=host, dbname=database, user=user or w.current_user.me().user_name, password=password,
                           sslmode="require", row_factory=dict_row, application_name="plaid_bronze_to_lakebase",
                           connect_timeout=30, autocommit=True)


def run(spark, conn, catalog="prod", schema="bronze", tz="America/Chicago", overrides=None, dry_run=False, reload=False):
    """One load in one transaction (conn in autocommit mode); a dry run rolls it back."""
    import psycopg
    plaid_load = importers()
    if not conn.execute("SELECT to_regclass('finance.plaid_bronze_load') IS NOT NULL AS ok").fetchone()["ok"]:
        raise SystemExit("finance.plaid_bronze_load isn't in this database yet: deploy the app from this branch first "
                         "(it adds the table on start), then run again.")
    with conn.transaction() as tx:
        state = plaid_load.state(conn)
        since = {"balances": None if reload else state["balances_through"],
                 "transactions": None if reload else state["transactions_through"]}
        accounts, balances, transactions, removed, through = read_bronze(spark, catalog, schema, since)
        report = plaid_load.apply(conn, accounts, balances, transactions, removed, through=through,
                                  overrides=overrides, tz=tz, dry_run=dry_run)
        if dry_run:
            raise psycopg.Rollback(tx)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--instance", help="Lakebase Provisioned: the database instance's name")
    parser.add_argument("--endpoint", help="Lakebase Autoscaling: the endpoint's full name")
    parser.add_argument("--database", default="databricks_postgres")
    parser.add_argument("--host", help="optional: the Postgres host, instead of looking it up")
    parser.add_argument("--user", help="optional: the Postgres role, instead of the job's own identity")
    parser.add_argument("--catalog", default="prod")
    parser.add_argument("--schema", default="bronze")
    parser.add_argument("--timezone", default="America/Chicago", help="the app's time zone: which day a balance belongs to")
    parser.add_argument("--masks", default="{}", help='JSON {"<plaid account_id>": "1234"} for accounts with no four-digit mask')
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reload", action="store_true", help="read all of bronze, not just what's new")
    args = parser.parse_args(argv)

    from databricks.sdk import WorkspaceClient
    from pyspark.sql import SparkSession
    import psycopg
    spark = SparkSession.builder.getOrCreate()
    w = WorkspaceClient()
    with connect(w, args.instance, args.endpoint, args.database, args.host, args.user) as conn:
        try:
            report = run(spark, conn, args.catalog, args.schema, args.timezone, json.loads(args.masks), args.dry_run, args.reload)
        except psycopg.errors.InsufficientPrivilege as error:
            raise SystemExit(f"Lakebase refused the write: {error}\nThe role this job signs in as needs to read and write the "
                             "finance tables; see 'Permissions' in docs/plaid-lakebase.md.") from None
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
