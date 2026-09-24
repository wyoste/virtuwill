"""Start-up data steps, run by db.bootstrap under the schema lock.

set_aside_legacy_schemas  renames the first Lakebase journal/health schemas
                          (PR #3) aside before the new schema files run
run_pending               every start: reference data and bundled media;
                          once per database: the move into the relational model

The one-time move reads each feature from wherever it lived before: the
set-aside schemas, the virtuwill.collections documents, or the JSON files
committed under data/. Nothing it reads is deleted.
"""
import json
import logging

from . import content, finance, garden, health, journal, media, music, travel

log = logging.getLogger(__name__)
DATA_DIR = media.db.ROOT / "data"
LEGACY = {"journal": "legacy_journal_v0", "health": "legacy_health_v0"}


def _exists(conn, name):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL AS found", (name,)).fetchone()["found"]


def set_aside_legacy_schemas(conn):
    """The first journal/health tables had a different shape; keep them under another name."""
    legacy = conn.execute("""SELECT 1 FROM information_schema.columns
                             WHERE table_schema = 'journal' AND table_name = 'entries'
                               AND column_name = 'account_snapshots'""").fetchone()
    if not legacy:
        return
    for schema, renamed in LEGACY.items():
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone():
            conn.execute(f"ALTER SCHEMA {schema} RENAME TO {renamed}")
            log.info("Kept the earlier %s schema as %s", schema, renamed)


def run_pending(conn):
    garden.seed_species(conn)
    content.seed_projects(conn)
    media.sync_bundled(conn)
    music.sync_bundled(conn)
    if conn.execute("INSERT INTO virtuwill.migrations (name) VALUES ('relational_v1') ON CONFLICT DO NOTHING").rowcount:
        move_to_relational(conn)


# ── The one-time move ────────────────────────────────────────────────────────

def _collection(conn, name, filename=None):
    """A feature's data from the earlier collections table, else its committed file."""
    if _exists(conn, "virtuwill.collections"):
        row = conn.execute("SELECT data FROM virtuwill.collections WHERE name = %s", (name,)).fetchone()
        if row:
            return row["data"]
    path = DATA_DIR / (filename or name + ".json")
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        log.warning("Could not read %s", path)
        return None


def _page_string(conn, name):
    """Values the pages once kept in browser storage were saved as JSON strings."""
    value = _collection(conn, name)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _step(conn, name, fn, *args):
    """Each feature moves in a savepoint: one bad record never blocks the rest."""
    try:
        with conn.transaction():
            result = fn(conn, *args)
        log.info("Moved %s", name)
        return result
    except Exception:
        log.exception("Could not move %s", name)
        return {"error": name}


def move_to_relational(conn):
    report = {}
    report["media"] = _step(conn, "uploaded files", _move_media)
    report["journal"] = _step(conn, "journal", _move_journal)
    report["health_manual"] = _step(conn, "logged workouts and weigh-ins", _move_health_manual)
    for name, fn in (("garden", _move_garden), ("music", _move_music), ("content", _move_content),
                     ("finance", _move_accounts_template), ("travel", _move_travel), ("trackers", _move_trackers)):
        report[name] = _step(conn, name, fn)
    conn.execute("""INSERT INTO virtuwill.sync_reports (source, report) VALUES ('relational_v1', %s)
                    ON CONFLICT (source) DO UPDATE SET report = EXCLUDED.report, synced_at = now()""",
                 (media.db.jsonb(report),))


def _move_media(conn):
    if not _exists(conn, "virtuwill.media"):
        return 0
    rows = conn.execute("SELECT path, content FROM virtuwill.media").fetchall()
    for row in rows:
        media.register(conn, row["path"], bytes(row["content"]))
    return len(rows)


def _legacy_journal(conn):
    schema = LEGACY["journal"]
    entries = []
    for e in conn.execute(f"SELECT * FROM {schema}.entries ORDER BY entry_date").fetchall():
        day = e["entry_date"]
        tags = [r["tag"] for r in conn.execute(f"SELECT tag FROM {schema}.entry_tags WHERE entry_date = %s ORDER BY position", (day,))]
        habits = {r["habit"]: r["done"] for r in conn.execute(f"SELECT habit, done FROM {schema}.habit_logs WHERE entry_date = %s", (day,))}
        meals = {slot: "" for slot in journal.SLOTS}
        for r in conn.execute(f"SELECT slot, description FROM {schema}.meals WHERE source = 'journal' AND meal_date = %s", (day,)):
            key = next((k for k, v in journal.SLOTS.items() if v == r["slot"]), None)
            if key:
                meals[key] = r["description"]
        entries.append({"id": e["entry_id"], "date": day.isoformat(), "quote": e["quote"], "quoteAuthor": e["quote_author"],
                        "freeWrite": e["free_write"], "source": e["source"], "createdAt": e["created_at"].isoformat(),
                        "tags": tags, "habits": habits, "meals": meals, "accounts": e["account_snapshots"] or []})
    return entries


def _move_journal(conn):
    if _exists(conn, LEGACY["journal"] + ".entries"):
        entries = _legacy_journal(conn)
    else:
        entries = _collection(conn, "journal_entries") or []
    conflicts = journal.import_entries(conn, entries)
    if conflicts:
        # Never silently pick one of two entries for the same date.
        conn.execute("""INSERT INTO virtuwill.sync_reports (source, report) VALUES ('journal_import_conflicts', %s)
                        ON CONFLICT (source) DO UPDATE SET report = EXCLUDED.report, synced_at = now()""",
                     (media.db.jsonb(conflicts),))
    return {"entries": len(entries) - len(conflicts), "conflicts": len(conflicts)}


def _move_health_manual(conn):
    """Workouts and weigh-ins logged on the dashboard (tracker rows are re-projected instead)."""
    schema = LEGACY["journal"]
    moved = {"workouts": 0, "weighIns": 0}
    if _exists(conn, schema + ".workouts"):
        for w in conn.execute(f"SELECT * FROM {schema}.workouts WHERE source = 'manual'").fetchall():
            kind = "Dog walk" if w["is_dog_walk"] else health._workout_type(conn, w["activity"])
            conn.execute("""INSERT INTO journal.workouts (workout_date, workout_type, activity, minutes, note, source)
                            VALUES (%s, %s, %s, %s, %s, 'manual')""",
                         (w["workout_date"], kind, "" if kind == w["activity"] else w["activity"], w["minutes"], w["note"]))
            moved["workouts"] += 1
    schema = LEGACY["health"]
    if _exists(conn, schema + ".body_measurements"):
        for m in conn.execute(f"SELECT * FROM {schema}.body_measurements WHERE source = 'manual'").fetchall():
            conn.execute("""INSERT INTO health.body_measurements (measured_on, measured_at, metric, value, unit, is_morning, note, source)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual')""",
                         (m["measured_on"], m["measured_at"], m["metric"], m["value"], m["unit"], m["is_morning"], m["note"]))
            moved["weighIns"] += 1
    return moved


def _move_garden(conn):
    doc = _collection(conn, "garden")
    if isinstance(doc, dict) and doc.get("beds"):
        garden.save(conn, doc)
    garden.import_photos(conn, _collection(conn, "garden_photos") or [])
    for name, key in (("garden_gallery_note", "garden.gallery_note"), ("garden_gallery_hero", "garden.hero")):
        value = _page_string(conn, name)
        if value not in (None, ""):
            content.set_site_text(conn, key, value if isinstance(value, str) else json.dumps(value))
    return {"beds": len((doc or {}).get("beds") or [])}


def _move_music(conn):
    doc = _collection(conn, "music_catalog")
    if isinstance(doc, dict) and doc.get("tracks"):
        music.save_catalog(conn, doc)
    return {"songs": len((doc or {}).get("tracks") or [])}


def _move_content(conn):
    content.import_posts(conn, _collection(conn, "blog") or [])
    content.import_messages(conn, _collection(conn, "messages") or [])
    content.import_uploads(conn, _collection(conn, "portfolio_uploads") or [])
    layout = _page_string(conn, "portfolio_layout")
    if isinstance(layout, dict):
        content.set_layout(conn, layout)
    return {}


def _move_accounts_template(conn):
    rows = _collection(conn, "accounts_template")
    if isinstance(rows, list) and rows:
        finance.set_accounts_template(conn, rows)
    return {"accounts": len(rows or [])}


def _move_travel(conn):
    pins = _page_string(conn, "travel_pins")
    if isinstance(pins, list):
        travel.set_pins(conn, pins)
    visited = _page_string(conn, "travel_visited")
    if isinstance(visited, dict):
        travel.set_visited(conn, visited)
    return {"pins": len(pins or []) if isinstance(pins, list) else 0}


def _move_trackers(conn):
    from . import trackers
    moved = []
    for row in conn.execute("SELECT kind, document, state FROM virtuwill.trackers WHERE state IS NOT NULL").fetchall():
        trackers.project(conn, row["kind"], json.loads(row["state"]), row["document"])
        moved.append(row["kind"])
    return moved

