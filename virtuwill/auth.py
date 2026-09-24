"""Sign-in for the owner: the admin session and the journal passphrase."""
import secrets
from functools import wraps

from flask import Blueprint, jsonify, request, session

import config

bp = Blueprint("auth", __name__)


def is_admin():
    return bool(session.get("admin_logged_in"))


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapped


def journal_unlocked():
    """The owner's sign-in opens the journal; the journal passphrase alone opens only the journal."""
    return bool(session.get("journal_unlocked") or session.get("admin_logged_in"))


def journal_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not journal_unlocked():
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapped


def strong_credentials():
    """Private trackers refuse to run on the documented development defaults."""
    return (config.SECRET_KEY != "dev-secret-change-in-production" and len(config.SECRET_KEY) >= 32
            and config.ADMIN_PASSWORD != "virtuwill2026" and len(config.ADMIN_PASSWORD) >= 12)


@bp.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True) or {}
    if (secrets.compare_digest(str(data.get("username", "")), config.ADMIN_USER)
            and secrets.compare_digest(str(data.get("password", "")), config.ADMIN_PASSWORD)):
        session["admin_logged_in"] = True
        session["tracker_csrf"] = secrets.token_urlsafe(32)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@bp.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_logged_in", None)
    session.pop("journal_unlocked", None)
    session.pop("tracker_csrf", None)
    return jsonify({"ok": True})


@bp.route("/api/admin/status")
def admin_status():
    return jsonify({"logged_in": is_admin()})


@bp.route("/api/journal/unlock", methods=["POST"])
def journal_unlock():
    data = request.get_json(force=True) or {}
    if secrets.compare_digest(str(data.get("password", "")), config.JOURNAL_PASSWORD):
        session["journal_unlocked"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect passphrase"}), 401


@bp.route("/api/journal/logout", methods=["POST"])
def journal_logout():
    session.pop("journal_unlocked", None)
    return jsonify({"ok": True})


@bp.route("/api/journal/status")
def journal_status():
    return jsonify({"unlocked": journal_unlocked()})
