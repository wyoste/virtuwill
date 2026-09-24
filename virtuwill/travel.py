"""Travel: map pins and the countries and US states visited.

The travel page still stores its data as two strings (pins and visited), so
these helpers convert between that shape and the travel tables.
"""
import json

from .util import number, parse_date, in_calendar

PIN_TYPES = {"visited", "recommend", "wishlist"}


def pins(conn):
    rows = conn.execute("SELECT * FROM travel.places ORDER BY created_at, place_id").fetchall()
    out = []
    for r in rows:
        photos = [p["url"] or ("/static/" + p["path"] if p["path"] else None) for p in conn.execute(
            """SELECT ph.url, m.path FROM travel.place_photos ph LEFT JOIN core.media_assets m USING (asset_id)
               WHERE ph.place_id = %s ORDER BY ph.position""", (r["place_id"],))]
        pin = {"id": _id_out(r["place_id"]), "name": r["name"], "city": r["city"], "lat": float(r["latitude"]),
               "lng": float(r["longitude"]), "type": r["pin_type"], "note": r["note"], "photos": [p for p in photos if p]}
        if r["display_name"]:
            pin["display"] = r["display_name"]
        out.append(pin)
    return out


def _id_out(place_id):
    return int(place_id) if place_id.isdigit() else place_id


def set_pins(conn, items):
    items = [p for p in items or [] if isinstance(p, dict) and number(p.get("lat")) is not None and number(p.get("lng")) is not None]
    ids = [str(p.get("id") or f"{p['lat']},{p['lng']}") for p in items]
    conn.execute("DELETE FROM travel.places WHERE NOT (place_id = ANY(%s))", (ids,))
    for place_id, p in zip(ids, items):
        lat, lng = number(p["lat"]), number(p["lng"])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        visited = parse_date(p.get("visited"))
        conn.execute(
            """INSERT INTO travel.places (place_id, name, city, display_name, latitude, longitude, pin_type, note, visited_on)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (place_id) DO UPDATE SET name = EXCLUDED.name, city = EXCLUDED.city, display_name = EXCLUDED.display_name,
                   latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, pin_type = EXCLUDED.pin_type, note = EXCLUDED.note,
                   visited_on = EXCLUDED.visited_on""",
            (place_id, p.get("name") or "Pin", p.get("city") or "", p.get("display") or "", round(lat, 6), round(lng, 6),
             p.get("type") if p.get("type") in PIN_TYPES else "visited", p.get("note") or "",
             visited if in_calendar(visited) else None))
        conn.execute("DELETE FROM travel.place_photos WHERE place_id = %s", (place_id,))
        for position, photo in enumerate(p.get("photos") or []):
            url = photo if isinstance(photo, str) else (photo or {}).get("url")
            if url:
                conn.execute("INSERT INTO travel.place_photos (place_id, position, url) VALUES (%s, %s, %s)", (place_id, position, url))


def visited(conn):
    rows = conn.execute("SELECT region_type, code FROM travel.visited_regions ORDER BY code").fetchall()
    return {"countries": [r["code"] for r in rows if r["region_type"] == "country"],
            "states": [r["code"] for r in rows if r["region_type"] == "us_state"]}


def set_visited(conn, data):
    data = data if isinstance(data, dict) else {}
    conn.execute("DELETE FROM travel.visited_regions")
    for region_type, key in (("country", "countries"), ("us_state", "states")):
        for code in dict.fromkeys(str(c).upper() for c in data.get(key) or []):
            if len(code) == 2 and code.isalpha():
                conn.execute("INSERT INTO travel.visited_regions (region_type, code) VALUES (%s, %s)", (region_type, code))


def as_page_string(value):
    return json.dumps(value)
