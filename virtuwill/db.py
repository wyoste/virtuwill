"""Database access: one connection pool to Lakebase (PostgreSQL).

On first use the pool brings the schema up to date and runs pending one-time
data migrations, all in one transaction under an advisory lock, so several
workers starting together never race.

db/schema/*.sql is a versioned sequence: each file runs once, in name order,
and is recorded with its checksum in virtuwill.schema_versions. A file that
has run is never edited; a change to the model is a new, higher-numbered
file. Editing an applied file is reported (logged and shown in diagnostics),
not silently re-run. Connection settings come from the PG*
variables Databricks sets when a database resource is attached to the app;
the password is the app's OAuth token (or PGPASSWORD for local development).
"""
import hashlib
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "db" / "schema"


class DatabaseUnavailable(Exception):
    """No database is configured, or it could not be reached."""


def configured():
    return bool(os.environ.get("PGHOST"))


def _password():
    if os.environ.get("PGPASSWORD"):
        return os.environ["PGPASSWORD"]
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient().config.oauth_token().access_token


_pool = None
_lock = threading.Lock()


def _open_pool():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    class TokenConnection(psycopg.Connection):
        # OAuth tokens expire hourly: fetch one for every new connection.
        @classmethod
        def connect(cls, conninfo="", **kwargs):
            kwargs["password"] = _password()
            return super().connect(conninfo, **kwargs)

    kwargs = {"row_factory": dict_row, "connect_timeout": 10, "application_name": "virtuwill"}
    if not os.environ.get("PGSSLMODE"):
        kwargs["sslmode"] = "require"
    if not os.environ.get("PGUSER") and not os.environ.get("PGPASSWORD"):
        from databricks.sdk import WorkspaceClient
        kwargs["user"] = WorkspaceClient().config.client_id

    def configure(conn):
        # "Today" and "this week" follow the owner's calendar.
        conn.execute("SELECT set_config('TimeZone', %s, false)", (os.environ.get("APP_TIMEZONE", "UTC"),))
        conn.commit()

    return ConnectionPool(connection_class=TokenConnection, kwargs=kwargs, configure=configure, min_size=0,
                          max_size=int(os.environ.get("DB_POOL_SIZE", "4")), max_lifetime=30 * 60,
                          max_idle=5 * 60, timeout=15, open=True)


def _pool_ready():
    global _pool
    with _lock:
        if _pool is None:
            if not configured():
                raise DatabaseUnavailable("No database is attached to the app (PGHOST is not set).")
            pool = _open_pool()
            try:
                with pool.connection() as conn:
                    bootstrap(conn)
            except Exception:
                pool.close()
                raise
            _pool = pool
        return _pool


def schema_order(name):
    """Files apply by their number (so 100_… follows 99_…, not 10_…), then by name."""
    prefix = name.split("_", 1)[0]
    return (int(prefix) if prefix.isdigit() else 10 ** 9, name)


def schema_files():
    return sorted(SCHEMA_DIR.glob("*.sql"), key=lambda p: schema_order(p.name))


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bootstrap(conn):
    """Bring the schema and data up to date. Safe to run on every start."""
    from . import migrate
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('virtuwill_schema'))")
    migrate.set_aside_legacy_schemas(conn)
    conn.execute("""CREATE SCHEMA IF NOT EXISTS virtuwill;
                    CREATE TABLE IF NOT EXISTS virtuwill.schema_versions (
                        version TEXT PRIMARY KEY,
                        checksum TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    applied = {r["version"]: r["checksum"] for r in conn.execute("SELECT version, checksum FROM virtuwill.schema_versions")}
    for path in schema_files():
        digest = checksum(path)
        if path.name not in applied:
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO virtuwill.schema_versions (version, checksum) VALUES (%s, %s)", (path.name, digest))
            log.info("Applied schema %s", path.name)
        elif applied[path.name] != digest:
            log.error("Schema file %s changed after it was applied; put changes in a new file", path.name)
    migrate.run_pending(conn)


def schema_status(conn):
    """Applied versions, pending files and edited files, for diagnostics."""
    rows = conn.execute("SELECT * FROM virtuwill.schema_versions").fetchall()
    applied = {r["version"]: r for r in sorted(rows, key=lambda r: schema_order(r["version"]))}
    files = {p.name: checksum(p) for p in schema_files()}
    return {"version": max(applied, key=schema_order) if applied else None,
            "applied": [{"version": v, "appliedAt": r["applied_at"].isoformat()} for v, r in applied.items()],
            "pending": sorted(set(files) - set(applied), key=schema_order),
            "edited": sorted(v for v in applied if v in files and files[v] != applied[v]["checksum"])}


def reset():
    """Close the pool (tests, or after changing connection settings)."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def tx():
    """A connection in a transaction: committed on success, rolled back on error."""
    import psycopg
    try:
        pool = _pool_ready()
        with pool.connection() as conn:
            yield conn
    except DatabaseUnavailable:
        raise
    except (psycopg.OperationalError, TimeoutError) as error:
        log.exception("Database unavailable")
        raise DatabaseUnavailable(str(error)) from error
    except Exception as error:
        if type(error).__name__ == "PoolTimeout":
            raise DatabaseUnavailable(str(error)) from error
        raise


def all(sql, *params):
    with tx() as conn:
        return conn.execute(sql, params).fetchall()


def one(sql, *params):
    with tx() as conn:
        return conn.execute(sql, params).fetchone()


def run(sql, *params):
    with tx() as conn:
        return conn.execute(sql, params).rowcount


def jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)
