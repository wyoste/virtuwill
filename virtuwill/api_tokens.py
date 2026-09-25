"""API tokens: how a scheduled job (such as a Claude task) calls the app without a browser.

Tokens are made and revoked by the owner in Settings; only a sha256 of each is
stored, and the token is shown once. A request presents it as

    X-VirtuWill-Token: vw_…          (works behind a proxy that uses Authorization itself)
    Authorization: Bearer vw_…

Token-protected routes never fall back to the browser session, so a token can't
be used to reach owner screens and a session can't be used from a script.
"""
import hashlib
import hmac
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, request

from . import db
from .auth import admin_required
from .util import plain

bp = Blueprint("api_tokens", __name__)
SCOPES = ("finance:write", "finance:read")
PREFIX = "vw_"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(conn, name, scopes):
    token = PREFIX + secrets.token_urlsafe(32)
    row = conn.execute("""INSERT INTO core.api_tokens (name, token_hash, token_prefix, scopes)
                          VALUES (%s, %s, %s, %s) RETURNING token_id""",
                       (name, _hash(token), token[:10], list(scopes))).fetchone()
    return row["token_id"], token


def _presented():
    token = request.headers.get("X-VirtuWill-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:].strip().startswith(PREFIX):
            token = auth[7:].strip()
    return token if token.startswith(PREFIX) and len(token) < 200 else None


def token_required(scope):
    """Allow the request only with a live token that has `scope`; sets g.api_token."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            token = _presented()
            if not token:
                return jsonify({"error": "An API token is required (X-VirtuWill-Token header)"}), 401
            digest = _hash(token)
            with db.tx() as conn:
                row = conn.execute("SELECT * FROM core.api_tokens WHERE token_hash = %s AND revoked_at IS NULL",
                                   (digest,)).fetchone()
                # Compare again in constant time; the lookup is by hash, so this is belt and braces.
                if not row or not hmac.compare_digest(row["token_hash"], digest):
                    return jsonify({"error": "That token isn't valid or has been revoked"}), 401
                if scope not in row["scopes"]:
                    return jsonify({"error": f"That token can't do this (needs {scope})"}), 403
                conn.execute("UPDATE core.api_tokens SET last_used_at = now(), use_count = use_count + 1 WHERE token_id = %s",
                             (row["token_id"],))
            g.api_token = {"id": row["token_id"], "name": row["name"], "scopes": row["scopes"]}
            return fn(*args, **kwargs)
        return wrapped
    return decorate


# ── Owner: make, list and revoke tokens ──────────────────────────────────────

@bp.route("/api/v1/api-tokens")
@admin_required
def tokens_list():
    with db.tx() as conn:
        rows = conn.execute("""SELECT token_id, name, token_prefix, scopes, created_at, last_used_at, use_count, revoked_at
                               FROM core.api_tokens ORDER BY revoked_at IS NOT NULL, created_at DESC""").fetchall()
        return jsonify([plain(r) for r in rows])


@bp.route("/api/v1/api-tokens", methods=["POST"])
@admin_required
def tokens_create():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:100]
    scopes = [s for s in data.get("scopes") or ["finance:write", "finance:read"] if s in SCOPES]
    if not name:
        return jsonify({"error": "Give the token a name, e.g. 'Claude weekly finance'"}), 400
    if not scopes:
        return jsonify({"error": f"Choose at least one scope: {', '.join(SCOPES)}"}), 400
    with db.tx() as conn:
        token_id, token = create(conn, name, scopes)
    # The only time the token is ever returned.
    return jsonify({"token_id": token_id, "name": name, "scopes": scopes, "token": token}), 201


@bp.route("/api/v1/api-tokens/<int:token_id>", methods=["DELETE"])
@admin_required
def tokens_revoke(token_id):
    with db.tx() as conn:
        done = conn.execute("UPDATE core.api_tokens SET revoked_at = now() WHERE token_id = %s AND revoked_at IS NULL",
                            (token_id,)).rowcount
    return jsonify({"ok": True}) if done else (jsonify({"error": "No live token with that id"}), 404)
