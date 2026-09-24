"""
app.py — VirtuWill Flask application
=====================================
Single-page application served via templates/index.html.
All page routing and UI logic lives in the frontend JS modules.
Flask provides JSON API endpoints only.

Persistence goes through storage.py: one data model stored in Lakebase
(Databricks-managed Postgres) when a database resource is attached, or in
data/*.json and static/ files during local development.
"""

import json
import os
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session

import config
import storage

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Trackers use private storage, with no fallback to public mock data.
from trackers import bp as trackers_blueprint
app.config["TRACKER_DATA_DIR"] = os.environ.get("TRACKER_DATA_DIR", str(Path(__file__).parent / "data/private-trackers"))
app.config["TRACKER_AUTH_CONFIGURED"] = (
    config.SECRET_KEY != "dev-secret-change-in-production"
    and len(config.SECRET_KEY) >= 32
    and config.ADMIN_PASSWORD != "virtuwill2026"
    and len(config.ADMIN_PASSWORD) >= 12
)
app.register_blueprint(trackers_blueprint)

DATA_DIR = Path(__file__).parent / "data"
MOCK_DIR = Path(__file__).parent / "mock_data"


@app.errorhandler(storage.StorageUnavailable)
def storage_unavailable(error):
    return jsonify({"error": "The database is unavailable. Try again shortly."}), 503


# Uploads are kept in the database; serve any not yet restored to static/.
_serve_static = app.view_functions["static"]


def _static_or_stored(filename):
    from werkzeug.exceptions import NotFound
    try:
        return _serve_static(filename=filename)
    except NotFound:
        media = storage.get_media(filename)
        if not media:
            raise
        from flask import Response
        return Response(media[1], mimetype=media[0])


app.view_functions["static"] = _static_or_stored


def _restore_media():
    try:
        count = storage.restore_media()
        if count:
            app.logger.info("Restored %d uploaded files from the database", count)
    except Exception:
        app.logger.exception("Could not restore uploaded files")


if storage.backend().name == "lakebase":
    import threading
    threading.Thread(target=_restore_media, daemon=True).start()


# ── Data helpers ───────────────────────────────────────────────────────────────

def load_music():
    # Bundled, read-only sample library.
    try:
        return json.loads((MOCK_DIR / "music_library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def load_garden():
    return storage.load("garden")


def save_garden(data):
    storage.save("garden", data)


# ── Auth decorator ─────────────────────────────────────────────────────────────

def journal_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("journal_unlocked"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ── SPA entry point ────────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def index(path):
    if path.startswith("static/"):
        from flask import abort
        abort(404)
    if session.get("admin_logged_in"):
        import secrets
        session.setdefault("tracker_csrf", secrets.token_urlsafe(32))
    return render_template(
        "index.html",
        journal_unlocked=session.get("journal_unlocked", False),
        admin_logged_in=session.get("admin_logged_in", False),
    )


# ── Journal API ────────────────────────────────────────────────────────────────

@app.route("/api/journal/unlock", methods=["POST"])
def journal_unlock():
    data = request.get_json(force=True) or {}
    if data.get("password") == config.JOURNAL_PASSWORD:
        session["journal_unlocked"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect passphrase"}), 401


@app.route("/api/journal/logout", methods=["POST"])
def journal_logout():
    session.pop("journal_unlocked", None)
    return jsonify({"ok": True})


@app.route("/api/journal/status")
def journal_status():
    return jsonify({"unlocked": session.get("journal_unlocked", False)})


@app.route("/api/journal/entries")
@journal_required
def journal_entries():
    return jsonify(storage.journal_list())


@app.route("/api/journal/entry", methods=["POST"])
@journal_required
def journal_save_entry():
    """
    Create or update (upsert) a journal entry.

    The frontend sends camelCase field names — quoteAuthor, freeWrite, meals.{B,L,D}.
    We store in the same camelCase format so reads and writes are symmetric.
    """
    data    = request.get_json(force=True) or {}

    entry = {
        "id":          data.get("id") or str(uuid.uuid4()),
        "date":        data.get("date", datetime.now().strftime("%Y-%m-%d")),
        "quote":       (data.get("quote") or "").strip(),
        "quoteAuthor": (data.get("quoteAuthor") or "").strip(),
        "meals": {
            "B": (data.get("meals", {}).get("B") or "").strip(),
            "L": (data.get("meals", {}).get("L") or "").strip(),
            "D": (data.get("meals", {}).get("D") or "").strip(),
        },
        "freeWrite": data.get("freeWrite") or "",
        "habits":    data.get("habits") or {},
        "tags":      data.get("tags") or [],
        "accounts":  data.get("accounts") or [],
        "source":    data.get("source", "manual"),
        "createdAt": data.get("createdAt", datetime.utcnow().isoformat() + "Z"),
    }

    try:
        datetime.strptime(entry["date"], "%Y-%m-%d")
    except (TypeError, ValueError):
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    # Upsert: one row per entry in Lakebase (or the JSON file locally).
    was_new = storage.journal_upsert(entry)
    return jsonify({"ok": True, "entry": entry, "wasNew": was_new}), 201 if was_new else 200


@app.route("/api/journal/entry/<entry_id>", methods=["DELETE"])
@journal_required
def journal_delete_entry(entry_id):
    if not storage.journal_delete(entry_id):
        return jsonify({"error": "Entry not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/journal/check/<date_str>")
@journal_required
def journal_check_date(date_str):
    return jsonify({"date": date_str, "exists": storage.journal_date_exists(date_str)})


@app.route("/api/journal/ocr", methods=["POST"])
@journal_required
def journal_ocr():
    """
    Proxy a base64 journal image to Claude Vision and return structured JSON.
    The Anthropic API key stays server-side — never sent to the browser.
    """
    try:
        import anthropic

        if not config.ANTHROPIC_API_KEY:
            return jsonify({"error": "ANTHROPIC_API_KEY is not configured in .env"}), 500

        data = request.get_json(force=True) or {}
        b64  = data.get("base64")
        mime = data.get("mediaType", "image/jpeg")

        if not b64:
            return jsonify({"error": "No image data provided"}), 400

        supported = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        safe_mime = mime if mime in supported else "image/jpeg"

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg    = client.messages.create(
            model      = "claude-opus-4-6",
            max_tokens = 1024,
            messages   = [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": safe_mime, "data": b64}},
                    {
                        "type": "text",
                        "text": (
                            "Transcribe this handwritten journal page. "
                            "Return ONLY a valid JSON object — no markdown fences — with these fields: "
                            "date (YYYY-MM-DD or empty string), quote (string), quoteAuthor (string), "
                            "meals (object with keys B, L, D — each a string), freeWrite (string). "
                            "Use empty string for any field not present."
                        ),
                    },
                ],
            }],
        )

        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

        return jsonify({"ok": True, "data": json.loads(raw.strip())})

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Could not parse Claude's response as JSON: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Admin auth API ─────────────────────────────────────────────────────────────

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True) or {}
    admin_user = config.ADMIN_USER
    admin_pass = config.ADMIN_PASSWORD
    if data.get("username") == admin_user and data.get("password") == admin_pass:
        session["admin_logged_in"] = True
        import secrets
        session["tracker_csrf"] = secrets.token_urlsafe(32)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_logged_in", None)
    session.pop("tracker_csrf", None)
    return jsonify({"ok": True})


@app.route("/api/admin/status")
def admin_status():
    return jsonify({"logged_in": session.get("admin_logged_in", False)})


# ── Garden API ─────────────────────────────────────────────────────────────────

@app.route("/api/garden")
def garden_get():
    return jsonify(load_garden())


@app.route("/api/garden", methods=["POST"])
def garden_save():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(force=True) or {}
    save_garden(data)
    return jsonify({"ok": True})



@app.route("/resume/download")
def resume_download():
    """Serve resume PDF for download."""
    from flask import send_from_directory
    return send_from_directory("static", "will_yoste_resume.pdf",
                               as_attachment=True,
                               download_name="Will_Yoste_Resume.pdf")


# ── Music catalog API ─────────────────────────────────────────────────────────
# Tracks stored in the music_catalog collection (admin-managed via music.js)
# Audio files saved to static/audio/ (and the database media table)
# Gallery photos saved to static/music/photos/

MUSIC_AUDIO_DIR    = Path(__file__).parent / "static" / "audio"
MUSIC_PHOTOS_DIR   = Path(__file__).parent / "static" / "music" / "photos"

def load_music_catalog():
    return storage.load("music_catalog")

def save_music_catalog(data):
    storage.save("music_catalog", data)

@app.route("/api/music/catalog")
def music_catalog_get():
    return jsonify(load_music_catalog())

@app.route("/api/music/catalog", methods=["POST"])
def music_catalog_save():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(force=True) or {}
    save_music_catalog(data)
    return jsonify({"ok": True})

@app.route("/api/music/upload", methods=["POST"])
def music_upload():
    """Upload an audio file (mp3/wav) and optional album art.

    Form fields:
      file        — the audio file
      album       — optional album/folder name (empty = root single)
      art         — optional image file (album art)
    """
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    import re as _re

    f     = request.files["file"]
    album = request.form.get("album", "").strip()
    art   = request.files.get("art")

    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    safe_name = _re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename)

    if album:
        # Album track — save into a subfolder
        safe_album = _re.sub(r"[^a-zA-Z0-9 ._-]", "_", album)
        dest_dir   = os.path.join(MUSIC_AUDIO_DIR, safe_album)
        os.makedirs(dest_dir, exist_ok=True)
        storage.save_upload(f, os.path.join(dest_dir, safe_name))
        audio_url = f"/static/audio/{safe_album}/{safe_name}"

        # Save album art alongside the audio if provided
        art_url = None
        if art and art.filename:
            ext     = os.path.splitext(art.filename)[1].lower() or ".jpg"
            art_fn  = safe_album + ext          # art named same as album folder
            storage.save_upload(art, os.path.join(MUSIC_AUDIO_DIR, art_fn))
            art_url = f"/static/audio/{art_fn}"
    else:
        # Single — save at root
        os.makedirs(MUSIC_AUDIO_DIR, exist_ok=True)
        storage.save_upload(f, os.path.join(MUSIC_AUDIO_DIR, safe_name))
        audio_url = f"/static/audio/{safe_name}"

        # Save art with same stem as the mp3
        art_url = None
        if art and art.filename:
            stem    = os.path.splitext(safe_name)[0]
            ext     = os.path.splitext(art.filename)[1].lower() or ".jpg"
            art_fn  = stem + ext
            storage.save_upload(art, os.path.join(MUSIC_AUDIO_DIR, art_fn))
            art_url = f"/static/audio/{art_fn}"

    return jsonify({"ok": True, "url": audio_url, "art": art_url})


@app.route("/api/music/library")
def music_library_scan():
    """Scan static/audio/ and return the album/single structure.

    Albums:  subdirectories containing mp3/wav files.
    Singles: mp3/wav files at the root level.
    Art:     image file with the same stem as the folder or mp3.
    """
    import glob, re as _re

    audio_exts = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    base = MUSIC_AUDIO_DIR
    if not os.path.isdir(base):
        return jsonify({"albums": [], "singles": []})

    def _find_art(stem, search_dir):
        """Look for image_stem.* in search_dir."""
        for ext in image_exts:
            p = os.path.join(search_dir, stem + ext)
            if os.path.isfile(p):
                rel = os.path.relpath(p, Path(__file__).parent / "static")
                return "/static/" + rel.replace("\\", "/")
        return None

    albums  = []
    singles = []

    entries = sorted(os.listdir(base))

    for entry in entries:
        full = os.path.join(base, entry)

        if os.path.isdir(full):
            # ── Album folder ────────────────────────────────────
            tracks = sorted([
                e for e in os.listdir(full)
                if os.path.isfile(os.path.join(full, e))
                and os.path.splitext(e)[1].lower() in audio_exts
            ])
            if not tracks:
                continue
            stem    = entry   # folder name is the album name stem
            art_url = _find_art(stem, base)         # art at root level: AlbumName.jpg
            if not art_url:
                art_url = _find_art(stem, full)      # art inside folder: AlbumName/AlbumName.jpg
            if not art_url:
                art_url = _find_art("cover", full)  # or cover.jpg inside the folder

            track_list = []
            for tfile in tracks:
                tstem    = os.path.splitext(tfile)[0]
                rel_url  = f"/static/audio/{entry}/{tfile}"
                # Per-track art: look for StemName.jpg inside folder
                track_art = _find_art(tstem, full)
                track_list.append({"title": tstem.replace("_", " "), "url": rel_url, "art": track_art})

            albums.append({
                "type":   "album",
                "name":   entry,
                "art":    art_url,
                "tracks": track_list,
            })

        else:
            # ── Root-level file ─────────────────────────────────
            ext = os.path.splitext(entry)[1].lower()
            if ext not in audio_exts:
                continue
            stem    = os.path.splitext(entry)[0]
            art_url = _find_art(stem, base)
            singles.append({
                "type":  "single",
                "name":  stem.replace("_", " "),
                "url":   f"/static/audio/{entry}",
                "art":   art_url,
            })

    return jsonify({"albums": albums, "singles": singles})

@app.route("/api/music/photo", methods=["POST"])
def music_photo_upload():
    """Upload a gallery photo for the music section."""
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    import re as _re
    safe = _re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename)
    os.makedirs(MUSIC_PHOTOS_DIR, exist_ok=True)
    storage.save_upload(f, os.path.join(MUSIC_PHOTOS_DIR, safe))
    return jsonify({"ok": True, "url": f"/static/music/photos/{safe}"})

@app.route("/api/music/photos")
def music_get_photos():
    import glob
    photos = [
        f"/static/music/photos/{os.path.basename(p)}"
        for p in glob.glob(f"{MUSIC_PHOTOS_DIR}/*")
        if p.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
    ]
    return jsonify(photos)

@app.route("/api/music")
def api_music():
    """Legacy endpoint — returns mock library data."""
    return jsonify(load_music())


# ── Contact form API ──────────────────────────────────────────────────────────

def _load_messages():
    return storage.load("messages")

def _save_messages(data):
    storage.save("messages", data)

@app.route("/api/contact", methods=["POST"])
def contact_submit():
    data = request.get_json(force=True) or {}
    name    = data.get("name","").strip()
    contact = data.get("contact","").strip()
    message = data.get("message","").strip()
    if not name or not message:
        return jsonify({"error": "Name and message are required"}), 400
    msgs = _load_messages()
    msgs.insert(0, {
        "id": str(len(msgs)+1),
        "name": name,
        "contact": contact,
        "message": message,
        "date": __import__("datetime").datetime.utcnow().isoformat()[:10],
        "read": False,
    })
    _save_messages(msgs)
    return jsonify({"ok": True})

@app.route("/api/contact/messages")
def contact_messages():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(_load_messages())

@app.route("/api/contact/read/<msg_id>", methods=["POST"])
def contact_mark_read(msg_id):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    msgs = _load_messages()
    for m in msgs:
        if m.get("id") == msg_id: m["read"] = True
    _save_messages(msgs)
    return jsonify({"ok": True})

# ── Portfolio HTML upload API ─────────────────────────────────────────────────
PORTFOLIO_DIR  = "static/portfolio"

def _load_portfolio_uploads():
    return storage.load("portfolio_uploads")

def _save_portfolio_uploads(data):
    storage.save("portfolio_uploads", data)

@app.route("/api/portfolio/upload", methods=["POST"])
def portfolio_upload():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".html"):
        return jsonify({"error": "Only .html files accepted"}), 400
    import re as _re
    safe = _re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename)
    os.makedirs(PORTFOLIO_DIR, exist_ok=True)
    path = os.path.join(PORTFOLIO_DIR, safe)
    storage.save_upload(f, path)
    # Build metadata
    title   = request.form.get("title", safe.replace(".html","").replace("_"," ").title())
    tag     = request.form.get("tag",  "Project")
    desc    = request.form.get("desc", "")
    uploads = _load_portfolio_uploads()
    uploads = [u for u in uploads if u.get("filename") != safe]  # replace if re-uploading
    uploads.insert(0, {
        "id":       "up_" + safe.replace(".","_"),
        "filename": safe,
        "title":    title,
        "tag":      tag,
        "desc":     desc,
        "url":      f"/static/portfolio/{safe}",
        "uploaded": __import__("datetime").datetime.utcnow().isoformat()[:10],
        "visible":  True,
    })
    _save_portfolio_uploads(uploads)
    return jsonify({"ok": True, "url": f"/static/portfolio/{safe}", "id": "up_" + safe.replace(".","_")})

@app.route("/api/portfolio/uploads")
def portfolio_uploads_list():
    return jsonify(_load_portfolio_uploads())

@app.route("/api/portfolio/upload/<filename>", methods=["DELETE"])
def portfolio_upload_delete(filename):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    uploads = _load_portfolio_uploads()
    uploads = [u for u in uploads if u.get("filename") != filename]
    _save_portfolio_uploads(uploads)
    storage.delete_upload(os.path.join(PORTFOLIO_DIR, os.path.basename(filename)))
    return jsonify({"ok": True})


# ── Blog / Newsletter API ─────────────────────────────────────────────────────
import uuid as _uuid

BLOG_DIR  = "static/blog"

def _load_blog():
    return storage.load("blog")

def _save_blog(posts):
    storage.save("blog", posts)

@app.route("/api/blog")
def blog_list():
    posts = _load_blog()
    is_admin = session.get("admin_logged_in", False)
    # Visitors only see published posts
    if not is_admin:
        posts = [p for p in posts if p.get("published")]
    return jsonify(posts)

@app.route("/api/blog", methods=["POST"])
def blog_create():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    data  = request.get_json(force=True) or {}
    posts = _load_blog()
    post  = {
        "id":        str(_uuid.uuid4())[:8],
        "title":     data.get("title", "Untitled").strip(),
        "body":      data.get("body", "").strip(),
        "excerpt":   data.get("excerpt", "").strip(),
        "thumbnail": data.get("thumbnail", ""),
        "published": data.get("published", False),
        "date":      __import__("datetime").datetime.utcnow().isoformat()[:10],
        "author":    "Will Yoste",
    }
    posts.insert(0, post)
    _save_blog(posts)
    return jsonify({"ok": True, "post": post})

@app.route("/api/blog/<post_id>", methods=["PUT"])
def blog_update(post_id):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    data  = request.get_json(force=True) or {}
    posts = _load_blog()
    post  = next((p for p in posts if p["id"] == post_id), None)
    if not post:
        return jsonify({"error": "Not found"}), 404
    post.update({
        "title":     data.get("title", post["title"]).strip(),
        "body":      data.get("body",  post["body"]).strip(),
        "excerpt":   data.get("excerpt", post.get("excerpt","")).strip(),
        "thumbnail": data.get("thumbnail", post.get("thumbnail","")),
        "published": data.get("published", post["published"]),
    })
    _save_blog(posts)
    return jsonify({"ok": True, "post": post})

@app.route("/api/blog/<post_id>", methods=["DELETE"])
def blog_delete(post_id):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    posts = _load_blog()
    posts = [p for p in posts if p["id"] != post_id]
    _save_blog(posts)
    return jsonify({"ok": True})

@app.route("/api/blog/thumbnail", methods=["POST"])
def blog_thumbnail():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f    = request.files["file"]
    ext  = os.path.splitext(f.filename)[1].lower() or ".jpg"
    name = str(_uuid.uuid4())[:8] + ext
    os.makedirs(BLOG_DIR, exist_ok=True)
    storage.save_upload(f, os.path.join(BLOG_DIR, name))
    return jsonify({"ok": True, "url": f"/static/blog/{name}"})



# ── Garden photos API ──────────────────────────────────────────────────────────

GARDEN_PHOTOS_DIR  = "static/garden/photos"

def _load_garden_photos():
    return storage.load("garden_photos")

def _save_garden_photos(data):
    storage.save("garden_photos", data)


@app.route("/api/garden/photos", methods=["GET"])
def garden_photos_get():
    return jsonify(_load_garden_photos())


@app.route("/api/garden/photo", methods=["POST"])
def garden_photo_upload():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    files  = request.files.getlist("files")
    beds   = request.form.getlist("beds")
    plants = request.form.getlist("plants")
    caption = request.form.get("caption", "").strip()
    category = request.form.get("category", "").strip()
    if not files:
        return jsonify({"error": "No files"}), 400
    os.makedirs(GARDEN_PHOTOS_DIR, exist_ok=True)
    photos = _load_garden_photos()
    added  = []
    for f in files:
        if not f.filename:
            continue
        ext  = os.path.splitext(f.filename)[1].lower() or ".jpg"
        name = str(_uuid.uuid4())[:12] + ext
        storage.save_upload(f, os.path.join(GARDEN_PHOTOS_DIR, name))
        entry = {
            "id":       str(_uuid.uuid4()),
            "url":      f"/static/garden/photos/{name}",
            "beds":     beds,
            "plants":   plants,
            "caption":  caption,
            "category": category,
            "date":     __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
        }
        photos.insert(0, entry)
        added.append(entry)
    _save_garden_photos(photos)
    return jsonify({"ok": True, "added": added})


@app.route("/api/garden/photo/<photo_id>", methods=["DELETE"])
def garden_photo_delete(photo_id):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    photos = _load_garden_photos()
    target = next((p for p in photos if p["id"] == photo_id), None)
    if target:
        # Remove file
        storage.delete_upload(os.path.join(GARDEN_PHOTOS_DIR, os.path.basename(target["url"])))
        photos = [p for p in photos if p["id"] != photo_id]
        _save_garden_photos(photos)
    return jsonify({"ok": True})


# ── Accounts template API ─────────────────────────────────────────────────────

def _load_acct_template():
    return storage.load("accounts_template")

def _save_acct_template(data):
    storage.save("accounts_template", data)


@app.route('/api/accounts-template', methods=['GET'])
def acct_template_get():
    return jsonify(_load_acct_template())


@app.route('/api/accounts-template', methods=['POST'])
def acct_template_post():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(force=True) or []
    _save_acct_template(data)
    return jsonify({'ok': True})


# ── Page settings formerly kept in browser storage ─────────────────────────────
# Travel pins/visited places, garden gallery text, and portfolio layout. Values
# are the strings the pages previously wrote to localStorage.
PAGE_DATA = {"travel_pins", "travel_visited", "garden_gallery_note", "garden_gallery_hero", "portfolio_layout"}


@app.route("/api/data/<name>", methods=["GET"])
def page_data_get(name):
    if name not in PAGE_DATA:
        return jsonify({"error": "Unknown data"}), 404
    return jsonify({"data": storage.load(name)})


@app.route("/api/data/<name>", methods=["PUT"])
def page_data_put(name):
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    if name not in PAGE_DATA:
        return jsonify({"error": "Unknown data"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str) or len(payload["data"]) > 1_000_000:
        return jsonify({"error": "Expected a data string under 1 MB"}), 400
    storage.save(name, payload["data"])
    return jsonify({"ok": True})


# ── Run ────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    port = int(os.getenv("DATABRICKS_APP_PORT", 5000))
    app.run(
        debug=config.FLASK_DEBUG,
        host="0.0.0.0",
        port=port
    )
