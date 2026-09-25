"""Music: songs (the compositions), recordings (the audio files), albums and
gallery photos.

Audio shipped under static/audio is registered on start: each folder is an
album and each file a recording. The music page's catalog entries are songs,
linked to a recording through the audio path they point at.
"""
import json
import os
import re
import uuid
from pathlib import PurePosixPath

from flask import Blueprint, jsonify, request

from . import db, media
from .auth import admin_required, is_admin
from .util import number, plain, slug

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
    if album:
        # An album appears publicly once it has music; the owner can hide it in the workspace.
        conn.execute("UPDATE music.albums SET published = true WHERE album_id = %s AND NOT EXISTS "
                     "(SELECT 1 FROM music.recordings WHERE album_id = %s)", (album, album))
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
            """INSERT INTO music.songs (song_id, title, year_written, written_at, story, genre, musical_key, bpm, published, position, slug)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (song_id) DO UPDATE SET title = EXCLUDED.title, year_written = EXCLUDED.year_written,
                   written_at = EXCLUDED.written_at, story = EXCLUDED.story, genre = EXCLUDED.genre,
                   musical_key = EXCLUDED.musical_key, bpm = EXCLUDED.bpm, published = EXCLUDED.published,
                   position = EXCLUDED.position, updated_at = now()""",
            (song_id, t["title"], int(year) if year and 1900 <= year <= 2100 else None, t.get("location") or "",
             t.get("story") or "", t.get("genre") or "", t.get("key") or "", bpm if bpm and bpm > 0 else None,
             t.get("published") is not False, position, unique_slug(conn, t["title"], song_id)))
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


# ── Workspace and public API (v1) ────────────────────────────────────────────
# Songs are the compositions (one page each); recordings are their versions.
# Visitors see published songs, and only published recordings on published
# albums (or singles). The owner sees everything, including audio files not
# yet attached to a song.

SONG_FIELDS = ("title", "year_written", "written_at", "story", "genre", "musical_key", "bpm", "published",
               "meaning", "themes", "capo", "tuning", "time_signature", "strumming", "influences")
# What a song page shows about the song beyond the audio ("behind the scenes").
ABOUT_FIELDS = ("meaning", "themes", "capo", "tuning", "time_signature", "strumming", "influences")
TEXT_FIELDS = ("title", "written_at", "story", "genre", "musical_key", "meaning", "tuning", "time_signature",
               "strumming", "influences")


def unique_slug(conn, title, song_id=None):
    base, n = slug(title), 1
    candidate = base
    while conn.execute("SELECT 1 FROM music.songs WHERE slug = %s AND song_id IS DISTINCT FROM %s", (candidate, song_id)).fetchone():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _recordings(conn, owner):
    visible = "" if owner else """ AND r.published AND (r.album_id IS NULL OR a.published)"""
    return [plain(r) | {"url": media.url(r["path"]), "art": media.url(r["art_path"] or r["album_art_path"])}
            for r in conn.execute(f"""
                SELECT r.recording_id, r.song_id, s.slug AS song_slug, s.title AS song_title, r.album_id, a.title AS album, a.published AS album_published,
                       a.release_year, r.track_number, r.title, r.version_label, r.notes, r.duration_seconds, r.published,
                       m.path, art.path AS art_path, aart.path AS album_art_path
                FROM music.recordings r
                JOIN core.media_assets m ON m.asset_id = r.audio_asset_id
                LEFT JOIN core.media_assets art ON art.asset_id = r.art_asset_id
                LEFT JOIN music.albums a ON a.album_id = r.album_id
                LEFT JOIN core.media_assets aart ON aart.asset_id = a.art_asset_id
                LEFT JOIN music.songs s ON s.song_id = r.song_id
                WHERE (m.content IS NOT NULL OR m.byte_size IS NOT NULL){visible}
                ORDER BY a.title NULLS LAST, r.track_number NULLS LAST, m.path""")]


def _song(row, versions, sections=None, notes=None):
    song = {k: row[k] for k in ("song_id", "slug", "title", "year_written", "written_at", "genre", "musical_key", "published")}
    song.update({k: row[k] for k in ABOUT_FIELDS})
    song.update(bpm=number(row["bpm"]), story=row["story"], versions=versions,
                art=next((v["art"] for v in versions if v["art"]), None),
                has_lyrics=row["has_lyrics"], has_chords=row["has_chords"], note_count=row["note_count"])
    if sections is not None:
        song["sections"] = sections
    if notes is not None:
        song["notes"] = notes
    return song


# How much there is to explore on each song, for the list's badges.
SONG_EXTRAS = """EXISTS (SELECT 1 FROM music.song_sections x WHERE x.song_id = s.song_id AND x.lyrics <> '') AS has_lyrics,
                 EXISTS (SELECT 1 FROM music.song_sections x WHERE x.song_id = s.song_id AND x.chords <> '') AS has_chords,
                 (SELECT COUNT(*) FROM music.song_notes n WHERE n.song_id = s.song_id) AS note_count"""


def music_page(conn, owner):
    recordings = _recordings(conn, owner)
    by_song = {}
    for r in recordings:
        if r["song_id"]:
            by_song.setdefault(r["song_id"], []).append(r)
    songs = [_song(s, by_song.get(s["song_id"], [])) for s in conn.execute(
        f"""SELECT s.*, {SONG_EXTRAS}
            FROM music.songs s {'' if owner else 'WHERE s.published'}
            ORDER BY s.position NULLS LAST, s.title""")]
    albums = {}
    for r in recordings:
        if r["album_id"]:
            album = albums.setdefault(r["album_id"], {"album_id": r["album_id"], "title": r["album"], "year": r["release_year"],
                                                      "published": r["album_published"], "art": media.url(r["album_art_path"]),
                                                      "tracks": []})
            album["tracks"].append(r)
    return {"songs": songs, "albums": list(albums.values()),
            "singles": [r for r in recordings if not r["album_id"] and not r["song_id"]],
            "unassigned": [r for r in recordings if not r["song_id"]] if owner else None}


@bp.route("/api/v1/music")
def music_v1():
    owner = is_admin() and request.args.get("view") == "owner"
    with db.tx() as conn:
        return jsonify(music_page(conn, owner))


@bp.route("/api/v1/music/songs/<slug_or_id>")
def song_v1(slug_or_id):
    owner = is_admin() and request.args.get("view") == "owner"
    with db.tx() as conn:
        row = conn.execute(f"""SELECT s.*, {SONG_EXTRAS}
                               FROM music.songs s WHERE (slug = %s OR song_id = %s)""" + ("" if owner else " AND published"),
                           (slug_or_id, slug_or_id)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        versions = [r for r in _recordings(conn, owner) if r["song_id"] == row["song_id"]]
        sections = [plain(x) for x in conn.execute(
            "SELECT position, section_type, label, chords, lyrics, tabs FROM music.song_sections WHERE song_id = %s ORDER BY position",
            (row["song_id"],))]
        notes = [plain(n) for n in conn.execute(
            "SELECT line_text, note FROM music.song_notes WHERE song_id = %s ORDER BY position, note_id", (row["song_id"],))]
        return jsonify(_song(row, versions, sections, notes))


def _write_song(conn, song_id, data):
    fields = {k: data[k] for k in SONG_FIELDS if k in data}
    if "title" in fields and not str(fields["title"]).strip():
        raise ValueError("A song needs a title")
    if "year_written" in fields:
        year = number(fields["year_written"])
        fields["year_written"] = int(year) if year and 1900 <= year <= 2100 else None
    if "bpm" in fields:
        bpm = number(fields["bpm"])
        fields["bpm"] = bpm if bpm and 0 < bpm < 400 else None
    if "published" in fields:
        fields["published"] = bool(fields["published"])
    if "capo" in fields:
        capo = number(fields["capo"])
        if fields["capo"] not in (None, "") and (capo is None or capo != int(capo) or not 0 <= capo <= 12):
            raise ValueError("Capo is a fret from 0 to 12")
        fields["capo"] = int(capo) if capo is not None else None
    if "themes" in fields:
        themes = fields["themes"] if fields["themes"] is not None else []
        if isinstance(themes, str):
            themes = themes.split(",")
        if not isinstance(themes, list):
            raise ValueError("Themes are a list of words, e.g. ['home', 'leaving']")
        seen = []
        for t in (str(t or "").strip()[:40] for t in themes):
            if t and t.lower() not in (x.lower() for x in seen):
                seen.append(t)
        fields["themes"] = seen[:12]
    for key in TEXT_FIELDS:
        if key in fields:
            fields[key] = str(fields[key] or "").strip()
    if "title" in fields:
        fields["slug"] = unique_slug(conn, fields["title"], song_id)
    if fields:
        conn.execute(f"UPDATE music.songs SET {', '.join(f'{k} = %s' for k in fields)}, updated_at = now() WHERE song_id = %s",
                     [*fields.values(), song_id])
    if isinstance(data.get("sections"), list):
        conn.execute("DELETE FROM music.song_sections WHERE song_id = %s", (song_id,))
        for position, x in enumerate(s for s in data["sections"] if isinstance(s, dict)):
            kind = x.get("section_type") if x.get("section_type") in SECTION_TYPES | {"full"} else "verse"
            conn.execute("INSERT INTO music.song_sections VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (song_id, position, kind, str(x.get("label") or ""), str(x.get("chords") or ""),
                          str(x.get("lyrics") or ""), str(x.get("tabs") or "")))
    if "notes" in data:
        notes = data["notes"]
        if not isinstance(notes, list) or not all(isinstance(n, dict) for n in notes):
            raise ValueError("Notes are a list of {line_text, note}")
        conn.execute("DELETE FROM music.song_notes WHERE song_id = %s", (song_id,))
        for position, n in enumerate(n for n in notes if str(n.get("line_text") or "").strip() and str(n.get("note") or "").strip()):
            conn.execute("INSERT INTO music.song_notes (song_id, line_text, note, position) VALUES (%s, %s, %s, %s)",
                         (song_id, str(n["line_text"]).strip()[:500], str(n["note"]).strip(), position))


@bp.route("/api/v1/music/songs", methods=["POST"])
@admin_required
def song_create_v1():
    data = request.get_json(silent=True) or {}
    if not str(data.get("title") or "").strip():
        return jsonify({"error": "A song needs a title"}), 400
    song_id = "s" + uuid.uuid4().hex[:12]
    with db.tx() as conn:
        conn.execute("INSERT INTO music.songs (song_id, title, slug, published) VALUES (%s, %s, %s, false)",
                     (song_id, data["title"].strip(), unique_slug(conn, data["title"])))
        try:
            _write_song(conn, song_id, data)
        except ValueError as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        if data.get("recording_id"):
            conn.execute("UPDATE music.recordings SET song_id = %s WHERE recording_id = %s", (song_id, data["recording_id"]))
        return jsonify({"song_id": song_id, "slug": conn.execute("SELECT slug FROM music.songs WHERE song_id = %s",
                                                                 (song_id,)).fetchone()["slug"]}), 201


@bp.route("/api/v1/music/songs/<song_id>", methods=["PUT", "DELETE"])
@admin_required
def song_write_v1(song_id):
    with db.tx() as conn:
        if not conn.execute("SELECT 1 FROM music.songs WHERE song_id = %s", (song_id,)).fetchone():
            return jsonify({"error": "Not found"}), 404
        if request.method == "DELETE":
            # Its recordings stay, back in the unassigned list.
            conn.execute("DELETE FROM music.songs WHERE song_id = %s", (song_id,))
            return jsonify({"ok": True})
        try:
            _write_song(conn, song_id, request.get_json(silent=True) or {})
        except ValueError as e:
            conn.rollback()         # nothing half-saved
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "slug": conn.execute("SELECT slug FROM music.songs WHERE song_id = %s", (song_id,)).fetchone()["slug"]})


@bp.route("/api/v1/music/recordings/<int:recording_id>", methods=["PUT"])
@admin_required
def recording_write_v1(recording_id):
    data = request.get_json(silent=True) or {}
    fields = {}
    if "song_id" in data:
        fields["song_id"] = data["song_id"] or None
    for key in ("title", "version_label"):
        if key in data:
            fields[key] = str(data[key] or "").strip()[:200]
    if "notes" in data:
        fields["notes"] = str(data["notes"] or "").strip()
    if "published" in data:
        fields["published"] = bool(data["published"])
    if not fields:
        return jsonify({"error": "Nothing to change"}), 400
    if fields.get("title") == "":
        return jsonify({"error": "A recording needs a title"}), 400
    with db.tx() as conn:
        if fields.get("song_id") and not conn.execute("SELECT 1 FROM music.songs WHERE song_id = %s", (fields["song_id"],)).fetchone():
            return jsonify({"error": "Unknown song"}), 400
        if not conn.execute(f"UPDATE music.recordings SET {', '.join(f'{k} = %s' for k in fields)} WHERE recording_id = %s",
                            [*fields.values(), recording_id]).rowcount:
            return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/v1/music/albums/<album_id>", methods=["PUT"])
@admin_required
def album_write_v1(album_id):
    data = request.get_json(silent=True) or {}
    fields = {}
    if "title" in data and str(data["title"]).strip():
        fields["title"] = str(data["title"]).strip()[:200]
    if "release_year" in data:
        year = number(data["release_year"])
        fields["release_year"] = int(year) if year and 1900 <= year <= 2100 else None
    if "published" in data:
        fields["published"] = bool(data["published"])
    if not fields:
        return jsonify({"error": "Nothing to change"}), 400
    with db.tx() as conn:
        if not conn.execute(f"UPDATE music.albums SET {', '.join(f'{k} = %s' for k in fields)} WHERE album_id = %s",
                            [*fields.values(), album_id]).rowcount:
            return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})
