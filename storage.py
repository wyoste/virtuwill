"""Unified persistence for VirtuWill.

One data model for every feature, stored in a single Postgres schema when a
Lakebase database is attached to the Databricks app (PGHOST is set), or in
local files for development:

  journal_entries  one row per journal entry
  collections  one JSON document per remaining feature (garden, music
               catalog, blog, messages, portfolio uploads, garden photos,
               accounts template, travel, page settings)
  media        uploaded files (audio, photos, thumbnails, portfolio HTML),
               keyed by their path under static/
  trackers     private Finance and Health tracker documents and state

A collection missing from Lakebase is read from its bundled JSON file, so the
first deployment starts from the data in the repository. The first write
stores it in Lakebase; from then on the database is the source of truth.
"""
import json
import logging
import mimetypes
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
MOCK_DIR = ROOT / "mock_data"
STATIC_DIR = ROOT / "static"
SCHEMA = "virtuwill"
log = logging.getLogger(__name__)

# name → (file under data/, bundled fallback under mock_data/ or None, default)
COLLECTIONS = {
    "journal_entries":   ("journal_entries.json", "journal_entries.json", []),
    "garden":            ("garden.json", "garden.json", {"beds": []}),
    "garden_photos":     ("garden_photos.json", "garden_photos.json", []),
    "music_catalog":     ("music_catalog.json", None, {"tracks": []}),
    "messages":          ("messages.json", None, []),
    "portfolio_uploads": ("portfolio_uploads.json", None, []),
    "blog":              ("blog.json", None, []),
    "accounts_template": ("accounts_template.json", "accounts_template.json", []),
    # Formerly browser-only (localStorage); values are the stored strings.
    "travel_pins":         ("travel_pins.json", None, None),
    "travel_visited":      ("travel_visited.json", None, None),
    "garden_gallery_note": ("garden_gallery_note.json", None, None),
    "garden_gallery_hero": ("garden_gallery_hero.json", None, None),
    "portfolio_layout":    ("portfolio_layout.json", None, None),
}


class AlreadyInstalled(Exception):
    """A tracker document exists; installs never replace live data."""


class StorageUnavailable(Exception):
    """The configured database could not be reached."""


def _read_file(name, use_mock=True):
    filename, mock, default = COLLECTIONS[name]
    for path in [DATA_DIR / filename] + ([MOCK_DIR / mock] if mock and use_mock else []):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Could not read %s", path)
    return default


def _static_path(relpath):
    path = (STATIC_DIR / relpath).resolve()
    if STATIC_DIR.resolve() not in path.parents:
        raise ValueError("Media path must be inside static/")
    return path


# ── Journal entries ───────────────────────────────────────────────────────────
# The API shape (camelCase, meals as {B, L, D}) is unchanged; Lakebase stores
# each entry as a row.

def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _entry_date(entry):
    try:
        return date.fromisoformat(str(entry.get("date")))
    except ValueError:
        return _timestamp(entry.get("createdAt")).date()


def _journal_params(entry):
    meals = entry.get("meals") or {}
    return {
        "id": str(entry["id"]),
        "entry_date": _entry_date(entry),
        "quote": entry.get("quote") or "",
        "quote_author": entry.get("quoteAuthor") or "",
        "breakfast": meals.get("B") or "",
        "lunch": meals.get("L") or "",
        "dinner": meals.get("D") or "",
        "free_write": entry.get("freeWrite") or "",
        "habits": entry.get("habits") or {},
        "tags": entry.get("tags") or [],
        "accounts": entry.get("accounts") or [],
        "source": entry.get("source") or "manual",
        "created_at": _timestamp(entry.get("createdAt")),
    }


def _journal_entry(row):
    return {
        "id": row["id"],
        "date": row["entry_date"].isoformat(),
        "quote": row["quote"],
        "quoteAuthor": row["quote_author"],
        "meals": {"B": row["breakfast"], "L": row["lunch"], "D": row["dinner"]},
        "freeWrite": row["free_write"],
        "habits": row["habits"],
        "tags": row["tags"],
        "accounts": row["accounts"],
        "source": row["source"],
        "createdAt": row["created_at"].astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


# ── Local files (development) ─────────────────────────────────────────────────

class LocalBackend:
    name = "local"

    def __init__(self, tracker_dir=None):
        self.tracker_dir = Path(tracker_dir or os.environ.get("TRACKER_DATA_DIR", DATA_DIR / "private-trackers"))

    def load(self, name):
        return _read_file(name)

    def save(self, name, data):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / COLLECTIONS[name][0]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def journal_list(self):
        return self.load("journal_entries")

    def journal_upsert(self, entry):
        entries = self.load("journal_entries")
        idx = next((i for i, e in enumerate(entries) if e.get("id") == entry["id"]), None)
        if idx is not None:
            entries[idx] = entry
        else:
            entries.insert(0, entry)
            entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        self.save("journal_entries", entries)
        return idx is None

    def journal_delete(self, entry_id):
        entries = self.load("journal_entries")
        remaining = [e for e in entries if e.get("id") != entry_id]
        if len(remaining) == len(entries):
            return False
        self.save("journal_entries", remaining)
        return True

    def journal_date_exists(self, day):
        return any(e.get("date") == day for e in self.load("journal_entries"))

    def put_media(self, relpath, content, content_type):
        pass  # the file on disk is the only copy

    def delete_media(self, relpath):
        pass

    def get_media(self, relpath):
        return None

    def media_paths(self):
        return []

    @contextmanager
    def trackers(self):
        self.tracker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.tracker_dir / "trackers.sqlite3"
        db = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS trackers (kind TEXT PRIMARY KEY, document TEXT NOT NULL, state TEXT, revision INTEGER NOT NULL DEFAULT 0)")
                yield _SqliteTrackers(db)
        finally:
            db.close()


class _SqliteTrackers:
    def __init__(self, db):
        self.db = db

    def get(self, kind):
        row = self.db.execute("SELECT kind, document, state, revision FROM trackers WHERE kind = ?", (kind,)).fetchone()
        return dict(row) if row else None

    def insert(self, kind, document):
        try:
            self.db.execute("INSERT INTO trackers(kind, document) VALUES (?, ?)", (kind, document))
        except sqlite3.IntegrityError:
            raise AlreadyInstalled() from None

    def update(self, kind, encoded, revision):
        result = self.db.execute("UPDATE trackers SET state = ?, revision = revision + 1 WHERE kind = ? AND revision = ?", (encoded, kind, revision))
        return result.rowcount == 1


# ── Lakebase (Databricks-managed Postgres) ────────────────────────────────────

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.collections (
    name TEXT PRIMARY KEY,
    data JSONB,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.media (
    path TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    content BYTEA NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.journal_entries (
    id TEXT PRIMARY KEY,
    entry_date DATE NOT NULL,
    quote TEXT NOT NULL DEFAULT '',
    quote_author TEXT NOT NULL DEFAULT '',
    breakfast TEXT NOT NULL DEFAULT '',
    lunch TEXT NOT NULL DEFAULT '',
    dinner TEXT NOT NULL DEFAULT '',
    free_write TEXT NOT NULL DEFAULT '',
    habits JSONB NOT NULL DEFAULT '{{}}',
    tags JSONB NOT NULL DEFAULT '[]',
    accounts JSONB NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS journal_entries_by_date ON {SCHEMA}.journal_entries (entry_date DESC, created_at DESC);
CREATE TABLE IF NOT EXISTS {SCHEMA}.migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.trackers (
    kind TEXT PRIMARY KEY,
    document TEXT NOT NULL,
    state TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _lakebase_password():
    """Databricks Apps authenticate to Lakebase with the app's OAuth token."""
    if os.environ.get("PGPASSWORD"):
        return os.environ["PGPASSWORD"]
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient().config.oauth_token().access_token


class LakebaseBackend:
    name = "lakebase"

    def __init__(self):
        self._pool = None
        self._lock = threading.Lock()

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                import psycopg
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool

                class TokenConnection(psycopg.Connection):
                    # OAuth tokens expire hourly: fetch one for every new connection.
                    @classmethod
                    def connect(cls, conninfo="", **kwargs):
                        kwargs["password"] = _lakebase_password()
                        return super().connect(conninfo, **kwargs)

                # Host, port, database and user come from the PG* variables
                # Databricks sets when a database resource is attached.
                kwargs = {"row_factory": dict_row, "connect_timeout": 10, "application_name": "virtuwill"}
                if not os.environ.get("PGSSLMODE"):
                    kwargs["sslmode"] = "require"
                if not os.environ.get("PGUSER") and not os.environ.get("PGPASSWORD"):
                    from databricks.sdk import WorkspaceClient
                    kwargs["user"] = WorkspaceClient().config.client_id
                pool = ConnectionPool(connection_class=TokenConnection, kwargs=kwargs, min_size=0, max_size=4,
                                      max_lifetime=30 * 60, max_idle=5 * 60, timeout=15, open=True)
                try:
                    with pool.connection() as db:
                        db.execute(DDL)
                        self._migrate(db)
                except Exception:
                    pool.close()
                    raise
                self._pool = pool
            return self._pool

    @contextmanager
    def connect(self):
        import psycopg
        try:
            pool = self._get_pool()
            with pool.connection() as db:  # commits on success, rolls back on error
                yield db
        except (psycopg.OperationalError, TimeoutError) as error:
            log.exception("Lakebase unavailable")
            raise StorageUnavailable(str(error)) from error
        except Exception as error:
            if type(error).__name__ == "PoolTimeout":
                raise StorageUnavailable(str(error)) from error
            raise

    def _migrate(self, db):
        """One-time copy of existing journal entries into their table.

        The marker row makes this run once, even with several workers starting
        together; deleting every entry later never re-imports old ones.
        """
        claimed = db.execute(f"INSERT INTO {SCHEMA}.migrations(name) VALUES ('journal_entries_table') ON CONFLICT DO NOTHING")
        if claimed.rowcount != 1:
            return
        row = db.execute(f"SELECT data FROM {SCHEMA}.collections WHERE name = 'journal_entries'").fetchone()
        entries = row["data"] if row else _read_file("journal_entries", use_mock=False)
        for entry in entries or []:
            if entry.get("id"):
                self._journal_write(db, entry, replace=False)
        log.info("Moved %d journal entries into %s.journal_entries", len(entries or []), SCHEMA)

    def _journal_write(self, db, entry, replace=True):
        from psycopg.types.json import Jsonb
        params = _journal_params(entry)
        for key in ("habits", "tags", "accounts"):
            params[key] = Jsonb(params[key])
        conflict = """DO UPDATE SET entry_date = EXCLUDED.entry_date, quote = EXCLUDED.quote,
            quote_author = EXCLUDED.quote_author, breakfast = EXCLUDED.breakfast, lunch = EXCLUDED.lunch,
            dinner = EXCLUDED.dinner, free_write = EXCLUDED.free_write, habits = EXCLUDED.habits,
            tags = EXCLUDED.tags, accounts = EXCLUDED.accounts, source = EXCLUDED.source,
            created_at = EXCLUDED.created_at, updated_at = now()""" if replace else "DO NOTHING"
        row = db.execute(
            f"""INSERT INTO {SCHEMA}.journal_entries
                (id, entry_date, quote, quote_author, breakfast, lunch, dinner, free_write, habits, tags, accounts, source, created_at)
                VALUES (%(id)s, %(entry_date)s, %(quote)s, %(quote_author)s, %(breakfast)s, %(lunch)s, %(dinner)s,
                        %(free_write)s, %(habits)s, %(tags)s, %(accounts)s, %(source)s, %(created_at)s)
                ON CONFLICT (id) {conflict}
                RETURNING (xmax = 0) AS inserted""", params).fetchone()
        return bool(row and row["inserted"])

    def journal_list(self):
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM {SCHEMA}.journal_entries ORDER BY entry_date DESC, created_at DESC").fetchall()
        return [_journal_entry(r) for r in rows]

    def journal_upsert(self, entry):
        with self.connect() as db:
            return self._journal_write(db, entry)

    def journal_delete(self, entry_id):
        with self.connect() as db:
            return db.execute(f"DELETE FROM {SCHEMA}.journal_entries WHERE id = %s", (entry_id,)).rowcount == 1

    def journal_date_exists(self, day):
        try:
            day = date.fromisoformat(day)
        except ValueError:
            return False
        with self.connect() as db:
            return db.execute(f"SELECT EXISTS (SELECT 1 FROM {SCHEMA}.journal_entries WHERE entry_date = %s) AS found", (day,)).fetchone()["found"]

    def load(self, name):
        with self.connect() as db:
            row = db.execute(f"SELECT data FROM {SCHEMA}.collections WHERE name = %s", (name,)).fetchone()
        return row["data"] if row else _read_file(name)

    def save(self, name, data):
        from psycopg.types.json import Jsonb
        with self.connect() as db:
            db.execute(
                f"""INSERT INTO {SCHEMA}.collections(name, data) VALUES (%s, %s)
                    ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data,
                    revision = {SCHEMA}.collections.revision + 1, updated_at = now()""",
                (name, Jsonb(data)))

    def put_media(self, relpath, content, content_type):
        with self.connect() as db:
            db.execute(
                f"""INSERT INTO {SCHEMA}.media(path, content_type, content) VALUES (%s, %s, %s)
                    ON CONFLICT (path) DO UPDATE SET content_type = EXCLUDED.content_type,
                    content = EXCLUDED.content, updated_at = now()""",
                (relpath, content_type, content))

    def delete_media(self, relpath):
        with self.connect() as db:
            db.execute(f"DELETE FROM {SCHEMA}.media WHERE path = %s", (relpath,))

    def get_media(self, relpath):
        with self.connect() as db:
            row = db.execute(f"SELECT content_type, content FROM {SCHEMA}.media WHERE path = %s", (relpath,)).fetchone()
        return (row["content_type"], bytes(row["content"])) if row else None

    def media_paths(self):
        with self.connect() as db:
            return [r["path"] for r in db.execute(f"SELECT path FROM {SCHEMA}.media ORDER BY path")]

    @contextmanager
    def trackers(self):
        with self.connect() as db:
            yield _PostgresTrackers(db)


class _PostgresTrackers:
    def __init__(self, db):
        self.db = db

    def get(self, kind):
        return self.db.execute(f"SELECT kind, document, state, revision FROM {SCHEMA}.trackers WHERE kind = %s", (kind,)).fetchone()

    def insert(self, kind, document):
        result = self.db.execute(f"INSERT INTO {SCHEMA}.trackers(kind, document) VALUES (%s, %s) ON CONFLICT (kind) DO NOTHING", (kind, document))
        if result.rowcount != 1:
            raise AlreadyInstalled()

    def update(self, kind, encoded, revision):
        result = self.db.execute(f"UPDATE {SCHEMA}.trackers SET state = %s, revision = revision + 1, updated_at = now() WHERE kind = %s AND revision = %s", (encoded, kind, revision))
        return result.rowcount == 1


# ── Active backend ────────────────────────────────────────────────────────────

_backend = LakebaseBackend() if os.environ.get("PGHOST") else LocalBackend()


def backend():
    return _backend


def use(new_backend):
    """Swap the backend (tests)."""
    global _backend
    _backend = new_backend


def load(name):
    return _backend.load(name)


def save(name, data):
    _backend.save(name, data)


def journal_list():
    return _backend.journal_list()


def journal_upsert(entry):
    """Create or replace one entry; returns True if it was new."""
    return _backend.journal_upsert(entry)


def journal_delete(entry_id):
    return _backend.journal_delete(entry_id)


def journal_date_exists(day):
    return _backend.journal_date_exists(day)


def save_upload(file_storage, path):
    """Save an uploaded file under static/ and, with Lakebase, persist a copy."""
    path = Path(path)
    target = path if path.is_absolute() else ROOT / path
    relpath = _static_path(target).relative_to(STATIC_DIR.resolve()).as_posix()
    target.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(str(target))
    content_type = mimetypes.guess_type(relpath)[0] or "application/octet-stream"
    _backend.put_media(relpath, target.read_bytes(), content_type)


def delete_upload(path):
    path = Path(path)
    target = path if path.is_absolute() else ROOT / path
    relpath = _static_path(target).relative_to(STATIC_DIR.resolve()).as_posix()
    try:
        target.unlink()
    except OSError:
        pass
    _backend.delete_media(relpath)


def get_media(relpath):
    return _backend.get_media(relpath)


def restore_media():
    """Write stored uploads back under static/ after a redeploy.

    Directory scans (e.g. the music library) then see them; any request that
    arrives first is served from the database by the static fallback route.
    """
    restored = 0
    for relpath in _backend.media_paths():
        target = _static_path(relpath)
        if target.exists():
            continue
        media = _backend.get_media(relpath)
        if media:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(media[1])
            restored += 1
    return restored
