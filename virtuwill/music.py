"""Music: songs (the compositions), recordings (the audio files), albums and
gallery photos.

Audio shipped under static/audio is registered on start: each folder is an
album and each file a recording. The music page's catalog entries are songs,
linked to a recording through the audio path they point at.
"""
import json
import os
import re
from pathlib import PurePosixPath

from flask import Blueprint, jsonify, request

from . import db, media
from .auth import admin_required
from .util import number

bp = Blueprint("music", __name__)
SECTION_TYPES = {"intro", "verse", "pre-chorus", "chorus", "bridge", "solo", "outro", "coda"}


def _art_for(conn, candidates):
    for path in candidates:
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            row = conn.execute("SELECT asset_id FROM core.media_assets WHERE path = %s", (path + ext,)).fetchone()
            if row:
                return row["asset_id"]
    return None


def add_recording(conn, asset, path, title=None):
    """The recording for an audio file under audio/, created with its album on first sight."""
    existing = conn.execute("SELECT recording_id FROM music.recordings WHERE audio_asset_id = %s", (asset,)).fetchone()
    if existing:
        return existing["recording_id"]
    parts = PurePosixPath(path).parts          # ('audio', 'Album', 'track.mp3') or ('audio', 'single.mp3')
    stem = PurePosixPath(path).stem
    album = parts[1] if len(parts) == 3 else None
    if album:
        conn.execute("INSERT INTO music.albums (album_id, title, art_asset_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                     (album, album, _art_for(conn, [f"audio/{album}", f"audio/{album}/{album}", f"audio/{album}/cover"])))
        art = _art_for(conn, [f"audio/{album}/{stem}"])
    else:
        art = _art_for(conn, [f"audio/{stem}"])
    return conn.execute("""INSERT INTO music.recordings (album_id, title, audio_asset_id, art_asset_id, published)
                           VALUES (%s, %s, %s, %s, true) RETURNING recording_id""",
                        (album, title or stem.replace("_", " "), asset, art)).fetchone()["recording_id"]


def sync_bundled(conn):
    """Recordings for every audio file and gallery entries for every music photo."""
    for row in conn.execute("""SELECT asset_id, path FROM core.media_assets m WHERE path LIKE 'audio/%%'
                               AND content_type LIKE 'audio/%%'
                               AND NOT EXISTS (SELECT 1 FROM music.recordings r WHERE r.audio_asset_id = m.asset_id)
                               ORDER BY path""").fetchall():
        if len(PurePosixPath(row["path"]).parts) in (2, 3):
            add_recording(conn, row["asset_id"], row["path"])
    conn.execute("""INSERT INTO music.gallery_photos (asset_id, position)
                    SELECT asset_id, NULL FROM core.media_assets WHERE path LIKE 'music/photos/%%'
                    ON CONFLICT DO NOTHING""")


# ── Catalog (the music page's song list) ─────────────────────────────────────

def catalog(conn):
    tracks = []
    for s in conn.execute("SELECT * FROM music.songs ORDER BY position NULLS LAST, created_at, song_id").fetchall():
        rec = conn.execute("""SELECT m.path, a.title AS album FROM music.recordings r
                              JOIN core.media_assets m ON m.asset_id = r.audio_asset_id
                              LEFT JOIN music.albums a ON a.album_id = r.album_id
                              WHERE r.song_id = %s ORDER BY r.published DESC, r.recording_id DESC LIMIT 1""",
                           (s["song_id"],)).fetchone()
        sections = conn.execute("SELECT * FROM music.song_sections WHERE song_id = %s ORDER BY position", (s["song_id"],)).fetchall()
        full = next((x for x in sections if x["section_type"] == "full"), None)
        tracks.append({
            "id": s["song_id"], "title": s["title"], "year": s["year_written"], "location": s["written_at"],
            "story": s["story"], "genre": s["genre"], "key": s["musical_key"], "bpm": number(s["bpm"]),
            "published": s["published"], "album": rec["album"] if rec and rec["album"] else "",
            "src": media.url(rec["path"]) if rec else None,
            "chords": full["chords"] if full else "", "lyrics": full["lyrics"] if full else "", "tabs": full["tabs"] if full else "",
            "sections": [{"id": f"sec-{s['song_id']}-{x['position']}", "type": x["section_type"], "label": x["label"],
                          "chords": x["chords"], "lyrics": x["lyrics"], "tabs": x["tabs"]}
                         for x in sections if x["section_type"] != "full"],
        })
    return {"tracks": tracks}


def save_catalog(conn, doc):
    tracks = [t for t in (doc or {}).get("tracks") or [] if isinstance(t, dict) and t.get("id") and t.get("title")]
    conn.execute("DELETE FROM music.songs WHERE NOT (song_id = ANY(%s))", ([str(t["id"]) for t in tracks],))
    for position, t in enumerate(tracks):
        song_id = str(t["id"])
        year, bpm = number(t.get("year")), number(t.get("bpm"))
        conn.execute(
            """INSERT INTO music.songs (song_id, title, year_written, written_at, story, genre, musical_key, bpm, published, position)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (song_id) DO UPDATE SET title = EXCLUDED.title, year_written = EXCLUDED.year_written,
                   written_at = EXCLUDED.written_at, story = EXCLUDED.story, genre = EXCLUDED.genre,
                   musical_key = EXCLUDED.musical_key, bpm = EXCLUDED.bpm, published = EXCLUDED.published,
                   position = EXCLUDED.position, updated_at = now()""",
            (song_id, t["title"], int(year) if year and 1900 <= year <= 2100 else None, t.get("location") or "",
             t.get("story") or "", t.get("genre") or "", t.get("key") or "", bpm if bpm and bpm > 0 else None,
             t.get("published") is not False, position))
        conn.execute("DELETE FROM music.song_sections WHERE song_id = %s", (song_id,))
        sections = [x for x in t.get("sections") or [] if isinstance(x, dict)]
        if any(t.get(k) for k in ("chords", "lyrics", "tabs")):
            sections = [{"type": "full", "label": "Full song", "chords": t.get("chords"), "lyrics": t.get("lyrics"),
                         "tabs": t.get("tabs")}] + sections
        for position, x in enumerate(sections):
            kind = x.get("type") if x.get("type") in SECTION_TYPES | {"full"} else "verse"
            conn.execute("INSERT INTO music.song_sections VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (song_id, position, kind, x.get("label") or "", x.get("chords") or "", x.get("lyrics") or "", x.get("tabs") or ""))
        conn.execute("UPDATE music.recordings SET song_id = NULL WHERE song_id = %s", (song_id,))
        asset = media.asset_id(conn, t.get("src"))
        if not asset and _audio_path(t.get("src")):
            # Keep the link even if the file is not on this disk yet (restored from the database later).
            asset = media.register(conn, t["src"])
        if asset:
            recording = add_recording(conn, asset, media.path_of(conn, asset), t["title"])
            conn.execute("UPDATE music.recordings SET song_id = %s, published = %s WHERE recording_id = %s",
                         (song_id, t.get("published") is not False, recording))


def _audio_path(src):
    try:
        return bool(src) and media.relpath(src).startswith("audio/")
    except ValueError:
        return False


def _playable(row):
    """On this disk, or kept in the database (restored on start). A song can name audio that is neither."""
    return row["stored"] or (media.STATIC / row["path"]).is_file()


def library(conn):
    """Albums (audio folders) and singles, the shape the music page scans for."""
    albums, singles = [], []
    for a in conn.execute("""SELECT a.album_id, a.title, m.path AS art FROM music.albums a
                             LEFT JOIN core.media_assets m ON m.asset_id = a.art_asset_id ORDER BY a.album_id""").fetchall():
        tracks = conn.execute("""SELECT r.title, m.path, m.content IS NOT NULL AS stored, art.path AS art FROM music.recordings r
                                 JOIN core.media_assets m ON m.asset_id = r.audio_asset_id
                                 LEFT JOIN core.media_assets art ON art.asset_id = r.art_asset_id
                                 WHERE r.album_id = %s ORDER BY m.path""", (a["album_id"],)).fetchall()
        tracks = [t for t in tracks if _playable(t)]
        if tracks:
            albums.append({"type": "album", "name": a["title"], "art": media.url(a["art"]),
                           "tracks": [{"title": PurePosixPath(t["path"]).stem.replace("_", " "), "url": media.url(t["path"]),
                                       "art": media.url(t["art"])} for t in tracks]})
    for r in conn.execute("""SELECT m.path, m.content IS NOT NULL AS stored, art.path AS art FROM music.recordings r
                             JOIN core.media_assets m ON m.asset_id = r.audio_asset_id
                             LEFT JOIN core.media_assets art ON art.asset_id = r.art_asset_id
                             WHERE r.album_id IS NULL ORDER BY m.path""").fetchall():
        if not _playable(r):
            continue
        singles.append({"type": "single", "name": PurePosixPath(r["path"]).stem.replace("_", " "),
                        "url": media.url(r["path"]), "art": media.url(r["art"])})
    return {"albums": albums, "singles": singles}


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/api/music/catalog")
def catalog_get():
    with db.tx() as conn:
        return jsonify(catalog(conn))


@bp.route("/api/music/catalog", methods=["POST"])
@admin_required
def catalog_save():
    with db.tx() as conn:
        save_catalog(conn, request.get_json(force=True) or {})
    return jsonify({"ok": True})


@bp.route("/api/music/library")
def library_get():
    with db.tx() as conn:
        return jsonify(library(conn))


@bp.route("/api/music/upload", methods=["POST"])
@admin_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file"}), 400
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename)
    album = re.sub(r"[^a-zA-Z0-9 ._-]", "_", request.form.get("album", "").strip())
    art = request.files.get("art")
    path = f"audio/{album}/{safe_name}" if album else f"audio/{safe_name}"
    art_path = None
    with db.tx() as conn:
        if art and art.filename:
            ext = os.path.splitext(art.filename)[1].lower() or ".jpg"
            art_path = f"audio/{album}{ext}" if album else f"audio/{os.path.splitext(safe_name)[0]}{ext}"
            media.save_upload(conn, art, art_path)
        asset = media.save_upload(conn, f, path)
        add_recording(conn, asset, path)
    return jsonify({"ok": True, "url": media.url(path), "art": media.url(art_path)})


@bp.route("/api/music/photo", methods=["POST"])
@admin_required
def photo_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file"}), 400
    path = "music/photos/" + re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename)
    with db.tx() as conn:
        asset = media.save_upload(conn, f, path)
        conn.execute("INSERT INTO music.gallery_photos (asset_id) VALUES (%s) ON CONFLICT DO NOTHING", (asset,))
    return jsonify({"ok": True, "url": media.url(path)})


@bp.route("/api/music/photos")
def photos_get():
    with db.tx() as conn:
        rows = conn.execute("""SELECT m.path FROM music.gallery_photos g JOIN core.media_assets m USING (asset_id)
                               WHERE m.content_type LIKE 'image/%%' ORDER BY g.position NULLS LAST, m.path""").fetchall()
    return jsonify([media.url(r["path"]) for r in rows])


@bp.route("/api/music")
def legacy_sample():
    """Legacy endpoint: the bundled sample library."""
    try:
        return jsonify(json.loads((db.ROOT / "mock_data" / "music_library.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return jsonify([])
