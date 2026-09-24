"""Small parsing helpers shared by the domain modules."""
import re
from datetime import date, datetime, timezone

FIRST_DAY, LAST_DAY = date(1900, 1, 1), date(2100, 12, 31)   # core.calendar's range


def timestamp(value):
    """An aware datetime from ISO text; now() when missing or unreadable."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def moment(value):
    """A timestamp only when the source recorded a time of day, else None."""
    if not value or "T" not in str(value):
        return None
    try:
        return timestamp(value)
    except ValueError:
        return None


def parse_date(value):
    """A calendar date from ISO text or epoch milliseconds/seconds, else None."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return timestamp(text).date() if "T" in text else None


def in_calendar(day):
    return day is not None and FIRST_DAY <= day <= LAST_DAY


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def iso_z(moment_value):
    return moment_value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "item"


def plain(row):
    """A row with dates as ISO text and decimals as floats, ready for JSON."""
    out = {}
    for key, value in row.items():
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif hasattr(value, "is_finite"):
            value = float(value)
        out[key] = value
    return out
