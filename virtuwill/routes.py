"""Workout routes from GPX and TCX files: the points, how far, how long, how much climbing.

GPX (Strava, Garmin, Apple Health exports via most apps) has <trkpt lat lon>
with <ele> and <time>; TCX (Garmin) has <Trackpoint> with <Position>,
<AltitudeMeters> and <Time>. Tags are matched by their local names, so any
namespace works. Files with a DOCTYPE are refused (no entity tricks).
"""
import math
import xml.etree.ElementTree as ET
from datetime import datetime

MAX_BYTES = 15 * 1024 * 1024
MAX_POINTS = 1500            # kept for drawing; distance is measured on the full track


class RouteError(ValueError):
    pass


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _time(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse(content):
    """[(lat, lng, elevation_m | None, time | None), …] from a GPX or TCX file."""
    if len(content) > MAX_BYTES:
        raise RouteError("That file is larger than 15 MB")
    head = content[:2048].lower()
    if b"<!doctype" in head or b"<!entity" in content[:65536].lower():
        raise RouteError("That file isn't a plain GPX or TCX file")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise RouteError("That file isn't a GPX or TCX file (it couldn't be read as XML)") from None
    points = []
    for el in root.iter():
        name = _local(el.tag)
        if name in ("trkpt", "rtept"):                                   # GPX
            lat, lng = _float(el.get("lat")), _float(el.get("lon"))
            kids = {_local(c.tag): c.text for c in el}
            if lat is not None and lng is not None:
                points.append((lat, lng, _float(kids.get("ele")), _time(kids.get("time"))))
        elif name == "Trackpoint":                                       # TCX
            kids = {_local(c.tag): c for c in el}
            pos = kids.get("Position")
            if pos is None:
                continue
            p = {_local(c.tag): c.text for c in pos}
            lat, lng = _float(p.get("LatitudeDegrees")), _float(p.get("LongitudeDegrees"))
            if lat is not None and lng is not None:
                alt, t = kids.get("AltitudeMeters"), kids.get("Time")
                points.append((lat, lng, _float(alt.text) if alt is not None else None, _time(t.text) if t is not None else None))
    points = [p for p in points if -90 <= p[0] <= 90 and -180 <= p[1] <= 180]
    if len(points) < 2:
        raise RouteError("No route found in that file (it needs at least two GPS points)")
    return points


def _km(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def summarise(points):
    """Distance, climbing, start and end, and a simplified line for the map."""
    distance = sum(_km(a, b) for a, b in zip(points, points[1:]))
    gain = sum(max(0, b[2] - a[2]) for a, b in zip(points, points[1:]) if a[2] is not None and b[2] is not None)
    times = [p[3] for p in points if p[3] is not None]
    step = max(1, math.ceil(len(points) / MAX_POINTS))
    line = [[round(p[0], 6), round(p[1], 6)] for p in points[::step]]
    if line[-1] != [round(points[-1][0], 6), round(points[-1][1], 6)]:
        line.append([round(points[-1][0], 6), round(points[-1][1], 6)])
    return {"distance_km": round(distance, 3), "elevation_gain_m": round(gain, 1) if gain else None,
            "started_at": min(times) if times else None, "ended_at": max(times) if times else None, "points": line}
