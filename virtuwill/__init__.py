"""VirtuWill: a single-page site served by Flask, backed by Lakebase.

Every feature reads and writes the relational model in db/schema through
one module per domain (journal, health, finance, garden, music, content,
travel, site). The page shell is templates/index.html; the pages themselves
are the JavaScript modules under static/js.
"""
import logging
import secrets
import threading

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, session
from werkzeug.exceptions import NotFound

import config
from . import auth, content, db, finance, garden, health, journal, media, money_imports, music, site, today, trackers

log = logging.getLogger(__name__)
BLUEPRINTS = (auth, journal, health, finance, money_imports, garden, music, content, site, today, trackers)


def create_app():
    app = Flask(__name__, root_path=str(db.ROOT))
    app.secret_key = config.SECRET_KEY
    # Private trackers refuse to run on the documented development credentials.
    app.config["TRACKER_AUTH_CONFIGURED"] = auth.strong_credentials()
    for module in BLUEPRINTS:
        app.register_blueprint(module.bp)

    @app.errorhandler(db.DatabaseUnavailable)
    def database_unavailable(error):
        log.warning("Database unavailable: %s", error)
        return jsonify({"error": "The database is unavailable. Try again shortly.", "available": False}), 503

    # Uploads are kept in the database; serve any not yet restored to static/.
    serve_static = app.view_functions["static"]

    def static_or_stored(filename):
        try:
            return serve_static(filename=filename)
        except NotFound:
            stored = media.stored(filename, include_private=auth.is_admin())
            if not stored:
                raise
            return Response(stored[1], mimetype=stored[0])

    app.view_functions["static"] = static_or_stored

    @app.route("/app")
    @app.route("/app/<path:path>")
    def workspace(path=""):
        """The owner's workspace: Today, Journal, Health, Money, Site, Settings."""
        signed_in = auth.is_admin()
        if signed_in:
            session.setdefault("tracker_csrf", secrets.token_urlsafe(32))
        response = app.make_response(render_template("workspace.html", signed_in=signed_in))
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @app.route("/admin")
    def old_admin():
        return redirect("/app")

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def index(path):
        if path.startswith(("static/", "api/")):
            abort(404)
        if auth.is_admin():
            session.setdefault("tracker_csrf", secrets.token_urlsafe(32))
        return render_template("index.html",
                               journal_unlocked=auth.journal_unlocked(),
                               admin_logged_in=auth.is_admin(),
                               chat_enabled=site.setting("site.chat_enabled", False) is True)

    @app.route("/resume/download")
    def resume_download():
        return send_from_directory(app.static_folder, "will_yoste_resume.pdf", as_attachment=True,
                                   download_name="Will_Yoste_Resume.pdf")

    @app.after_request
    def headers(response):
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.path.startswith("/static/portfolio/"):
            # Uploaded project pages run scripts; keep them off the site's origin,
            # whether framed by the portfolio page or opened directly.
            response.headers["Content-Security-Policy"] = "sandbox allow-scripts allow-popups allow-forms"
        return response

    if db.configured():
        threading.Thread(target=_warm_up, daemon=True).start()
    else:
        log.warning("No database attached (PGHOST is not set): pages load, data requests return 503.")
    return app


def _warm_up():
    """Bring the schema up to date and restore uploads before the first visitor needs them."""
    try:
        restored = media.restore_to_disk()
        if restored:
            log.info("Restored %d uploaded files from the database", restored)
    except Exception:
        log.exception("Database warm-up failed; requests will retry")
