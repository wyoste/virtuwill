"""Site settings and the page data the pages once kept in browser storage.

core.settings holds owner switches such as whether the chat widget shows.
/api/data/<name> keeps its string-in, string-out contract, but each name is
now backed by real tables: travel.places, travel.visited_regions,
content.site_text and content.portfolio_projects.
"""
import json
import os
import time
import uuid

from flask import Blueprint, jsonify, request

from . import content, db, media, travel
from .auth import admin_required, is_admin

bp = Blueprint("site", __name__)

# Settings the owner can change, their type, and whether visitors may read them.
SETTINGS = {"site.chat_enabled": (bool, True),
            # Money overview: everything, or only the goals.
            "money.overview_mode": (str, False, {"full", "goals"})}


def settings(conn, public_only=False):
    rows = conn.execute("SELECT key, value FROM core.settings").fetchall()
    return {r["key"]: r["value"] for r in rows if r["key"] in SETTINGS and (SETTINGS[r["key"]][1] or not public_only)}


def setting(key, default=None):
    """One setting, or the default when the database is unavailable."""
    try:
        with db.tx() as conn:
            row = conn.execute("SELECT value FROM core.settings WHERE key = %s", (key,)).fetchone()
    except db.DatabaseUnavailable:
        return default
    return row["value"] if row else default


@bp.route("/api/settings")
def settings_get():
    with db.tx() as conn:
        return jsonify(settings(conn, public_only=not is_admin()))


@bp.route("/api/settings", methods=["PUT"])
@admin_required
def settings_put():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        return jsonify({"error": "Expected an object of settings"}), 400
    for key, value in payload.items():
        if key not in SETTINGS:
            return jsonify({"error": f"Unknown setting: {key}"}), 404
        if not isinstance(value, SETTINGS[key][0]):
            return jsonify({"error": f"{key} must be a {SETTINGS[key][0].__name__}"}), 400
        if len(SETTINGS[key]) > 2 and value not in SETTINGS[key][2]:
            return jsonify({"error": f"{key} must be one of: {', '.join(sorted(SETTINGS[key][2]))}"}), 400
    with db.tx() as conn:
        for key, value in payload.items():
            conn.execute("""INSERT INTO core.settings (key, value) VALUES (%s, %s)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                         (key, db.jsonb(value)))
        return jsonify(settings(conn))


# ── Page data (strings the pages parse) ──────────────────────────────────────

def _parse(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def _stored(value):
    """None when nothing is stored yet, so a browser holding older data uploads it once (store.js)."""
    return json.dumps(value) if value else None


# name → (read(conn) → string or None, write(conn, string))
PAGE_DATA = {
    "travel_pins": (lambda c: _stored(travel.pins(c)),
                    lambda c, s: travel.set_pins(c, _parse(s) if isinstance(_parse(s), list) else [])),
    "travel_visited": (lambda c: _stored(travel.visited(c)) if any(travel.visited(c).values()) else None,
                       lambda c, s: travel.set_visited(c, _parse(s))),
    "garden_gallery_note": (lambda c: content.site_text(c, "garden.gallery_note"),
                            lambda c, s: content.set_site_text(c, "garden.gallery_note", s)),
    "garden_gallery_hero": (lambda c: content.site_text(c, "garden.hero"),
                            lambda c, s: content.set_site_text(c, "garden.hero", s)),
    "portfolio_layout": (lambda c: _stored(content.layout(c)) if any(not v["visible"] or v["deleted"] for v in content.layout(c).values()) else None,
                         lambda c, s: content.set_layout(c, _parse(s) if isinstance(_parse(s), dict) else {})),
}


@bp.route("/api/data/<name>", methods=["GET"])
def page_data_get(name):
    if name not in PAGE_DATA:
        return jsonify({"error": "Unknown data"}), 404
    with db.tx() as conn:
        return jsonify({"data": PAGE_DATA[name][0](conn)})


@bp.route("/api/data/<name>", methods=["PUT"])
@admin_required
def page_data_put(name):
    if name not in PAGE_DATA:
        return jsonify({"error": "Unknown data"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str) or len(payload["data"]) > 1_000_000:
        return jsonify({"error": "Expected a data string under 1 MB"}), 400
    with db.tx() as conn:
        PAGE_DATA[name][1](conn, payload["data"])
    return jsonify({"ok": True})


# ── Diagnostics (owner only) ─────────────────────────────────────────────────

def _commit():
    """The deployed commit: APP_COMMIT if set, else read from .git when present."""
    if os.environ.get("APP_COMMIT"):
        return os.environ["APP_COMMIT"]
    head = db.ROOT / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            ref = (db.ROOT / ".git" / ref[5:]).read_text().strip()
        return ref[:12]
    except OSError:
        return None


@bp.route("/api/admin/diagnostics")
@admin_required
def diagnostics():
    """What is deployed and whether each part is working: build, database, schema, trackers."""
    out = {"commit": _commit(), "database": {"configured": db.configured(), "reachable": False}}
    if not db.configured():
        return jsonify(out)
    try:
        with db.tx() as conn:
            out["database"].update(reachable=True, **conn.execute(
                "SELECT current_database() AS name, current_setting('TimeZone') AS timezone").fetchone())
            out["schema"] = db.schema_status(conn)
            out["migrations"] = [r["name"] for r in conn.execute("SELECT name FROM virtuwill.migrations ORDER BY applied_at")]
            out["trackers"] = [{"kind": r["kind"], "installed": True, "revision": r["revision"],
                                "savedAt": r["updated_at"].isoformat(), "hasState": r["has_state"]}
                               for r in conn.execute("""SELECT kind, revision, updated_at, state IS NOT NULL AS has_state
                                                        FROM virtuwill.trackers ORDER BY kind""")]
            out["syncs"] = [{"source": r["source"], "syncedAt": r["synced_at"].isoformat(),
                             "ok": not (isinstance(r["report"], dict) and r["report"].get("error")),
                             "error": r["report"].get("error") if isinstance(r["report"], dict) else None}
                            for r in conn.execute("SELECT * FROM virtuwill.sync_reports ORDER BY source")]
            out["media"] = conn.execute("""SELECT COUNT(*) AS files, COUNT(content) AS stored_in_database,
                                                  COALESCE(SUM(octet_length(content)), 0) AS stored_bytes
                                           FROM core.media_assets""").fetchone()
            out["settings"] = settings(conn)
    except db.DatabaseUnavailable as error:
        out["database"]["error"] = str(error)
    return jsonify(out)


# ── Travel (v1) ───────────────────────────────────────────────────────────────

@bp.route("/api/v1/travel")
def travel_v1():
    with db.tx() as conn:
        return jsonify({"places": travel.pins(conn), "visited": travel.visited(conn)})


@bp.route("/api/v1/travel/places", methods=["PUT"])
@admin_required
def travel_places_v1():
    items = request.get_json(silent=True)
    if not isinstance(items, list):
        return jsonify({"error": "Expected a list of places"}), 400
    with db.tx() as conn:
        travel.set_pins(conn, items)
        return jsonify({"places": travel.pins(conn)})


@bp.route("/api/v1/travel/places", methods=["POST"])
@admin_required
def travel_place_add_v1():
    data = request.get_json(silent=True) or {}
    place_id = str(int(time.time() * 1000))
    with db.tx() as conn:
        problem = travel.write_place(conn, place_id, data)
        if problem:
            return jsonify({"error": problem}), 400
        return jsonify(next(p for p in travel.pins(conn) if str(p["id"]) == place_id)), 201


@bp.route("/api/v1/travel/places/<place_id>", methods=["PUT", "DELETE"])
@admin_required
def travel_place_v1(place_id):
    with db.tx() as conn:
        if not conn.execute("SELECT 1 FROM travel.places WHERE place_id = %s", (place_id,)).fetchone():
            return jsonify({"error": "Not found"}), 404
        if request.method == "DELETE":
            paths = [r["path"] for r in conn.execute("""SELECT m.path FROM travel.place_photos ph JOIN core.media_assets m USING (asset_id)
                                                        WHERE ph.place_id = %s""", (place_id,))]
            conn.execute("DELETE FROM travel.places WHERE place_id = %s", (place_id,))
            for path in paths:
                media.delete(conn, path)
            return jsonify({"ok": True})
        current = next(p for p in travel.pins(conn) if str(p["id"]) == place_id)
        problem = travel.write_place(conn, place_id, {**current, **(request.get_json(silent=True) or {})})
        if problem:
            return jsonify({"error": problem}), 400
        return jsonify(next(p for p in travel.pins(conn) if str(p["id"]) == place_id))


@bp.route("/api/v1/travel/places/<place_id>/photos", methods=["POST"])
@admin_required
def travel_photos_add_v1(place_id):
    """Attach photos to a place: files, caption."""
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"error": "Choose one or more photos"}), 400
    caption = request.form.get("caption", "").strip()[:500]
    with db.tx() as conn:
        if not conn.execute("SELECT 1 FROM travel.places WHERE place_id = %s", (place_id,)).fetchone():
            return jsonify({"error": "Not found"}), 404
        position = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) AS n FROM travel.place_photos WHERE place_id = %s",
                                (place_id,)).fetchone()["n"]
        for f in files:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in media.IMAGES:
                return jsonify({"error": f"{f.filename}: only JPEG, PNG, WebP or GIF images"}), 400
            asset = media.save_upload(conn, f, f"travel/photos/{uuid.uuid4().hex[:12]}{ext}")
            conn.execute("INSERT INTO travel.place_photos (place_id, position, asset_id, caption) VALUES (%s, %s, %s, %s)",
                         (place_id, position, asset, caption))
            position += 1
        return jsonify(next(p for p in travel.pins(conn) if str(p["id"]) == place_id)), 201


@bp.route("/api/v1/travel/places/<place_id>/photos/<int:position>", methods=["PUT", "DELETE"])
@admin_required
def travel_photo_v1(place_id, position):
    with db.tx() as conn:
        row = conn.execute("""SELECT ph.asset_id, m.path FROM travel.place_photos ph LEFT JOIN core.media_assets m USING (asset_id)
                              WHERE ph.place_id = %s AND ph.position = %s""", (place_id, position)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        if request.method == "DELETE":
            conn.execute("DELETE FROM travel.place_photos WHERE place_id = %s AND position = %s", (place_id, position))
            if row["path"]:
                media.delete(conn, row["path"])
        else:
            caption = str((request.get_json(silent=True) or {}).get("caption") or "").strip()[:500]
            conn.execute("UPDATE travel.place_photos SET caption = %s WHERE place_id = %s AND position = %s", (caption, place_id, position))
        return jsonify(next(p for p in travel.pins(conn) if str(p["id"]) == place_id))


@bp.route("/api/v1/travel/visited", methods=["PUT"])
@admin_required
def travel_visited_v1():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected {countries, states}"}), 400
    with db.tx() as conn:
        travel.set_visited(conn, data)
        return jsonify(travel.visited(conn))
