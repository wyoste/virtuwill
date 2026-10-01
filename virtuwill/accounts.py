"""One account, however many records sources made for it.

An import without an account's last four digits and a later Plaid pull with
them can leave two records for one account ("BEC 403(b)" and "BIG EXAMPLE
COMPANY, INC. 403(B) RETIREMENT PLAN ••1234"), and both count toward net worth. Merging moves everything to the kept record and turns the merged
record's name and digits into aliases of it (finance.account_aliases), so later
loads land on the kept record. The mapping is data in the database, never code.

    merge(conn, merged_id, into_id)   move everything, record the aliases and the merge
    suggestions(conn)                 pairs that look like one account, and whether that's certain
    cleanup(conn)                     the one-time cleanup: merges only the certain pairs
"""
import json
import logging
import re

from flask import Blueprint, jsonify, request

from . import db
from .auth import admin_required
from .util import plain

log = logging.getLogger(__name__)
bp = Blueprint("accounts", __name__)


# ── Merging ─────────────────────────────────────────────────────────────────

def _references(conn):
    """Every (table, column) that points at an account, read from the database's own foreign keys."""
    return [(r["tbl"], r["col"]) for r in conn.execute("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname AS col
        FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.contype = 'f' AND c.confrelid = 'finance.accounts'::regclass
          AND c.conrelid <> 'finance.account_merges'::regclass
        ORDER BY 1, 2""")]


# Rows that would repeat one the kept account already has (same bank id, same fingerprint,
# same statement period) are copies of it: dropped, not moved.
_REPEATS = {
    "finance.transactions": ["external_id", "fingerprint"],
    "finance.statements": ["period_end"],
}


def merge(conn, merged_id, into_id, reason="", by="owner"):
    """Merge one account record into another. Returns the merge record."""
    if merged_id == into_id:
        raise ValueError("Pick two different accounts")
    rows = {r["account_id"]: r for r in conn.execute(
        "SELECT * FROM finance.accounts WHERE account_id = ANY(%s) FOR UPDATE", ([merged_id, into_id],))}
    if merged_id not in rows or into_id not in rows:
        raise LookupError("Account not found")
    gone, kept = rows[merged_id], rows[into_id]
    type_of = lambda r: conn.execute("SELECT is_liability FROM finance.account_types WHERE account_type = %s",
                                     (r["account_type"],)).fetchone()["is_liability"]
    if type_of(gone) != type_of(kept) and "other" not in (gone["account_type"], kept["account_type"]):
        raise ValueError("A card or loan can't be merged with a cash or investment account")

    moved = {}
    for table, column in _references(conn):
        for key in _REPEATS.get(table, []) if column == "account_id" else []:
            n = conn.execute(f"""DELETE FROM {table} d WHERE d.{column} = %(gone)s AND d.{key} IS NOT NULL
                                 AND EXISTS (SELECT 1 FROM {table} k WHERE k.{column} = %(kept)s AND k.{key} = d.{key})""",
                             {"gone": merged_id, "kept": into_id}).rowcount
            if n:
                moved[f"{table} (repeats dropped)"] = moved.get(f"{table} (repeats dropped)", 0) + n
        n = conn.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s", (into_id, merged_id)).rowcount
        if n:
            moved[table] = n

    # The kept record keeps its name; it takes what it was missing from the merged one.
    conn.execute("""UPDATE finance.accounts k SET
                        mask = COALESCE(k.mask, %(mask)s),
                        institution = CASE WHEN k.institution = '' THEN %(institution)s ELSE k.institution END,
                        account_type = CASE WHEN k.account_type = 'other' THEN %(account_type)s ELSE k.account_type END,
                        retirement_type = CASE WHEN k.account_type = 'other' THEN %(retirement_type)s ELSE k.retirement_type END,
                        opened_on = LEAST(k.opened_on, %(opened_on)s),
                        show_in_journal = k.show_in_journal OR %(show_in_journal)s
                    WHERE k.account_id = %(into)s""", {**gone, "into": into_id})
    # Its names and digits now point at the kept record.
    for kind, alias in (("name", gone["name"]), ("name", " ".join(x for x in (gone["institution"], gone["name"]) if x)),
                        ("mask", gone["mask"] if gone["mask"] and gone["mask"] != kept["mask"] else None)):
        if alias:
            set_alias(conn, kind, alias, into_id, "merge")
    conn.execute("DELETE FROM finance.accounts WHERE account_id = %s", (merged_id,))
    return plain(conn.execute("""
        INSERT INTO finance.account_merges (merged_account_id, merged_name, merged_mask, into_account_id, reason, merged_by, rows_moved)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (merged_id, gone["name"], gone["mask"], into_id, reason, by, json.dumps(moved))).fetchone())


def set_alias(conn, kind, alias, account_id, origin="manual"):
    alias = alias.strip()
    if kind == "mask" and not re.fullmatch(r"\d{4}", alias):
        raise ValueError("Last four digits must be four digits")
    conn.execute("""INSERT INTO finance.account_aliases (kind, alias, account_id, origin) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (kind, alias) DO UPDATE SET account_id = EXCLUDED.account_id, origin = EXCLUDED.origin""",
                 (kind, alias, account_id, origin))


def account_for_mask(conn, mask):
    """The account a set of last four digits belongs to, by alias (a merged record's digits)."""
    row = conn.execute("SELECT account_id FROM finance.account_aliases WHERE kind = 'mask' AND alias = %s", (mask,)).fetchone()
    return row["account_id"] if row else None


# ── Telling that two records are one account, by name ───────────────────────

_PLAN = re.compile(r"\b(401|403|457)\s*\(?\s*([abk])\s*\)?(?![a-z])", re.I)
_IRA = re.compile(r"\b(roth|traditional|rollover|sep|simple)\b[\s-]*ira\b", re.I)
_NOISE = {"inc", "llc", "corp", "corporation", "co", "company", "the", "of", "and", "plan", "plans", "retirement",
          "savings", "saving", "trustee", "trust", "fmtc", "qualified", "account", "acct", "program", "fund", "ira",
          "roth", "traditional", "rollover", "sep", "simple", "deferred", "compensation", "401", "403", "457", "k", "a", "b"}


def plan_code(name):
    """'403b', '401k', 'roth_ira' … read from an account's name, or None."""
    m = _PLAN.search(name or "")
    if m:
        return m.group(1) + m.group(2).lower()
    m = _IRA.search(name or "")
    return f"{m.group(1).lower()}_ira" if m else None


def _words(name):
    text = _IRA.sub(" ", _PLAN.sub(" ", (name or "").lower()))
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in _NOISE]


def same_sponsor(short, long):
    """Whether a short name ("BEC 403(b)", "Widget 401(k)") names the same employer or bank as a long one
    ("BIG EXAMPLE COMPANY, INC. …", "WIDGET 401K SAVINGS PLAN"): every word of it appears in the
    long name, or it is the long name's initials."""
    s, l = _words(short), _words(long)
    if not s or not l:
        return False
    if all(w in l for w in s):
        return True
    # Initials come from the long name as written ("Big Example Company" → bec), skipping only joining words.
    spelled = [w for w in re.findall(r"[a-z0-9]+", _IRA.sub(" ", _PLAN.sub(" ", long.lower()))) if w not in ("the", "of", "and")]
    return len(s) == 1 and len(s[0]) >= 2 and "".join(w[0] for w in spelled[:len(s[0])]) == s[0]


def suggestions(conn):
    """Pairs that look like one account: a record without digits (an earlier import) and one with them
    (a later pull), of the same kind, whose names name the same sponsor and don't name different plans.

    Two rounds, each one-to-one: first pairs whose names name the same plan ("403(b)" and "403(B)"),
    then, among the records left, pairs where each record has only one possible partner. A pair is
    certain when it is the only pairing for both records and their balances, where both are known,
    are close. Only certain pairs are merged by the cleanup; the rest are offered for review."""
    accounts = [plain(r) for r in conn.execute("""
        SELECT a.account_id, a.name, a.institution, a.mask, a.account_type, a.retirement_type, t.is_liability, l.balance
        FROM finance.accounts a JOIN finance.account_types t USING (account_type)
        LEFT JOIN finance.latest_balances l USING (account_id) WHERE a.is_active""")]
    pairs = []
    for old in (a for a in accounts if not a["mask"]):
        for new in (a for a in accounts if a["mask"]):
            if old["account_type"] != new["account_type"]:
                continue
            long_name = f"{new['name']} {new['institution']}"
            if not (same_sponsor(old["name"], long_name) or same_sponsor(f"{old['institution']} {old['name']}", long_name)):
                continue
            a, b = plan_code(old["name"]) or old["retirement_type"], plan_code(new["name"])
            if a and b and a != b:
                continue
            pairs.append({"old": old, "new": new, "named": bool(a and b), "close": _close(old["balance"], new["balance"])})

    settled, out = set(), []
    for named_round in (True, False):
        live = [p for p in pairs if p["old"]["account_id"] not in settled and p["new"]["account_id"] not in settled]
        for p in (p for p in live if p["named"] == named_round):
            o, n = p["old"]["account_id"], p["new"]["account_id"]
            only = (sum(q["old"]["account_id"] == o for q in live if q["named"] or not named_round) == 1
                    and sum(q["new"]["account_id"] == n for q in live if q["named"] or not named_round) == 1)
            certain = only and p["close"] is not False
            out.append({"merge": n, "into": o, "merge_name": p["new"]["name"], "merge_mask": p["new"]["mask"],
                        "into_name": p["old"]["name"], "certain": certain,
                        "why": ("same sponsor and plan" if named_round else "same sponsor, the only match left")
                               + ({None: "", True: "; balances close", False: "; balances far apart"}[p["close"]])})
        settled |= {x for s in out if s["certain"] for x in (s["merge"], s["into"])}
    # A record already settled isn't offered again with another partner.
    return [s for s in out if s["certain"] or not ({s["merge"], s["into"]} & settled)]


def _close(a, b):
    """Whether two balances of one account, read days apart, agree (within 10%); None when either is unknown."""
    if a is None or b is None:
        return None
    a, b = float(a), float(b)
    return abs(a - b) <= 0.10 * max(abs(a), abs(b), 1)


def cleanup(conn):
    """The one-time cleanup: merge every pair that is certain. Uncertain ones stay as suggestions."""
    done = []
    for s in suggestions(conn):
        if s["certain"]:
            done.append(merge(conn, s["merge"], s["into"], reason="cleanup: " + s["why"], by="cleanup"))
    if done:
        log.info("Account cleanup merged %d duplicate account records", len(done))
    return done


# ── API ─────────────────────────────────────────────────────────────────────

@bp.route("/api/v1/money/account-merges")
@admin_required
def merges_view():
    with db.tx() as conn:
        return jsonify({
            "suggestions": suggestions(conn),
            "merges": [plain(r) for r in conn.execute("""SELECT m.*, a.name AS into_name FROM finance.account_merges m
                                                         JOIN finance.accounts a ON a.account_id = m.into_account_id
                                                         ORDER BY merged_at DESC LIMIT 100""")],
            "aliases": [plain(r) for r in conn.execute("""SELECT x.kind, x.alias, x.account_id, x.origin, a.name AS account_name
                                                          FROM finance.account_aliases x JOIN finance.accounts a USING (account_id)
                                                          ORDER BY a.name, x.kind, x.alias""")],
        })


@bp.route("/api/v1/money/accounts/<account_id>/merge", methods=["POST"])
@admin_required
def merge_route(account_id):
    """Merge this account into another: {into}."""
    into = (request.get_json(silent=True) or {}).get("into")
    try:
        with db.tx() as conn:
            return jsonify(merge(conn, account_id, str(into or ""), reason="merged by the owner")), 201
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/v1/money/account-aliases", methods=["POST", "DELETE"])
@admin_required
def alias_route():
    """Add {kind: name|mask, alias, account_id}, or remove {kind, alias}."""
    body = request.get_json(silent=True) or {}
    kind, alias = body.get("kind"), str(body.get("alias") or "").strip()
    if kind not in ("name", "mask") or not alias or len(alias) > 200:
        return jsonify({"error": "kind must be name or mask, with an alias"}), 400
    with db.tx() as conn:
        if request.method == "DELETE":
            conn.execute("DELETE FROM finance.account_aliases WHERE kind = %s AND alias = %s", (kind, alias))
            return jsonify({"ok": True})
        if not conn.execute("SELECT 1 FROM finance.accounts WHERE account_id = %s", (body.get("account_id"),)).fetchone():
            return jsonify({"error": "Account not found"}), 404
        try:
            set_alias(conn, kind, alias, body["account_id"], "manual")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True}), 201
