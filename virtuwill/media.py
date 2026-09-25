"""Files the site serves: audio, photos, thumbnails, portfolio HTML.

Every file is a core.media_assets row keyed by its path under static/. Files
shipped in the repository are registered by path (content stays on disk);
uploads also keep their bytes in the database, so a redeploy, which resets
the app's disk, never loses them.
"""
import hashlib
import logging
import mimetypes
from pathlib import Path

from . import db

log = logging.getLogger(__name__)
STATIC = db.ROOT / "static"
BUNDLED_DIRS = ("audio", "music/photos", "garden/photos", "blog", "portfolio")
AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
mimetypes.add_type("audio/mp4", ".m4a")


def content_type(path):
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def relpath(path):
    """'static/blog/x.png', '/static/blog/x.png' or an absolute path → 'blog/x.png'."""
    text = str(path)
    if text.startswith("/static/"):
        text = text[len("/static/"):]
    elif text.startswith("static/"):
        text = text[len("static/"):]
    full = (STATIC / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
    if STATIC.resolve() not in full.parents:
        raise ValueError("Media path must be inside static/")
    return full.relative_to(STATIC.resolve()).as_posix()


def url(path):
    return "/static/" + path if path else None


def register(conn, path, content=None, visibility="public"):
    """Create or update the asset for a path; returns its id."""
    path = relpath(path)
    size = len(content) if content is not None else None
    if size is None and (STATIC / path).is_file():
        size = (STATIC / path).stat().st_size
    digest = hashlib.sha256(content).hexdigest() if content is not None else None
    return conn.execute(
        """INSERT INTO core.media_assets (path, content_type, content, byte_size, sha256, visibility)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (path) DO UPDATE SET content_type = EXCLUDED.content_type,
               content = COALESCE(EXCLUDED.content, core.media_assets.content),
               byte_size = COALESCE(EXCLUDED.byte_size, core.media_assets.byte_size),
               sha256 = COALESCE(EXCLUDED.sha256, core.media_assets.sha256)
           RETURNING asset_id""",
        (path, content_type(path), content, size, digest, visibility)).fetchone()["asset_id"]


def asset_id(conn, path):
    if not path:
        return None
    try:
        path = relpath(path)
    except ValueError:
        return None
    row = conn.execute("SELECT asset_id FROM core.media_assets WHERE path = %s", (path,)).fetchone()
    return row["asset_id"] if row else None


def path_of(conn, asset):
    if not asset:
        return None
    row = conn.execute("SELECT path FROM core.media_assets WHERE asset_id = %s", (asset,)).fetchone()
    return row["path"] if row else None


def save_upload(conn, file_storage, path):
    """Keep an uploaded file's bytes in the database, and a copy under static/ when the disk allows.

    The database copy is the one that counts: a deployment whose app folder is
    read-only, or that runs several instances, still serves the file from it."""
    path = relpath(path)
    content = file_storage.read()
    try:
        target = STATIC / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    except OSError as error:
        log.info("Kept %s in the database only: %s", path, error)
    return register(conn, path, content)


def delete(conn, path):
    path = relpath(path)
    try:
        (STATIC / path).unlink()
    except OSError:
        pass
    conn.execute("DELETE FROM core.media_assets WHERE path = %s", (path,))


def stored(path, include_private=False):
    """(content_type, bytes) for a file kept in the database, or None.

    Only public assets are served to visitors; private ones need the owner.
    """
    try:
        row = db.one("""SELECT content_type, content FROM core.media_assets
                        WHERE path = %s AND content IS NOT NULL AND (visibility = 'public' OR %s)""",
                     relpath(path), include_private)
    except (db.DatabaseUnavailable, ValueError):
        return None
    return (row["content_type"], bytes(row["content"])) if row else None


def sync_bundled(conn):
    """Register files shipped in the repository (new ones appear after a deploy)."""
    known = {r["path"] for r in conn.execute("SELECT path FROM core.media_assets")}
    for folder in BUNDLED_DIRS:
        base = STATIC / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in AUDIO | IMAGES | {".html"}:
                rel = path.relative_to(STATIC).as_posix()
                if rel not in known:
                    register(conn, rel)


def restore_to_disk():
    """Write uploads kept in the database back under static/ after a redeploy."""
    restored = 0
    # Files under static/ are served to anyone, so only public ones are written there.
    for row in db.all("SELECT path FROM core.media_assets WHERE content IS NOT NULL AND visibility = 'public'"):
        target = STATIC / row["path"]
        if target.exists():
            continue
        media = stored(row["path"])
        if media:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(media[1])
            restored += 1
    return restored
