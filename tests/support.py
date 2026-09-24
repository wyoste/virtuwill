"""Test helpers: every test runs against a real PostgreSQL database.

Set VIRTUWILL_TEST_PG to a libpq connection string for a scratch database,
e.g. "host=localhost port=5432 dbname=scratch user=app password=pw sslmode=disable".
Its app schemas are dropped and rebuilt by the tests. Without it, only the
tests that need no database run.
"""
import os
import unittest

PG = os.environ.get("VIRTUWILL_TEST_PG")
SCHEMAS = ("core", "journal", "health", "finance", "garden", "music", "content", "career", "travel", "virtuwill",
           "legacy_journal_v0", "legacy_health_v0")

# Strong credentials, so the private trackers are enabled.
os.environ.setdefault("SECRET_KEY", "test-secret-" * 4)
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")


def pg_env():
    names = {"dbname": "PGDATABASE"}
    return {names.get(k, "PG" + k.upper()): v for k, v in (part.split("=", 1) for part in PG.split())}


def drop_schemas():
    import psycopg
    with psycopg.connect(PG, autocommit=True) as conn:
        for schema in SCHEMAS:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def fresh_database(bootstrap=True):
    """An empty database; with bootstrap, the current schema and seed data."""
    if not PG:
        raise unittest.SkipTest("Set VIRTUWILL_TEST_PG to run the database tests.")
    os.environ.update(pg_env())
    from virtuwill import db
    db.reset()
    drop_schemas()
    if bootstrap:
        with db.tx():
            pass


needs_database = unittest.skipUnless(PG, "Set VIRTUWILL_TEST_PG to run the database tests.")


def admin_client(app, journal=False):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        if journal:
            session["journal_unlocked"] = True
    return client
