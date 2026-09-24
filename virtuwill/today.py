"""Today: one day across every domain, for the workspace's landing screen.

Everything here is read from the shared tables; each record is still written
through its own screen's endpoints (journal, health, money).
"""
from datetime import timedelta

from flask import Blueprint, jsonify, request

from . import db, journal
from .auth import admin_required
from .util import in_calendar, parse_date, plain

bp = Blueprint("today", __name__)


def _rows(conn, sql, *args):
    return [plain(r) for r in conn.execute(sql, args)]


@bp.route("/api/v1/today")
@admin_required
def today():
    with db.tx() as conn:
        day = parse_date(request.args.get("date")) or conn.execute("SELECT current_date AS d").fetchone()["d"]
        if not in_calendar(day):
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        entry = conn.execute(journal.SELECT + " WHERE e.entry_date = %s", (day,)).fetchone()
        activity = conn.execute("SELECT * FROM health.daily_activity WHERE day = %s", (day,)).fetchone()
        week_start = day - timedelta(days=day.isoweekday() - 1)
        through = conn.execute("SELECT MAX(posted_on) AS d FROM finance.transactions").fetchone()["d"]
        return jsonify({
            "date": day.isoformat(),
            "entry": journal.to_api(entry) if entry else None,
            "habits": _rows(conn, """SELECT habit, label, polarity, derived_from, done, origin FROM journal.day_habits
                                     WHERE day = %s ORDER BY polarity, label""", day),
            "activity": plain(activity) if activity else None,
            "week": plain(conn.execute("SELECT * FROM health.weekly_workout_progress WHERE week_start = %s",
                                       (week_start,)).fetchone() or {"week_start": week_start}),
            "goals": _rows(conn, "SELECT * FROM health.goal_progress ORDER BY metric"),
            "workouts": _rows(conn, """SELECT w.*, NOT t.counts_toward_goal AS is_dog_walk FROM journal.workouts w
                                       JOIN journal.workout_types t USING (workout_type) WHERE workout_date = %s
                                       ORDER BY workout_id""", day),
            "meals": _rows(conn, """SELECT * FROM journal.meals WHERE meal_date = %s
                                    ORDER BY array_position(ARRAY['breakfast','lunch','dinner','snack','meal'], slot), meal_id""", day),
            "weighIns": _rows(conn, """SELECT * FROM health.body_measurements WHERE measured_on = %s
                                       ORDER BY measured_at NULLS LAST, measurement_id""", day),
            "drinks": _rows(conn, "SELECT * FROM health.alcohol WHERE drink_date = %s ORDER BY drink_id", day),
            "money": {
                "through": through.isoformat() if through else None,
                "transactions": _rows(conn, """
                    SELECT t.transaction_id, t.merchant, t.amount, t.kind, t.category, a.name AS account_name,
                           EXISTS (SELECT 1 FROM finance.receipt_payments p WHERE p.transaction_id = t.transaction_id) AS has_receipt
                    FROM finance.transactions t LEFT JOIN finance.accounts a USING (account_id)
                    WHERE t.posted_on = %s ORDER BY t.amount DESC""", day),
                "weekSpending": float(conn.execute("""SELECT COALESCE(SUM(amount), 0) AS s FROM finance.spending
                                                      WHERE day BETWEEN %s AND %s""", (week_start, day)).fetchone()["s"]),
            },
        })
