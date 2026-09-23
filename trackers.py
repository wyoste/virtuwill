"""Private, admin-only hosting for the standalone Yoste trackers.

The original HTML contains personal seed data. It belongs in the private SQLite
store, never templates/static or Git. Frames run in an opaque-origin sandbox;
only the parent admin page can call the persistence API.
"""
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from functools import wraps
from html.parser import HTMLParser
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session, Response

bp = Blueprint("trackers", __name__)
KINDS = {"finance": "yoste-finance-spa-v1", "health": "yoste-health-v1"}
MAX_BYTES = 5 * 1024 * 1024


class SeedParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.capture = attrs.get("id") == "seed" and attrs.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)


def validate_document(kind, html):
    if kind not in KINDS or not isinstance(html, str) or len(html.encode()) > MAX_BYTES:
        raise ValueError("Invalid or oversized tracker document (5 MB maximum).")
    parser = SeedParser()
    parser.feed(html)
    try:
        seed = json.loads("".join(parser.parts))
    except (ValueError, TypeError):
        raise ValueError("Choose the original Yoste tracker HTML file.") from None
    expected = ("transactions", "receipts", "retirement") if kind == "finance" else ("foods", "recipes", "counts")
    if not isinstance(seed, dict) or any(k not in seed for k in expected) or KINDS[kind] not in html:
        raise ValueError("This file does not match the selected tracker.")
    if not re.search(r"<head\b[^>]*>", html, re.I):
        raise ValueError("Tracker HTML is missing its head element.")


def validate_state(kind, data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported tracker state version.")
    arrays = ("transactions", "movements", "items", "receipts", "retirement", "allocations", "shopping") if kind == "finance" else ("weights", "meals", "workouts", "beers", "complete", "foods")
    if any(not isinstance(data.get(k), list) for k in arrays):
        raise ValueError("Tracker state is missing a required section.")
    if kind == "health" and not isinstance(data.get("settings"), dict):
        raise ValueError("Health settings are required.")
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Tracker state must contain valid JSON values.") from None
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError("Tracker data exceeds the 5 MB limit.")
    return encoded


class TrackerStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / "trackers.sqlite3"
        db = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS trackers (kind TEXT PRIMARY KEY, document TEXT NOT NULL, state TEXT, revision INTEGER NOT NULL DEFAULT 0)")
                yield db
        finally:
            db.close()

    def get(self, kind):
        with self.connect() as db:
            row = db.execute("SELECT * FROM trackers WHERE kind = ?", (kind,)).fetchone()
            return dict(row) if row else None

    def install(self, kind, document):
        validate_document(kind, document)
        # One-time install. Replacing executable source must never reset live data.
        with self.connect() as db:
            db.execute("INSERT INTO trackers(kind, document) VALUES (?, ?)", (kind, document))

    def save(self, kind, state, revision):
        encoded = validate_state(kind, state)
        with self.connect() as db:
            result = db.execute("UPDATE trackers SET state = ?, revision = revision + 1 WHERE kind = ? AND revision = ?", (encoded, kind, revision))
            return result.rowcount == 1


def store():
    return TrackerStore(current_app.config["TRACKER_DATA_DIR"])


def protected(fn):
    @wraps(fn)
    def wrapped(kind, *args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify(error="Sign in to Admin to open your trackers."), 401
        if kind not in KINDS:
            return jsonify(error="Unknown tracker."), 404
        if not current_app.config.get("TRACKER_AUTH_CONFIGURED"):
            return jsonify(error="Set a unique SECRET_KEY (32+ characters) and ADMIN_PASSWORD (12+ characters) on the server before using private trackers."), 503
        if request.method != "GET":
            token = session.get("tracker_csrf", "")
            if not token or not secrets.compare_digest(request.headers.get("X-Tracker-CSRF", ""), token):
                return jsonify(error="Session expired. Reload the admin page."), 403
            if request.content_length and request.content_length > MAX_BYTES + 4096:
                return jsonify(error="File or data exceeds the 5 MB limit."), 413
        return fn(kind, *args, **kwargs)
    return wrapped


@bp.after_request
def private_headers(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.route("/api/admin/trackers/<kind>", methods=["GET", "PUT"])
@protected
def tracker_data(kind):
    if request.method == "GET":
        session.setdefault("tracker_csrf", secrets.token_urlsafe(32))
        row = store().get(kind)
        return jsonify(configured=bool(row), revision=row["revision"] if row else 0, csrf=session["tracker_csrf"])
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
        return jsonify(error="A state and integer revision are required."), 400
    try:
        if not store().save(kind, payload.get("state"), payload["revision"]):
            return jsonify(error="Another tab or device saved newer data. Export your unsaved changes, then reload this tracker."), 409
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(revision=payload["revision"] + 1)


@bp.route("/api/admin/trackers/<kind>/install", methods=["POST"])
@protected
def install_tracker(kind):
    upload = request.files.get("file")
    if not upload:
        return jsonify(error="Choose a tracker HTML file."), 400
    try:
        document = upload.stream.read(MAX_BYTES + 1).decode("utf-8")
        store().install(kind, document)
    except (ValueError, UnicodeError) as error:
        return jsonify(error=str(error)), 400
    except sqlite3.IntegrityError:
        return jsonify(error="Tracker already installed. Use its Restore backup option to migrate newer records."), 409
    return jsonify(ok=True), 201


@bp.route("/admin/trackers/<kind>/frame")
@protected
def tracker_frame(kind):
    row = store().get(kind)
    if not row:
        return jsonify(error="Import your tracker HTML first."), 404
    # Escape script delimiters in saved user text before embedding it in HTML.
    bootstrap = json.dumps({"kind": kind, "key": KINDS[kind], "revision": row["revision"], "state": json.loads(row["state"]) if row["state"] else None, "origin": request.host_url.rstrip("/")}).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    bridge = (Path(__file__).parent / "static/js/tracker-frame.js").read_text()
    script = '<script id="vw-tracker-bridge">window.TRACKER_BOOT=' + bootstrap + ";\n" + bridge + "</script>"
    # Insert first: storage must be replaced before the imported app executes.
    document = row["document"].replace("Saved on this browser", "Saving to server…")
    document = document.replace("const d=document.documentElement.cloneNode(true);", "const d=document.documentElement.cloneNode(true);window.trackerExportCleanup?.(d);")
    document = document.replace("Records stay in this browser when storage is available; they do not sync between devices.", "Records save to your private server and are available across devices after saving.")
    document = document.replace("replaces this browser’s data", "replaces this tracker’s server data")
    document = document.replace("New records live in this browser only. To move devices, export and restore the JSON backup.", "New records save to your private server. Use JSON exports for additional backups.")
    document = document.replace("Changes save in this browser when storage is available. They do not modify the original workbook or sync across devices.", "Changes save to your private server and are available across devices. They do not modify the original workbook.")
    # Both supported apps restore in async handlers. Native confirm dialogs are
    # blocked in opaque-origin frames, so use an in-frame accessible dialog.
    document = document.replace("if(!confirm(", "if(!await trackerConfirm(")
    html = re.sub(r"(<head\b[^>]*>)", lambda m: m[1] + script, document, count=1, flags=re.I)
    response = Response(html, mimetype="text/html")
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-scripts allow-downloads allow-modals allow-forms"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
