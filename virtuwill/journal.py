"""The journal: one entry per calendar date, with tags, habits and meals.

The journal page's API shape is unchanged (camelCase, meals as {B, L, D},
accounts as [{institution, name, balance}]). An entry's account list is kept
as finance.balance_snapshots on the entry's date.
"""
import json
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

import config
from . import db, finance
from .auth import journal_required
from .util import in_calendar, iso_z, number, parse_date, timestamp

bp = Blueprint("journal", __name__)
SLOTS = {"B": "breakfast", "L": "lunch", "D": "dinner"}


class DateTaken(Exception):
    """Another journal entry already exists for that calendar date."""


# ── Repository ────────────────────────────────────────────────────────────────

def entry_date(entry):
    return parse_date(entry.get("date")) or timestamp(entry.get("createdAt")).date()


def write(conn, entry):
    """Create or update one entry; returns True if it was new."""
    day = entry_date(entry)
    entry_id = str(entry["id"])
    fields = {
        "entry_id": entry_id, "entry_date": day,
        "quote": entry.get("quote") or "", "quote_author": entry.get("quoteAuthor") or "",
        "free_write": entry.get("freeWrite") or "",
        "source": "photo" if entry.get("source") == "photo" else "manual",
        "created_at": timestamp(entry.get("createdAt")),
    }
    current = conn.execute("SELECT entry_date FROM journal.entries WHERE entry_id = %s", (entry_id,)).fetchone()
    if current:
        if current["entry_date"] != day:
            if conn.execute("SELECT 1 FROM journal.entries WHERE entry_date = %s", (day,)).fetchone():
                raise DateTaken(day.isoformat())
            conn.execute("UPDATE journal.meals SET meal_date = %s WHERE source = 'journal' AND meal_date = %s",
                         (day, current["entry_date"]))
        conn.execute(
            """UPDATE journal.entries SET entry_date = %(entry_date)s, quote = %(quote)s, quote_author = %(quote_author)s,
               free_write = %(free_write)s, source = %(source)s, created_at = %(created_at)s, updated_at = now()
               WHERE entry_id = %(entry_id)s""", fields)
        was_new = False
    else:
        inserted = conn.execute(
            """INSERT INTO journal.entries (entry_date, entry_id, quote, quote_author, free_write, source, created_at)
               VALUES (%(entry_date)s, %(entry_id)s, %(quote)s, %(quote_author)s, %(free_write)s, %(source)s, %(created_at)s)
               ON CONFLICT (entry_date) DO NOTHING""", fields)
        if inserted.rowcount != 1:
            raise DateTaken(day.isoformat())
        was_new = True

    conn.execute("DELETE FROM journal.entry_tags WHERE entry_date = %s", (day,))
    tags = list(dict.fromkeys(str(t).strip() for t in entry.get("tags") or [] if str(t).strip()))
    for position, tag in enumerate(tags):
        conn.execute("INSERT INTO journal.entry_tags (entry_date, tag, position) VALUES (%s, %s, %s)", (day, tag, position))

    conn.execute("DELETE FROM journal.habit_logs WHERE entry_date = %s", (day,))
    for habit, done in (entry.get("habits") or {}).items():
        conn.execute("INSERT INTO journal.habits (habit, label) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (str(habit), str(habit).title()))
        conn.execute("INSERT INTO journal.habit_logs (entry_date, habit, done) VALUES (%s, %s, %s)", (day, str(habit), bool(done)))

    conn.execute("DELETE FROM journal.meals WHERE source = 'journal' AND source_ref = %s", (entry_id,))
    for key, slot in SLOTS.items():
        text = ((entry.get("meals") or {}).get(key) or "").strip()
        if text:
            conn.execute("INSERT INTO journal.meals (meal_date, slot, description, source, source_ref) VALUES (%s, %s, %s, 'journal', %s)",
                         (day, slot, text, entry_id))

    finance.write_journal_balances(conn, entry_id, day, entry.get("accounts") or [])
    return was_new


SELECT = """
SELECT e.entry_id, e.entry_date, e.quote, e.quote_author, e.free_write, e.source, e.created_at,
       COALESCE((SELECT jsonb_agg(t.tag ORDER BY t.position) FROM journal.entry_tags t WHERE t.entry_date = e.entry_date), '[]') AS tags,
       COALESCE((SELECT jsonb_object_agg(h.habit, h.done) FROM journal.habit_logs h WHERE h.entry_date = e.entry_date), '{}') AS habits,
       COALESCE((SELECT jsonb_object_agg(m.slot, m.description) FROM journal.meals m
                 WHERE m.source = 'journal' AND m.source_ref = e.entry_id), '{}') AS meals,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('institution', s.institution_text, 'name', s.account_text,
                                                     'balance', s.balance) ORDER BY s.position)
                 FROM finance.balance_snapshots s WHERE s.source = 'journal' AND s.source_ref = e.entry_id), '[]') AS accounts,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('activity', COALESCE(NULLIF(w.activity, ''), w.workout_type),
                                                    'type', w.workout_type, 'minutes', w.minutes, 'note', w.note,
                                                    'dogWalk', NOT wt.counts_toward_goal, 'source', w.source) ORDER BY w.workout_id)
                 FROM journal.workouts w JOIN journal.workout_types wt USING (workout_type)
                 WHERE w.workout_date = e.entry_date), '[]') AS workouts,
       a.workout_minutes, a.qualifying_workout_day, a.weight, a.total_calories
FROM journal.entries e
LEFT JOIN health.daily_activity a ON a.day = e.entry_date
"""


def to_api(row):
    meals = row["meals"] or {}
    accounts = [{"institution": a["institution"], "name": a["name"],
                 "balance": "" if a["balance"] is None else a["balance"]} for a in row["accounts"]]
    return {
        "id": row["entry_id"], "date": row["entry_date"].isoformat(),
        "quote": row["quote"], "quoteAuthor": row["quote_author"],
        "meals": {key: meals.get(slot, "") for key, slot in SLOTS.items()},
        "freeWrite": row["free_write"], "habits": row["habits"], "tags": row["tags"], "accounts": accounts,
        "source": row["source"], "createdAt": iso_z(row["created_at"]),
        # Read-only: that day's health data from the shared tables.
        "health": {
            "workouts": [{**w, "minutes": number(w["minutes"])} for w in row["workouts"]],
            "workoutMinutes": number(row["workout_minutes"]) or 0,
            "qualifyingWorkoutDay": bool(row["qualifying_workout_day"]),
            "weight": number(row["weight"]),
            "totalCalories": number(row["total_calories"]),
        },
    }


def list_entries(conn):
    return [to_api(r) for r in conn.execute(SELECT + " ORDER BY e.entry_date DESC").fetchall()]


def delete(conn, entry_id):
    row = conn.execute("DELETE FROM journal.entries WHERE entry_id = %s RETURNING entry_date", (entry_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM journal.meals WHERE source = 'journal' AND source_ref = %s", (entry_id,))
    conn.execute("DELETE FROM finance.balance_snapshots WHERE source = 'journal' AND source_ref = %s", (entry_id,))
    return True


def date_exists(conn, day):
    day = parse_date(day)
    return bool(day and conn.execute("SELECT 1 FROM journal.entries WHERE entry_date = %s", (day,)).fetchone())


def import_entries(conn, entries):
    """Copy entries from an older store; a second entry for a date is kept aside."""
    conflicts = []
    for entry in entries or []:
        if not entry.get("id") or not in_calendar(entry_date(entry)):
            conflicts.append(entry)
            continue
        try:
            with conn.transaction():
                write(conn, entry)
        except DateTaken:
            conflicts.append(entry)
    return conflicts


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/api/journal/entries")
@journal_required
def entries_route():
    with db.tx() as conn:
        return jsonify(list_entries(conn))


@bp.route("/api/journal/entry", methods=["POST"])
@journal_required
def save_route():
    data = request.get_json(force=True) or {}
    meals = data.get("meals") or {}
    entry = {
        "id": data.get("id") or str(uuid.uuid4()),
        "date": data.get("date", datetime.now().strftime("%Y-%m-%d")),
        "quote": (data.get("quote") or "").strip(),
        "quoteAuthor": (data.get("quoteAuthor") or "").strip(),
        "meals": {k: (meals.get(k) or "").strip() for k in SLOTS},
        "freeWrite": data.get("freeWrite") or "",
        "habits": data.get("habits") or {},
        "tags": data.get("tags") or [],
        "accounts": data.get("accounts") or [],
        "source": data.get("source", "manual"),
        "createdAt": data.get("createdAt", datetime.utcnow().isoformat() + "Z"),
    }
    try:
        day = datetime.strptime(str(entry["date"]), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    if not in_calendar(day):
        return jsonify({"error": "date must be between 1900 and 2100"}), 400
    try:
        with db.tx() as conn:
            was_new = write(conn, entry)
    except DateTaken:
        return jsonify({"error": f"An entry already exists for {entry['date']}"}), 409
    return jsonify({"ok": True, "entry": entry, "wasNew": was_new}), 201 if was_new else 200


@bp.route("/api/journal/entry/<entry_id>", methods=["DELETE"])
@journal_required
def delete_route(entry_id):
    with db.tx() as conn:
        if not delete(conn, entry_id):
            return jsonify({"error": "Entry not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/journal/check/<date_str>")
@journal_required
def check_route(date_str):
    with db.tx() as conn:
        return jsonify({"date": date_str, "exists": date_exists(conn, date_str)})


@bp.route("/api/journal/ocr", methods=["POST"])
@journal_required
def ocr_route():
    """Transcribe a photographed journal page with Claude; the API key stays on the server."""
    try:
        import anthropic
        if not config.ANTHROPIC_API_KEY:
            return jsonify({"error": "ANTHROPIC_API_KEY is not configured"}), 500
        data = request.get_json(force=True) or {}
        b64 = data.get("base64")
        if not b64:
            return jsonify({"error": "No image data provided"}), 400
        mime = data.get("mediaType", "image/jpeg")
        mime = mime if mime in {"image/jpeg", "image/png", "image/gif", "image/webp"} else "image/jpeg"
        msg = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY).messages.create(
            model="claude-opus-4-6", max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": (
                    "Transcribe this handwritten journal page. "
                    "Return ONLY a valid JSON object — no markdown fences — with these fields: "
                    "date (YYYY-MM-DD or empty string), quote (string), quoteAuthor (string), "
                    "meals (object with keys B, L, D — each a string), freeWrite (string). "
                    "Use empty string for any field not present.")},
            ]}])
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        return jsonify({"ok": True, "data": json.loads(raw.strip())})
    except json.JSONDecodeError as error:
        return jsonify({"error": f"Could not parse Claude's response as JSON: {error}"}), 500
    except Exception as error:
        return jsonify({"error": str(error)}), 500
