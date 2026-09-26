"""Places on Earth for the travel pick lists: cities, and the states or regions they sit in.

data/geo/cities.tsv.gz is GeoNames (geonames.org, CC BY 4.0) by way of the
cities.json package: every city over about 1,000 people, with its country, its
first-level region (a US state's USPS code, e.g. NY) and its position.
data/geo/regions.json names those regions ("US.NY" → "New York").

Loaded on first use and kept per country, so a search only reads one country.
"""
import gzip
import json
import math
import unicodedata
from functools import lru_cache

from . import db

GEO = db.ROOT / "data" / "geo"


def fold(text):
    """Lower case, accents dropped: 'São Paulo' and 'sao paulo' match."""
    text = str(text or "")
    if text.isascii():
        return text.lower().strip()
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower().strip()


@lru_cache(maxsize=1)
def _cities():
    by_country = {}
    with gzip.open(GEO / "cities.tsv.gz", "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            name, country, region, lat, lng = line.rstrip("\n").split("\t")
            by_country.setdefault(country, []).append((fold(name), name, region, float(lat), float(lng)))
    return by_country


@lru_cache(maxsize=1)
def _region_names():
    return json.loads((GEO / "regions.json").read_text(encoding="utf-8"))


def region_name(country, region):
    return _region_names().get(f"{country}.{region}", "") if country and region else ""


def regions(country):
    """The country's states or regions that have cities, A–Z: [{code, name}]."""
    country = str(country or "").upper()
    codes = {c[2] for c in _cities().get(country, []) if c[2] and c[2] != "00"}
    return sorted(({"code": code, "name": region_name(country, code) or code} for code in codes), key=lambda r: fold(r["name"]))


def _city(country, c):
    return {"name": c[1], "country": country, "region": c[2], "region_name": region_name(country, c[2]), "lat": c[3], "lng": c[4]}


def search(q, country, region=None, limit=20):
    """Cities in a country (and region) matching q: exact names first, then names starting with it,
    then a word starting with it, then containing it; shorter names first within each."""
    country = str(country or "").upper()
    q = fold(q)
    if not q or country not in _cities():
        return []
    ranked = []
    for c in _cities()[country]:
        if region and c[2] != region:
            continue
        name = c[0]
        if name == q:
            tier = 0
        elif name.startswith(q):
            tier = 1
        elif f" {q}" in name or f"-{q}" in name:
            tier = 2
        elif q in name:
            tier = 3
        else:
            continue
        ranked.append((tier, len(name), name, c))
    ranked.sort(key=lambda r: r[:3])
    return [_city(country, r[3]) for r in ranked[:limit]]


def _km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(a)))


def nearest(lat, lng, country=None, within_km=75):
    """The city closest to a point (in the country, when given), if one is within reach."""
    pools = [(country.upper(), _cities().get(country.upper(), []))] if country else _cities().items()
    best = None
    for code, cities in pools:
        for c in cities:
            if abs(c[3] - lat) > 1.5:          # cheap reject before the real distance
                continue
            d = _km(lat, lng, c[3], c[4])
            if d <= within_km and (best is None or d < best[0]):
                best = (d, code, c)
    return _city(best[1], best[2]) if best else None
