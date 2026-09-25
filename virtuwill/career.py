"""Career: the CV, the work ethos and the projects explorer, as one public page.

    GET  /api/v1/career                   everything the Career page shows (public)
    PUT  /api/v1/career/profile           name, headline, contact, ethos statement
    POST /api/v1/career/cv                upload a new CV (PDF); /resume/download serves it
    /api/v1/career/{roles,education,skill-groups,highlights}   owner record APIs
    POST/DELETE /api/v1/career/education/<id>/logo            a school's logo (image)

Projects stay in content.portfolio_projects; each may name the role it came from.
"""
import hashlib
import json

from flask import Blueprint, Response, jsonify, request, send_from_directory

from . import db, media, records
from .auth import admin_required, is_admin
from .records import Field as F
from .util import plain

bp = Blueprint("career", __name__)
SEED = db.ROOT / "db" / "seed" / "career.json"
PROFILE_FIELDS = {"full_name": 120, "headline": 160, "organization": 160, "location": 120, "email": 200, "phone": 40,
                  "linkedin_url": 300, "ethos_eyebrow": 80, "ethos_headline": 300, "ethos_summary": 2000}
SECTIONS = ("pillar", "impact", "strength", "certification")
MAX_CV = 10 * 1024 * 1024
MAX_LOGO = 2 * 1024 * 1024
LOGO_TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp", b"GIF8": ".gif"}


def seed(conn):
    """The CV as it stood when it moved out of the page's HTML; loaded once."""
    if not conn.execute("INSERT INTO virtuwill.migrations (name) VALUES ('career_seed_v1') ON CONFLICT DO NOTHING").rowcount:
        return
    data = json.loads(SEED.read_text(encoding="utf-8"))
    p = data["profile"]
    conn.execute(f"""INSERT INTO career.profile ({', '.join(PROFILE_FIELDS)}) VALUES ({', '.join(['%s'] * len(PROFILE_FIELDS))})
                     ON CONFLICT (profile_id) DO NOTHING""", [p.get(k, "") for k in PROFILE_FIELDS])
    positions = {}
    for h in data["highlights"]:
        positions[h["section"]] = positions.get(h["section"], -1) + 1
        conn.execute("INSERT INTO career.highlights (section, position, title, body, icon) VALUES (%s, %s, %s, %s, %s)",
                     (h["section"], positions[h["section"]], h["title"], h.get("body", ""), h.get("icon", "")))
    for r in data["roles"]:
        role_id = conn.execute("""INSERT INTO career.roles (slug, title, organization, location, started_on, ended_on, bullets, tags)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (slug) DO NOTHING RETURNING role_id""",
                               (r["slug"], r["title"], r["organization"], r.get("location", ""), r["started_on"], r.get("ended_on"),
                                r.get("bullets", []), r.get("tags", []))).fetchone()
        if role_id:
            conn.execute("UPDATE content.portfolio_projects SET role_id = %s WHERE project_id = ANY(%s) AND role_id IS NULL",
                         (role_id["role_id"], r.get("projects", [])))
    for i, g in enumerate(data["skill_groups"]):
        conn.execute("INSERT INTO career.skill_groups (label, skills, featured, position) VALUES (%s, %s, %s, %s)",
                     (g["label"], g["skills"], g.get("featured", []), i))
    for e in data["education"]:
        conn.execute("""INSERT INTO career.education (degree, field_of_study, school, location, finished_on, grade, highlight_label, highlight, logo_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (e["degree"], e.get("field_of_study", ""), e["school"], e.get("location", ""), e.get("finished_on"),
                      e.get("grade", ""), e.get("highlight_label", ""), e.get("highlight", ""), e.get("logo_path", "")))


def _projects(conn, owner):
    from .content import PROJECTS, _project
    rows = conn.execute(PROJECTS + ("" if owner else " AND p.visible") +
                        " ORDER BY p.position NULLS LAST, p.uploaded_on DESC NULLS LAST, p.title").fetchall()
    return [_project(conn, r, detail=True) for r in rows]


def page(conn, owner=False):
    profile = plain(conn.execute("SELECT * FROM career.profile WHERE profile_id = 1").fetchone() or {})
    profile["has_cv"] = True   # the bundled PDF, or an uploaded one
    profile["cv_uploaded"] = bool(profile.pop("cv_asset_id", None))
    hidden = "" if owner else " WHERE visible"
    highlights = {s: [] for s in SECTIONS}
    for h in conn.execute("SELECT * FROM career.highlights ORDER BY section, position, highlight_id"):
        highlights[h["section"]].append(plain(h))
    return {
        "profile": profile,
        "highlights": highlights,
        "roles": [plain(r) for r in conn.execute(f"SELECT * FROM career.roles{hidden} ORDER BY ended_on DESC NULLS FIRST, started_on DESC")],
        "education": [plain(r) | {"logo": media.url(r["logo_path"])}
                      for r in conn.execute(f"SELECT * FROM career.education{hidden} ORDER BY finished_on DESC NULLS FIRST")],
        "skills": [plain(r) for r in conn.execute("SELECT * FROM career.skill_groups ORDER BY position, group_id")],
        "projects": _projects(conn, owner),
    }


@bp.route("/api/v1/career")
def career():
    with db.tx() as conn:
        return jsonify(page(conn, owner=is_admin() and request.args.get("view") == "owner"))


@bp.route("/api/v1/career/profile", methods=["PUT"])
@admin_required
def profile_put():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    values = {}
    for key, limit in PROFILE_FIELDS.items():
        if key in data:
            text = str(data[key] or "").strip()
            if len(text) > limit:
                return jsonify({"error": f"{key} is too long"}), 400
            values[key] = text
    if values.get("full_name") == "":
        return jsonify({"error": "A name is required"}), 400
    if values.get("linkedin_url") and not values["linkedin_url"].startswith("https://"):
        return jsonify({"error": "linkedin_url must start with https://"}), 400
    if not values:
        return jsonify({"error": "Nothing to change"}), 400
    with db.tx() as conn:
        conn.execute("INSERT INTO career.profile (full_name) VALUES ('') ON CONFLICT DO NOTHING")
        conn.execute(f"UPDATE career.profile SET {', '.join(f'{k} = %s' for k in values)}, updated_at = now() WHERE profile_id = 1",
                     list(values.values()))
        return jsonify(page(conn, owner=True)["profile"])


@bp.route("/api/v1/career/cv", methods=["POST", "DELETE"])
@admin_required
def cv_upload():
    with db.tx() as conn:
        if request.method == "DELETE":
            conn.execute("UPDATE career.profile SET cv_asset_id = NULL WHERE profile_id = 1")
            return jsonify({"ok": True})
        f = request.files.get("file")
        content = f.read(MAX_CV + 1) if f else b""
        if not content.startswith(b"%PDF") or len(content) > MAX_CV:
            return jsonify({"error": "Choose a PDF under 10 MB"}), 400
        asset = media.register(conn, f"career/cv-{hashlib.sha256(content).hexdigest()[:12]}.pdf", content)
        conn.execute("UPDATE career.profile SET cv_asset_id = %s WHERE profile_id = 1", (asset,))
        return jsonify({"ok": True})


@bp.route("/api/v1/career/education/<int:education_id>/logo", methods=["POST", "DELETE"])
@admin_required
def education_logo(education_id):
    with db.tx() as conn:
        if not conn.execute("SELECT 1 FROM career.education WHERE education_id = %s", (education_id,)).fetchone():
            return jsonify({"error": "Not found"}), 404
        if request.method == "DELETE":
            conn.execute("UPDATE career.education SET logo_path = '' WHERE education_id = %s", (education_id,))
            return jsonify({"ok": True})
        f = request.files.get("file")
        content = f.read(MAX_LOGO + 1) if f else b""
        ext = next((e for magic, e in LOGO_TYPES.items() if content.startswith(magic)), None)
        if ext == ".webp" and content[8:12] != b"WEBP":
            ext = None
        if not ext or len(content) > MAX_LOGO:
            return jsonify({"error": "Choose a PNG, JPEG, WebP or GIF image under 2 MB"}), 400
        path = f"career/logos/upload-{hashlib.sha256(content).hexdigest()[:12]}{ext}"
        try:
            (media.STATIC / path).parent.mkdir(parents=True, exist_ok=True)
            (media.STATIC / path).write_bytes(content)
        except OSError:
            pass            # kept in the database; restored to disk on start
        media.register(conn, path, content)
        conn.execute("UPDATE career.education SET logo_path = %s WHERE education_id = %s", (path, education_id))
        return jsonify({"ok": True, "logo": media.url(path)})


def download():
    """/resume/download: the uploaded CV if there is one, else the bundled PDF."""
    name = "CV.pdf"
    try:
        with db.tx() as conn:
            row = conn.execute("""SELECT p.full_name, m.path FROM career.profile p
                                  LEFT JOIN core.media_assets m ON m.asset_id = p.cv_asset_id WHERE p.profile_id = 1""").fetchone()
        if row:
            name = (row["full_name"] or "CV").replace(" ", "_") + "_CV.pdf"
            stored = media.stored(row["path"]) if row["path"] else None
            if stored:
                return Response(stored[1], mimetype="application/pdf",
                                headers={"Content-Disposition": f'attachment; filename="{name}"'})
    except db.DatabaseUnavailable:
        pass
    return send_from_directory(media.STATIC, "will_yoste_resume.pdf", as_attachment=True, download_name=name)


def _role_dates(conn, values, body):
    if values.get("started_on") and values.get("ended_on") and values["ended_on"] < values["started_on"]:
        raise records.Invalid("ended_on must be on or after started_on")
    return values


# Owner record APIs for each list on the page.
records.Resource(bp, "/api/v1/career/roles", "career.roles", "role_id", [
    F("slug", required=True, max_length=60), F("title", required=True, max_length=160), F("organization", required=True, max_length=160),
    F("location", max_length=120), F("started_on", "date", required=True), F("ended_on", "date"),
    F("summary", max_length=2000), F("bullets", "list", default=[], max_length=500), F("tags", "list", default=[], max_length=60),
    F("visible", "bool", default=True)], order="ended_on DESC NULLS FIRST, started_on DESC", prepare=_role_dates)
records.Resource(bp, "/api/v1/career/education", "career.education", "education_id", [
    F("degree", required=True, max_length=160), F("field_of_study", max_length=160), F("school", required=True, max_length=160),
    F("location", max_length=120), F("finished_on", "date"), F("grade", max_length=60),
    F("highlight_label", max_length=80), F("highlight", max_length=2000), F("visible", "bool", default=True)],
    order="finished_on DESC NULLS FIRST")
records.Resource(bp, "/api/v1/career/skill-groups", "career.skill_groups", "group_id", [
    F("label", required=True, max_length=120), F("skills", "list", default=[], max_length=60),
    F("featured", "list", default=[], max_length=60), F("position", "int", default=0, low=0, high=1000)],
    order="position, group_id")
records.Resource(bp, "/api/v1/career/highlights", "career.highlights", "highlight_id", [
    F("section", required=True, choices=set(SECTIONS)), F("title", required=True, max_length=200), F("body", max_length=1000),
    F("icon", max_length=8), F("position", "int", default=0, low=0, high=1000)],
    order="section, position, highlight_id")
