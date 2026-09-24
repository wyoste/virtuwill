"""Money › Imports: upload portal exports (or structured bundles), review what
they would change, then commit them into the finance tables.

    POST   /api/v1/money/imports            files → extract, stage, preview
    GET    /api/v1/money/imports            recent imports
    GET    /api/v1/money/imports/<id>       one import with its preview and report
    POST   /api/v1/money/imports/<id>/commit
    DELETE /api/v1/money/imports/<id>       discard a staged import
    GET    /api/v1/money/imports/<id>/bundle[?format=csv]   the structured data (JSON, or a zip of CSVs)
    POST   /api/v1/money/extract[?format=csv]  files → structured data, without staging anything

The original files are kept as private media (never served publicly).
"""
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from . import db, media
from .auth import admin_required
from .importers import ExtractError, extract, merge
from .importers import load as loader
from .importers.canonical import to_csv

bp = Blueprint("money_imports", __name__)
MAX_BYTES = 30 * 1024 * 1024
ALLOWED = {".pdf", ".csv", ".json"}


def _uploaded():
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        raise ExtractError("Choose one or more files")
    out, total = [], 0
    for f in files:
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(f.filename).name)[:160]
        if Path(name).suffix.lower() not in ALLOWED:
            raise ExtractError(f"{name}: only PDF, CSV or JSON files")
        content = f.read(MAX_BYTES + 1)
        total += len(content)
        if total > MAX_BYTES:
            raise ExtractError("Files are larger than 30 MB together")
        out.append((name, content))
    return out


def _bundle(files):
    return merge([extract(name, content) for name, content in files])


def _download(bundle, fmt, name):
    stem = Path(name).stem or "finance"
    if fmt == "csv":
        buffer = io.BytesIO()
        with tempfile.TemporaryDirectory() as folder, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            for path in to_csv(bundle, folder):
                z.write(path, path.name)
        return Response(buffer.getvalue(), mimetype="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.csv.zip"'})
    return Response(json.dumps(bundle, indent=1, default=str), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.finance.json"'})


def _summary(row):
    out = {k: row[k] for k in ("import_id", "filename", "parser", "status")}
    out.update(created_at=row["created_at"].isoformat(), committed_at=row["committed_at"].isoformat() if row["committed_at"] else None,
               preview=row["preview"], report=row["report"])
    return out


@bp.route("/api/v1/money/extract", methods=["POST"])
@admin_required
def extract_route():
    try:
        files = _uploaded()
        return _download(_bundle(files), request.args.get("format"), files[0][0])
    except ExtractError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/v1/money/imports", methods=["POST"])
@admin_required
def create_import():
    try:
        files = _uploaded()
        bundle = _bundle(files)
    except ExtractError as e:
        return jsonify({"error": str(e)}), 400
    with db.tx() as conn:
        assets = []
        for name, content in files:
            # Kept privately so every loaded row can be traced to its file.
            sha = bundle["document"]["sha256"][:16]
            assets.append(media.register(conn, f"private/finance/{sha}/{name}", content, visibility="private"))
        import_id, preview = loader.stage(conn, bundle, assets)
        row = conn.execute("SELECT * FROM finance.staged_imports WHERE import_id = %s", (import_id,)).fetchone()
        return jsonify(_summary(row)), 201


@bp.route("/api/v1/money/imports")
@admin_required
def list_imports():
    with db.tx() as conn:
        rows = conn.execute("""SELECT import_id, filename, parser, status, created_at, committed_at, preview, report
                               FROM finance.staged_imports WHERE status <> 'discarded'
                               ORDER BY import_id DESC LIMIT 100""").fetchall()
        return jsonify([_summary(r) for r in rows])


@bp.route("/api/v1/money/imports/<int:import_id>")
@admin_required
def get_import(import_id):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM finance.staged_imports WHERE import_id = %s", (import_id,)).fetchone()
        return jsonify(_summary(row)) if row else (jsonify({"error": "Not found"}), 404)


@bp.route("/api/v1/money/imports/<int:import_id>/commit", methods=["POST"])
@admin_required
def commit_import(import_id):
    try:
        with db.tx() as conn:
            report = loader.commit(conn, import_id)
            row = conn.execute("SELECT * FROM finance.staged_imports WHERE import_id = %s", (import_id,)).fetchone()
            return jsonify({**_summary(row), "report": report})
    except LookupError:
        return jsonify({"error": "Not found"}), 404
    except loader.AlreadyCommitted as e:
        return jsonify({"error": str(e)}), 409


@bp.route("/api/v1/money/imports/<int:import_id>", methods=["DELETE"])
@admin_required
def discard_import(import_id):
    with db.tx() as conn:
        if not conn.execute("UPDATE finance.staged_imports SET status = 'discarded' WHERE import_id = %s AND status = 'staged'",
                            (import_id,)).rowcount:
            return jsonify({"error": "Only a staged import can be discarded"}), 409
    return jsonify({"ok": True})


@bp.route("/api/v1/money/imports/<int:import_id>/bundle")
@admin_required
def import_bundle(import_id):
    with db.tx() as conn:
        row = conn.execute("SELECT filename, bundle FROM finance.staged_imports WHERE import_id = %s", (import_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return _download(row["bundle"], request.args.get("format"), row["filename"])

