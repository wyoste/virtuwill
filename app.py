"""
app.py — VirtuWill Flask application
=====================================
Single-page application served via templates/index.html.
All page routing and UI logic lives in the frontend JS modules.
Flask provides JSON API endpoints only.

Data fallback hierarchy:
  1. data/journal_entries.json  (persisted user data — auto-created on first write)
  2. mock_data/journal_entries.json  (bundled sample entries — no config needed)
"""

import json
import os
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

DATA_DIR = Path(__file__).parent / "data"
MOCK_DIR = Path(__file__).parent / "mock_data"


# ── Data helpers ───────────────────────────────────────────────────────────────

def _load(live_path, mock_filename, default):
    """Load JSON from live path, fall back to mock, then to default."""
    for path in [Path(live_path), MOCK_DIR / mock_filename]:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save(filename, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / filename).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_journal():
    return _load("data/journal_entries.json", "journal_entries.json", [])


def save_journal(entries):
    _save("journal_entries.json", entries)


def load_music():
    return _load("mock_data/music_library.json", "music_library.json", [])


def load_garden():
    return _load("data/garden.json", "garden.json", {"beds": []})


def save_garden(data):
    _save("garden.json", data)


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
    return jsonify(load_journal())


@app.route("/api/journal/entry", methods=["POST"])
@journal_required
def journal_save_entry():
    """
    Create or update (upsert) a journal entry.

    The frontend sends camelCase field names — quoteAuthor, freeWrite, meals.{B,L,D}.
    We store in the same camelCase format so reads and writes are symmetric.
    """
    data    = request.get_json(force=True) or {}
    entries = load_journal()

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

    # Upsert: update in place if ID exists, else prepend and sort
    idx = next((i for i, e in enumerate(entries) if e.get("id") == entry["id"]), None)
    if idx is not None:
        entries[idx] = entry
        was_new = False
    else:
        entries.insert(0, entry)
        entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        was_new = True

    save_journal(entries)
    return jsonify({"ok": True, "entry": entry, "wasNew": was_new}), 201 if was_new else 200


@app.route("/api/journal/entry/<entry_id>", methods=["DELETE"])
@journal_required
def journal_delete_entry(entry_id):
    entries     = load_journal()
    new_entries = [e for e in entries if e.get("id") != entry_id]
    if len(new_entries) == len(entries):
        return jsonify({"error": "Entry not found"}), 404
    save_journal(new_entries)
    return jsonify({"ok": True})


@app.route("/api/journal/check/<date_str>")
@journal_required
def journal_check_date(date_str):
    entries = load_journal()
    return jsonify({"date": date_str, "exists": any(e.get("date") == date_str for e in entries)})


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
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_logged_in", None)
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
# Tracks stored in data/music_catalog.json (admin-managed via music.js)
# Audio files saved to static/audio/
# Gallery photos saved to static/music/photos/

MUSIC_CATALOG_FILE = DATA_DIR / "music_catalog.json"
MUSIC_AUDIO_DIR    = Path(__file__).parent / "static" / "audio"
MUSIC_PHOTOS_DIR   = Path(__file__).parent / "static" / "music" / "photos"

def load_music_catalog():
    try:
        with open(MUSIC_CATALOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tracks": []}

def save_music_catalog(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(MUSIC_CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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
        f.save(os.path.join(dest_dir, safe_name))
        audio_url = f"/static/audio/{safe_album}/{safe_name}"

        # Save album art alongside the audio if provided
        art_url = None
        if art and art.filename:
            ext     = os.path.splitext(art.filename)[1].lower() or ".jpg"
            art_fn  = safe_album + ext          # art named same as album folder
            art.save(os.path.join(MUSIC_AUDIO_DIR, art_fn))
            art_url = f"/static/audio/{art_fn}"
    else:
        # Single — save at root
        os.makedirs(MUSIC_AUDIO_DIR, exist_ok=True)
        f.save(os.path.join(MUSIC_AUDIO_DIR, safe_name))
        audio_url = f"/static/audio/{safe_name}"

        # Save art with same stem as the mp3
        art_url = None
        if art and art.filename:
            stem    = os.path.splitext(safe_name)[0]
            ext     = os.path.splitext(art.filename)[1].lower() or ".jpg"
            art_fn  = stem + ext
            art.save(os.path.join(MUSIC_AUDIO_DIR, art_fn))
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
    f.save(os.path.join(MUSIC_PHOTOS_DIR, safe))
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
MESSAGES_FILE = "data/messages.json"

def _load_messages():
    try:
        with open(MESSAGES_FILE) as f: return json.load(f)
    except: return []

def _save_messages(data):
    os.makedirs("data", exist_ok=True)
    with open(MESSAGES_FILE, "w") as f: json.dump(data, f, indent=2)

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
PORTFOLIO_META = "data/portfolio_uploads.json"

def _load_portfolio_uploads():
    try:
        with open(PORTFOLIO_META) as f: return json.load(f)
    except: return []

def _save_portfolio_uploads(data):
    os.makedirs("data", exist_ok=True)
    with open(PORTFOLIO_META, "w") as f: json.dump(data, f, indent=2)

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
    f.save(path)
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
    try: os.remove(os.path.join(PORTFOLIO_DIR, filename))
    except: pass
    return jsonify({"ok": True})


# ── Blog / Newsletter API ─────────────────────────────────────────────────────
import uuid as _uuid

BLOG_FILE = "data/blog.json"
BLOG_DIR  = "static/blog"

def _load_blog():
    try:
        with open(BLOG_FILE) as f: return json.load(f)
    except: return []

def _save_blog(posts):
    os.makedirs("data", exist_ok=True)
    with open(BLOG_FILE, "w") as f: json.dump(posts, f, indent=2)

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
    f.save(os.path.join(BLOG_DIR, name))
    return jsonify({"ok": True, "url": f"/static/blog/{name}"})



# ── Garden photos API ──────────────────────────────────────────────────────────

GARDEN_PHOTOS_DIR  = "static/garden/photos"
GARDEN_PHOTOS_META = "data/garden_photos.json"

def _load_garden_photos():
    return _load(GARDEN_PHOTOS_META, "garden_photos.json", [])

def _save_garden_photos(data):
    _save("garden_photos.json", data)


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
        f.save(os.path.join(GARDEN_PHOTOS_DIR, name))
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
        try:
            fname = os.path.basename(target["url"])
            os.remove(os.path.join(GARDEN_PHOTOS_DIR, fname))
        except Exception:
            pass
        photos = [p for p in photos if p["id"] != photo_id]
        _save_garden_photos(photos)
    return jsonify({"ok": True})


# ── Accounts template API ─────────────────────────────────────────────────────

def _load_acct_template():
    return _load("data/accounts_template.json", "accounts_template.json", [])

def _save_acct_template(data):
    _save("accounts_template.json", data)


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


# ── Run ────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    port = int(os.getenv("DATABRICKS_APP_PORT", 5000))
    app.run(
        debug=config.FLASK_DEBUG,
        host="0.0.0.0",
        port=port
    )
