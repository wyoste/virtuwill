"""Garden: beds, plantings, species, plant health over time, and photos.

The garden planner still saves the whole garden as one document; a save
upserts beds and plantings, removes the ones that were deleted, and logs a
plant observation whenever a plant is added or its health changes.
"""
import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from . import db, media
from .auth import admin_required
from .util import in_calendar, number, parse_date

bp = Blueprint("garden", __name__)
SEED = db.ROOT / "db" / "seed" / "garden_species.json"
PHOTOS_DIR = "garden/photos"


def seed_species(conn):
    for s in json.loads(SEED.read_text(encoding="utf-8")):
        conn.execute("""INSERT INTO garden.species (species_id, name, category, mature_diameter_ft, emoji)
                        VALUES (%(species_id)s, %(name)s, %(category)s, %(mature_diameter_ft)s, %(emoji)s)
                        ON CONFLICT (species_id) DO NOTHING""", s)


def _ensure_species(conn, species_id, display_name):
    conn.execute("""INSERT INTO garden.species (species_id, name, category, mature_diameter_ft)
                    VALUES (%s, %s, 'Other', 1.5) ON CONFLICT DO NOTHING""", (species_id, display_name or species_id))


def _plant(p):
    plant = {"id": p["planting_id"], "speciesId": p["species_id"], "displayName": p["display_name"],
             "gi": p["grid_i"], "gj": p["grid_j"], "health": p["health"], "notes": p["notes"]}
    if p["radius_ft"] is not None:
        plant["radiusFt"] = number(p["radius_ft"])
    if p["age_years"] is not None:
        plant["age"] = number(p["age_years"])
    return plant


def load(conn):
    settings = conn.execute("""SELECT s.feet_per_pixel, m.path FROM garden.settings s
                               LEFT JOIN core.media_assets m ON m.asset_id = s.background_asset_id""").fetchone()
    beds = []
    for b in conn.execute("SELECT * FROM garden.beds ORDER BY position NULLS LAST, bed_id"):
        shape = {"type": b["shape_type"]}
        if b["shape_type"] == "rectangle":
            shape.update(width=number(b["width_ft"]), height=number(b["height_ft"]))
        elif b["shape_type"] == "circle":
            shape["radius"] = number(b["radius_ft"])
        else:
            shape["vertices"] = b["vertices"]
        plants = [_plant(p) for p in conn.execute("""SELECT p.* FROM garden.plantings p
                                           LEFT JOIN garden.seasons s ON s.season_id = p.season_id
                                           WHERE p.bed_id = %s AND p.removed_on IS NULL AND (s.is_active OR p.season_id IS NULL)
                                           ORDER BY p.planting_id""", (b["bed_id"],))]
        beds.append({"id": b["bed_id"], "name": b["name"], "color": b["color"], "shape": shape,
                     "transform": {"x": number(b["x_ft"]), "y": number(b["y_ft"]), "rotation": number(b["rotation_deg"])},
                     "plants": plants})
    return {"calibration": {"feetPerPixel": number(settings["feet_per_pixel"]) if settings else None},
            "backgroundImageUrl": media.url(settings["path"]) if settings and settings["path"] else "/static/garden_illustrated.png",
            "beds": beds}


def save(conn, doc):
    background = doc.get("backgroundImageUrl")
    background_id = media.register(conn, background) if background and os.path.isfile(media.STATIC / media.relpath(background)) else None
    fpp = number((doc.get("calibration") or {}).get("feetPerPixel"))
    conn.execute("""INSERT INTO garden.settings (settings_id, background_asset_id, feet_per_pixel) VALUES (1, %s, %s)
                    ON CONFLICT (settings_id) DO UPDATE SET background_asset_id = EXCLUDED.background_asset_id,
                        feet_per_pixel = EXCLUDED.feet_per_pixel""", (background_id, fpp if fpp and fpp > 0 else None))
    beds = [b for b in doc.get("beds") or [] if isinstance(b, dict) and b.get("id")]
    keep_beds = [str(b["id"]) for b in beds]
    conn.execute("DELETE FROM garden.beds WHERE NOT (bed_id = ANY(%s))", (keep_beds,))
    for position, b in enumerate(beds):
        shape, transform = b.get("shape") or {}, b.get("transform") or {}
        kind = shape.get("type") if shape.get("type") in ("rectangle", "circle", "polygon") else "rectangle"
        conn.execute(
            """INSERT INTO garden.beds (bed_id, name, color, shape_type, width_ft, height_ft, radius_ft, vertices, x_ft, y_ft, rotation_deg, position)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (bed_id) DO UPDATE SET name = EXCLUDED.name, color = EXCLUDED.color, shape_type = EXCLUDED.shape_type,
                   width_ft = EXCLUDED.width_ft, height_ft = EXCLUDED.height_ft, radius_ft = EXCLUDED.radius_ft,
                   vertices = EXCLUDED.vertices, x_ft = EXCLUDED.x_ft, y_ft = EXCLUDED.y_ft,
                   rotation_deg = EXCLUDED.rotation_deg, position = EXCLUDED.position""",
            (str(b["id"]), b.get("name") or "Bed", b.get("color") or "#5DCAA5", kind,
             number(shape.get("width")) if kind == "rectangle" else None, number(shape.get("height")) if kind == "rectangle" else None,
             number(shape.get("radius")) if kind == "circle" else None, db.jsonb(shape.get("vertices") or []) if kind == "polygon" else None,
             number(transform.get("x")) or 0, number(transform.get("y")) or 0, number(transform.get("rotation")) or 0, position))
        season = conn.execute("SELECT season_id FROM garden.seasons WHERE bed_id = %s AND is_active", (str(b["id"]),)).fetchone()
        if not season:
            season = conn.execute("""INSERT INTO garden.seasons (bed_id, label, is_active) VALUES (%s, 'Current', true)
                                     ON CONFLICT (bed_id, label) DO UPDATE SET is_active = true RETURNING season_id""",
                                  (str(b["id"]),)).fetchone()
        plants = [p for p in b.get("plants") or [] if isinstance(p, dict) and p.get("id") and p.get("speciesId")]
        conn.execute("DELETE FROM garden.plantings WHERE bed_id = %s AND season_id = %s AND NOT (planting_id = ANY(%s))",
                     (str(b["id"]), season["season_id"], [str(p["id"]) for p in plants]))
        for p in plants:
            _ensure_species(conn, p["speciesId"], p.get("displayName"))
            health = int(number(p.get("health")) if number(p.get("health")) is not None else 2)
            health = min(max(health, 0), 4)
            radius, age = number(p.get("radiusFt")), number(p.get("age"))
            previous = conn.execute("SELECT health FROM garden.plantings WHERE planting_id = %s", (str(p["id"]),)).fetchone()
            conn.execute(
                """INSERT INTO garden.plantings (planting_id, bed_id, season_id, species_id, display_name, grid_i, grid_j, health, notes,
                                                radius_ft, age_years)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (planting_id) DO UPDATE SET bed_id = EXCLUDED.bed_id, season_id = EXCLUDED.season_id,
                       species_id = EXCLUDED.species_id, display_name = EXCLUDED.display_name, grid_i = EXCLUDED.grid_i,
                       grid_j = EXCLUDED.grid_j, health = EXCLUDED.health, notes = EXCLUDED.notes,
                       radius_ft = EXCLUDED.radius_ft, age_years = EXCLUDED.age_years""",
                (str(p["id"]), str(b["id"]), season["season_id"], p["speciesId"], p.get("displayName") or p["speciesId"],
                 int(number(p.get("gi")) or 0), int(number(p.get("gj")) or 0), health, p.get("notes") or "",
                 radius if radius and radius > 0 else None, age if age and age >= 0 else None))
            if not previous or previous["health"] != health:
                conn.execute("""INSERT INTO garden.plant_observations (planting_id, observed_on, health)
                                VALUES (%s, current_date, %s)""", (str(p["id"]), health))


def photos(conn):
    rows = conn.execute("""SELECT p.*, m.path,
                                  COALESCE(array_agg(s.subject_ref) FILTER (WHERE s.subject_type = 'bed'), '{}') AS beds,
                                  COALESCE(array_agg(s.subject_ref) FILTER (WHERE s.subject_type <> 'bed'), '{}') AS plants
                           FROM garden.photos p JOIN core.media_assets m USING (asset_id)
                           LEFT JOIN garden.photo_subjects s USING (photo_id)
                           GROUP BY p.photo_id, m.path ORDER BY p.taken_on DESC NULLS LAST, p.photo_id""").fetchall()
    return [{"id": r["photo_id"], "url": media.url(r["path"]), "beds": r["beds"], "plants": r["plants"],
             "caption": r["caption"], "category": r["category"],
             "date": r["taken_on"].isoformat() if r["taken_on"] else ""} for r in rows]


def add_photo(conn, photo_id, asset, taken_on, caption, category, beds, plants):
    conn.execute("""INSERT INTO garden.photos (photo_id, asset_id, taken_on, caption, category) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (photo_id) DO NOTHING""",
                 (photo_id, asset, taken_on if in_calendar(taken_on) else None, caption or "", category or ""))
    known = {r["species_id"] for r in conn.execute("SELECT species_id FROM garden.species")}
    plantings = {r["planting_id"] for r in conn.execute("SELECT planting_id FROM garden.plantings")}
    for bed in dict.fromkeys(b for b in beds if b):
        conn.execute("INSERT INTO garden.photo_subjects VALUES (%s, 'bed', %s) ON CONFLICT DO NOTHING", (photo_id, bed))
    for plant in dict.fromkeys(p for p in plants if p):
        kind = "planting" if plant in plantings else "species" if plant in known else "label"
        conn.execute("INSERT INTO garden.photo_subjects VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (photo_id, kind, plant))


def import_photos(conn, items):
    for p in items or []:
        asset = media.asset_id(conn, p.get("url"))
        if not asset and p.get("url"):
            asset = media.register(conn, p["url"])
        if asset:
            add_photo(conn, str(p.get("id") or uuid.uuid4()), asset, parse_date(p.get("date")), p.get("caption"),
                      p.get("category"), p.get("beds") or [], p.get("plants") or [])


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/api/garden")
def garden_get():
    with db.tx() as conn:
        return jsonify(load(conn))


@bp.route("/api/garden", methods=["POST"])
@admin_required
def garden_save():
    with db.tx() as conn:
        save(conn, request.get_json(force=True) or {})
    return jsonify({"ok": True})


@bp.route("/api/garden/photos", methods=["GET"])
def photos_get():
    with db.tx() as conn:
        return jsonify(photos(conn))


@bp.route("/api/garden/photo", methods=["POST"])
@admin_required
def photo_upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files"}), 400
    added = []
    with db.tx() as conn:
        for f in files:
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
            name = str(uuid.uuid4())[:12] + ext
            asset = media.save_upload(conn, f, f"{PHOTOS_DIR}/{name}")
            photo_id = str(uuid.uuid4())
            add_photo(conn, photo_id, asset, datetime.now().date(), request.form.get("caption", "").strip(),
                      request.form.get("category", "").strip(), request.form.getlist("beds"), request.form.getlist("plants"))
            added.append(photo_id)
        by_id = {p["id"]: p for p in photos(conn)}
    return jsonify({"ok": True, "added": [by_id[i] for i in added if i in by_id]})


@bp.route("/api/garden/photo/<photo_id>", methods=["DELETE"])
@admin_required
def photo_delete(photo_id):
    with db.tx() as conn:
        row = conn.execute("""DELETE FROM garden.photos WHERE photo_id = %s
                              RETURNING (SELECT path FROM core.media_assets m WHERE m.asset_id = garden.photos.asset_id) AS path""",
                           (photo_id,)).fetchone()
        if row and row["path"]:
            media.delete(conn, row["path"])
    return jsonify({"ok": True})
