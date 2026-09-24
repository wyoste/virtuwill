"""Public content: blog posts, portfolio projects, page text, contact messages."""
import json
import os
import re
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from . import db, media
from .auth import admin_required, is_admin
from .util import in_calendar, number, parse_date

bp = Blueprint("content", __name__)
PROJECT_SEED = db.ROOT / "db" / "seed" / "portfolio_projects.json"


# ── Blog ─────────────────────────────────────────────────────────────────────

def _post(row):
    return {"id": row["post_id"], "title": row["title"], "body": row["body"], "excerpt": row["excerpt"],
            "thumbnail": media.url(row["thumbnail"]) or "", "published": row["published"],
            "date": row["post_date"].isoformat(), "author": row["author"]}


POSTS = """SELECT p.*, m.path AS thumbnail FROM content.blog_posts p
           LEFT JOIN core.media_assets m ON m.asset_id = p.thumbnail_asset_id"""


def posts(conn, include_drafts):
    where = "" if include_drafts else " WHERE p.published"
    return [_post(r) for r in conn.execute(POSTS + where + " ORDER BY p.post_date DESC, p.created_at DESC")]


def write_post(conn, post_id, data, day=None):
    thumb = media.asset_id(conn, data.get("thumbnail")) or (media.register(conn, data["thumbnail"])
                                                            if data.get("thumbnail") and os.path.isfile(media.STATIC / media.relpath(data["thumbnail"])) else None)
    conn.execute(
        """INSERT INTO content.blog_posts (post_id, title, body, excerpt, thumbnail_asset_id, author, published, post_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (post_id) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body, excerpt = EXCLUDED.excerpt,
               thumbnail_asset_id = EXCLUDED.thumbnail_asset_id, published = EXCLUDED.published, updated_at = now()""",
        (post_id, (data.get("title") or "Untitled").strip(), (data.get("body") or "").strip(), (data.get("excerpt") or "").strip(),
         thumb, data.get("author") or "Will Yoste", bool(data.get("published")), day or datetime.now().date()))


@bp.route("/api/blog")
def blog_list():
    with db.tx() as conn:
        return jsonify(posts(conn, is_admin()))


@bp.route("/api/blog", methods=["POST"])
@admin_required
def blog_create():
    post_id = str(uuid.uuid4())[:8]
    with db.tx() as conn:
        write_post(conn, post_id, request.get_json(force=True) or {})
        post = _post(conn.execute(POSTS + " WHERE p.post_id = %s", (post_id,)).fetchone())
    return jsonify({"ok": True, "post": post})


@bp.route("/api/blog/<post_id>", methods=["PUT"])
@admin_required
def blog_update(post_id):
    data = request.get_json(force=True) or {}
    with db.tx() as conn:
        row = conn.execute(POSTS + " WHERE p.post_id = %s", (post_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        current = _post(row)
        write_post(conn, post_id, {k: data.get(k, current[k]) for k in ("title", "body", "excerpt", "thumbnail", "published")}
                   | {"author": current["author"]}, row["post_date"])
        post = _post(conn.execute(POSTS + " WHERE p.post_id = %s", (post_id,)).fetchone())
    return jsonify({"ok": True, "post": post})


@bp.route("/api/blog/<post_id>", methods=["DELETE"])
@admin_required
def blog_delete(post_id):
    with db.tx() as conn:
        conn.execute("DELETE FROM content.blog_posts WHERE post_id = %s", (post_id,))
    return jsonify({"ok": True})


@bp.route("/api/blog/thumbnail", methods=["POST"])
@admin_required
def blog_thumbnail():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file"}), 400
    path = f"blog/{str(uuid.uuid4())[:8]}{os.path.splitext(f.filename)[1].lower() or '.jpg'}"
    with db.tx() as conn:
        media.save_upload(conn, f, path)
    return jsonify({"ok": True, "url": media.url(path)})


# ── Contact messages ─────────────────────────────────────────────────────────

def _message(row):
    return {"id": row["message_id"], "name": row["sender_name"], "contact": row["contact"], "message": row["body"],
            "date": row["received_on"].isoformat(), "read": row["is_read"]}


def add_message(conn, message_id, name, contact, body, day, read=False):
    conn.execute("""INSERT INTO content.messages (message_id, sender_name, contact, body, received_on, received_at, is_read)
                    VALUES (%s, %s, %s, %s, %s, now(), %s) ON CONFLICT (message_id) DO NOTHING""",
                 (message_id, name, contact, body, day, read))


@bp.route("/api/contact", methods=["POST"])
def contact_submit():
    data = request.get_json(force=True) or {}
    name, contact, body = (str(data.get(k, "")).strip() for k in ("name", "contact", "message"))
    if not name or not body:
        return jsonify({"error": "Name and message are required"}), 400
    if len(name) > 200 or len(contact) > 300 or len(body) > 10000:
        return jsonify({"error": "Message is too long"}), 400
    with db.tx() as conn:
        add_message(conn, str(uuid.uuid4()), name, contact, body, datetime.now().date())
    return jsonify({"ok": True})


@bp.route("/api/contact/messages")
@admin_required
def contact_messages():
    with db.tx() as conn:
        return jsonify([_message(r) for r in conn.execute(
            "SELECT * FROM content.messages ORDER BY received_on DESC, received_at DESC NULLS LAST")])


@bp.route("/api/contact/read/<msg_id>", methods=["POST"])
@admin_required
def contact_mark_read(msg_id):
    with db.tx() as conn:
        conn.execute("UPDATE content.messages SET is_read = true WHERE message_id = %s", (msg_id,))
    return jsonify({"ok": True})


# ── Portfolio ────────────────────────────────────────────────────────────────

def seed_projects(conn):
    """The built-in projects shown on the portfolio page."""
    for position, p in enumerate(json.loads(PROJECT_SEED.read_text(encoding="utf-8"))):
        inserted = conn.execute(
            """INSERT INTO content.portfolio_projects (project_id, title, tag, tag_class, description, subtitle, overview, chips,
                                                       is_builtin, visible, position)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s) ON CONFLICT (project_id) DO NOTHING""",
            (p["id"], p["title"], p.get("tag", "Project"), p.get("tagClass", "platform"), p.get("desc", ""),
             p.get("subtitle", ""), p.get("overview", ""), p.get("chips", []), p.get("visible", True), position)).rowcount
        if inserted:
            for i, m in enumerate(p.get("metrics", [])):
                conn.execute("INSERT INTO content.project_metrics VALUES (%s, %s, %s, %s)", (p["id"], i, m["val"], m["lbl"]))
            for i, t in enumerate(p.get("timeline", [])):
                conn.execute("INSERT INTO content.project_timeline VALUES (%s, %s, %s, %s, %s)",
                             (p["id"], i, t["period"], t["phase"], t.get("desc", "")))


def uploads(conn):
    return [{"id": r["project_id"], "filename": os.path.basename(r["path"] or ""), "title": r["title"], "tag": r["tag"],
             "desc": r["description"], "url": media.url(r["path"]), "uploaded": r["uploaded_on"].isoformat() if r["uploaded_on"] else "",
             "visible": r["visible"]}
            for r in conn.execute("""SELECT p.*, m.path FROM content.portfolio_projects p
                                     JOIN core.media_assets m ON m.asset_id = p.html_asset_id
                                     WHERE NOT p.is_builtin AND NOT p.removed ORDER BY p.uploaded_on DESC NULLS LAST, p.position""")]


def add_upload(conn, project_id, path, title, tag, desc, day, visible=True):
    asset = media.asset_id(conn, path) or media.register(conn, path)
    conn.execute(
        """INSERT INTO content.portfolio_projects (project_id, title, tag, description, html_asset_id, visible, removed, uploaded_on)
           VALUES (%s, %s, %s, %s, %s, %s, false, %s)
           ON CONFLICT (project_id) DO UPDATE SET title = EXCLUDED.title, tag = EXCLUDED.tag, description = EXCLUDED.description,
               html_asset_id = EXCLUDED.html_asset_id, removed = false, uploaded_on = EXCLUDED.uploaded_on""",
        (project_id, title, tag, desc, asset, visible, day if in_calendar(day) else None))


def layout(conn):
    """{project id: {visible, deleted}}, the shape the portfolio page stores."""
    return {r["project_id"]: {"visible": r["visible"], "deleted": r["removed"]}
            for r in conn.execute("SELECT project_id, visible, removed FROM content.portfolio_projects WHERE is_builtin")}


def set_layout(conn, state):
    for project_id, choice in (state or {}).items():
        if isinstance(choice, dict):
            conn.execute("UPDATE content.portfolio_projects SET visible = %s, removed = %s WHERE project_id = %s AND is_builtin",
                         (bool(choice.get("visible", True)), bool(choice.get("deleted")), project_id))


@bp.route("/api/portfolio/uploads")
def portfolio_list():
    with db.tx() as conn:
        return jsonify(uploads(conn))


@bp.route("/api/portfolio/upload", methods=["POST"])
@admin_required
def portfolio_upload():
    f = request.files.get("file")
    if not f or not f.filename or not f.filename.lower().endswith(".html"):
        return jsonify({"error": "Only .html files accepted"}), 400
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", f.filename)
    project_id = "up_" + safe.replace(".", "_")
    with db.tx() as conn:
        media.save_upload(conn, f, f"portfolio/{safe}")
        add_upload(conn, project_id, f"portfolio/{safe}", request.form.get("title", safe.replace(".html", "").replace("_", " ").title()),
                   request.form.get("tag", "Project"), request.form.get("desc", ""), datetime.now().date())
    return jsonify({"ok": True, "url": f"/static/portfolio/{safe}", "id": project_id})


@bp.route("/api/portfolio/upload/<filename>", methods=["DELETE"])
@admin_required
def portfolio_delete(filename):
    path = f"portfolio/{os.path.basename(filename)}"
    with db.tx() as conn:
        conn.execute("""DELETE FROM content.portfolio_projects
                        WHERE html_asset_id = (SELECT asset_id FROM core.media_assets WHERE path = %s)""", (path,))
        media.delete(conn, path)
    return jsonify({"ok": True})


# ── Page text ────────────────────────────────────────────────────────────────

def site_text(conn, key):
    row = conn.execute("SELECT value FROM content.site_text WHERE text_key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_site_text(conn, key, value):
    conn.execute("""INSERT INTO content.site_text (text_key, value) VALUES (%s, %s)
                    ON CONFLICT (text_key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (key, value))


def import_posts(conn, items):
    for p in items or []:
        if p.get("id") and p.get("title") is not None:
            day = parse_date(p.get("date"))
            write_post(conn, str(p["id"]), p, day if in_calendar(day) else None)


def import_messages(conn, items):
    for m in items or []:
        day = parse_date(m.get("date"))
        if m.get("message"):
            add_message(conn, str(m.get("id") or uuid.uuid4()), m.get("name") or "", m.get("contact") or "", m["message"],
                        day if in_calendar(day) else datetime.now().date(), bool(m.get("read")))


def import_uploads(conn, items):
    for u in items or []:
        if u.get("url") and os.path.isfile(media.STATIC / media.relpath(u["url"])) or media.asset_id(conn, u.get("url")):
            add_upload(conn, u.get("id") or "up_" + os.path.basename(u["url"]).replace(".", "_"), u["url"], u.get("title") or "Project",
                       u.get("tag") or "Project", u.get("desc") or "", parse_date(u.get("uploaded")), u.get("visible", True))


# ── Workspace and public API (v1) ────────────────────────────────────────────

def _project(conn, row, detail=False):
    p = {"id": row["project_id"], "title": row["title"], "tag": row["tag"], "tag_class": row["tag_class"],
         "description": row["description"], "subtitle": row["subtitle"], "chips": row["chips"] or [],
         "builtin": row["is_builtin"], "visible": row["visible"], "position": row["position"],
         "url": media.url(row["path"]) if row["path"] else None,
         "uploaded_on": row["uploaded_on"].isoformat() if row["uploaded_on"] else None, "role_id": row["role_id"]}
    if detail:
        p["overview"] = row["overview"]
        p["metrics"] = [{"value": m["value"], "label": m["label"]} for m in conn.execute(
            "SELECT value, label FROM content.project_metrics WHERE project_id = %s ORDER BY position", (row["project_id"],))]
        p["timeline"] = [{"period": t["period"], "phase": t["phase"], "description": t["description"]} for t in conn.execute(
            "SELECT * FROM content.project_timeline WHERE project_id = %s ORDER BY position", (row["project_id"],))]
    return p


PROJECTS = """SELECT p.*, m.path FROM content.portfolio_projects p
              LEFT JOIN core.media_assets m ON m.asset_id = p.html_asset_id WHERE NOT p.removed"""


@bp.route("/api/v1/projects")
def projects_v1():
    owner = is_admin() and request.args.get("view") == "owner"
    with db.tx() as conn:
        rows = conn.execute(PROJECTS + ("" if owner else " AND p.visible") +
                            " ORDER BY p.position NULLS LAST, p.uploaded_on DESC NULLS LAST, p.title").fetchall()
        return jsonify([_project(conn, r, detail=True) for r in rows])


@bp.route("/api/v1/projects/<project_id>")
def project_v1(project_id):
    owner = is_admin()
    with db.tx() as conn:
        row = conn.execute(PROJECTS + " AND p.project_id = %s" + ("" if owner else " AND p.visible"), (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_project(conn, row, detail=True))


@bp.route("/api/v1/projects/<project_id>", methods=["PUT", "DELETE"])
@admin_required
def project_write_v1(project_id):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM content.portfolio_projects WHERE project_id = %s", (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        if request.method == "DELETE":
            # Built-in projects are hidden for good; uploaded ones are deleted with their file.
            if row["is_builtin"]:
                conn.execute("UPDATE content.portfolio_projects SET removed = true WHERE project_id = %s", (project_id,))
            else:
                path = media.path_of(conn, row["html_asset_id"])
                conn.execute("DELETE FROM content.portfolio_projects WHERE project_id = %s", (project_id,))
                if path:
                    media.delete(conn, path)
            return jsonify({"ok": True})
        data = request.get_json(silent=True) or {}
        fields = {}
        for key, column in (("title", "title"), ("tag", "tag"), ("description", "description"), ("subtitle", "subtitle"),
                            ("overview", "overview")):
            if key in data:
                fields[column] = str(data[key] or "").strip()[:5000]
        if "chips" in data and isinstance(data["chips"], list):
            fields["chips"] = [str(c).strip()[:60] for c in data["chips"] if str(c).strip()][:20]
        if "visible" in data:
            fields["visible"] = bool(data["visible"])
        if "role_id" in data:
            role = number(data["role_id"]) if data["role_id"] not in (None, "") else None
            if role is not None and not conn.execute("SELECT 1 FROM career.roles WHERE role_id = %s", (int(role),)).fetchone():
                return jsonify({"error": "No such role"}), 400
            fields["role_id"] = int(role) if role is not None else None
        if "position" in data:
            fields["position"] = int(number(data["position"])) if number(data["position"]) is not None else None
        if fields.get("title") == "":
            return jsonify({"error": "A project needs a title"}), 400
        if not fields:
            return jsonify({"error": "Nothing to change"}), 400
        conn.execute(f"UPDATE content.portfolio_projects SET {', '.join(f'{k} = %s' for k in fields)} WHERE project_id = %s",
                     [*fields.values(), project_id])
        row = conn.execute(PROJECTS + " AND p.project_id = %s", (project_id,)).fetchone()
        return jsonify(_project(conn, row, detail=True))


@bp.route("/api/v1/posts")
def posts_v1():
    with db.tx() as conn:
        return jsonify(posts(conn, is_admin() and request.args.get("view") == "owner"))


@bp.route("/api/v1/posts/<post_id>")
def post_v1(post_id):
    with db.tx() as conn:
        row = conn.execute(POSTS + " WHERE p.post_id = %s" + ("" if is_admin() else " AND p.published"), (post_id,)).fetchone()
        return jsonify(_post(row)) if row else (jsonify({"error": "Not found"}), 404)


@bp.route("/api/v1/site-text/<key>", methods=["GET", "PUT"])
def site_text_v1(key):
    if key not in SITE_TEXT_KEYS:
        return jsonify({"error": "Unknown text"}), 404
    with db.tx() as conn:
        if request.method == "PUT":
            if not is_admin():
                return jsonify({"error": "Unauthorized"}), 401
            value = (request.get_json(silent=True) or {}).get("value")
            if not isinstance(value, str) or len(value) > 20000:
                return jsonify({"error": "Expected text under 20,000 characters"}), 400
            set_site_text(conn, key, value)
        return jsonify({"key": key, "value": site_text(conn, key)})


SITE_TEXT_KEYS = {"garden.gallery_note", "garden.hero", "home.intro"}
