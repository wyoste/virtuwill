"""Record-level API resources: list, create, update and delete one table's rows
with the same validation, errors and response shape everywhere.

    Resource(bp, "/api/v1/health/workouts", "journal.workouts", "workout_id",
             [Field("workout_date", "date", required=True), Field("minutes", "number", low=0, high=1440)],
             date_column="workout_date")

registers GET (with ?date= or ?from=&to=), POST, PUT /<id> and DELETE /<id>,
all owner-only. Field names are the column names, so the API reads like the model.
"""
from flask import jsonify, request

from . import db
from .auth import admin_required
from .util import in_calendar, moment, number, parse_date, plain


class Invalid(ValueError):
    pass


class Field:
    def __init__(self, name, kind="text", required=False, choices=None, low=None, high=None, default=None, max_length=5000):
        self.name, self.kind, self.required = name, kind, required
        self.choices, self.low, self.high, self.default, self.max_length = choices, low, high, default, max_length

    def clean(self, value):
        if value in (None, ""):
            if self.required:
                raise Invalid(f"{self.name} is required")
            return self.default if self.kind != "text" else (self.default or "")
        if self.kind == "date":
            day = parse_date(value)
            if not in_calendar(day):
                raise Invalid(f"{self.name} must be a date (YYYY-MM-DD)")
            return day
        if self.kind == "time":
            at = moment(value)
            if not at:
                raise Invalid(f"{self.name} must be an ISO time")
            return at
        if self.kind in ("number", "int"):
            n = number(value)
            if n is None or (self.low is not None and n < self.low) or (self.high is not None and n > self.high):
                bounds = f" between {self.low} and {self.high}" if self.low is not None and self.high is not None else ""
                raise Invalid(f"{self.name} must be a number{bounds}")
            return int(n) if self.kind == "int" else n
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise Invalid(f"{self.name} must be true or false")
            return value
        text = str(value).strip()
        if len(text) > self.max_length:
            raise Invalid(f"{self.name} is too long")
        if self.choices and text not in self.choices:
            raise Invalid(f"{self.name} must be one of: {', '.join(sorted(self.choices))}")
        return text


def clean(fields, data, partial=False):
    """Validated column values from a JSON body; raises Invalid with a readable message."""
    if not isinstance(data, dict):
        raise Invalid("Expected a JSON object")
    out = {}
    for field in fields:
        if field.name in data:
            out[field.name] = field.clean(data[field.name])
        elif not partial:
            out[field.name] = field.clean(None)
    if not out:
        raise Invalid("Nothing to change")
    return out


def error(message, status=400):
    return jsonify({"error": message}), status


class Resource:
    def __init__(self, bp, path, table, pk, fields, date_column=None, order=None, extra=None, defaults=None,
                 select=None, after_write=None, prepare=None):
        self.table, self.pk, self.fields, self.date_column = table, pk, fields, date_column
        self.order = order or (f"{date_column} DESC, {pk} DESC" if date_column else pk)
        self.defaults = defaults or {}          # columns set on create, e.g. source = 'manual'
        self.select = select or f"SELECT * FROM {table}"
        self.after_write = after_write          # fn(conn, row) after create/update/delete
        self.prepare = prepare                  # fn(conn, values) → values, e.g. fill nutrition from a food
        name = table.replace(".", "_")

        bp.add_url_rule(path, f"{name}_list", admin_required(self.list), methods=["GET"])
        bp.add_url_rule(path, f"{name}_create", admin_required(self.create), methods=["POST"])
        bp.add_url_rule(f"{path}/<int:record_id>", f"{name}_update", admin_required(self.update), methods=["PUT"])
        bp.add_url_rule(f"{path}/<int:record_id>", f"{name}_delete", admin_required(self.delete), methods=["DELETE"])

    def _one(self, conn, record_id):
        row = conn.execute(f"SELECT * FROM ({self.select}) r WHERE {self.pk} = %s", (record_id,)).fetchone()
        return plain(row) if row else None

    def list(self):
        where, args = [], []
        if self.date_column:
            day, start, end = (parse_date(request.args.get(k)) for k in ("date", "from", "to"))
            if day:
                where.append(f"{self.date_column} = %s"); args.append(day)
            if start:
                where.append(f"{self.date_column} >= %s"); args.append(start)
            if end:
                where.append(f"{self.date_column} <= %s"); args.append(end)
        limit = min(int(number(request.args.get("limit")) or 500), 2000)
        sql = f"SELECT * FROM ({self.select}) r" + (" WHERE " + " AND ".join(where) if where else "")
        with db.tx() as conn:
            return jsonify([plain(r) for r in conn.execute(f"{sql} ORDER BY {self.order} LIMIT {limit}", args)])

    def create(self):
        try:
            values = clean(self.fields, request.get_json(silent=True)) | self.defaults
        except Invalid as e:
            return error(str(e))
        with db.tx() as conn:
            if self.prepare:
                try:
                    values = self.prepare(conn, values)
                except Invalid as e:
                    return error(str(e))
            cols = list(values)
            record_id = conn.execute(
                f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING {self.pk}",
                [values[c] for c in cols]).fetchone()[self.pk]
            row = self._one(conn, record_id)
            if self.after_write:
                self.after_write(conn, row)
        return jsonify(row), 201

    def update(self, record_id):
        try:
            values = clean(self.fields, request.get_json(silent=True), partial=True)
        except Invalid as e:
            return error(str(e))
        with db.tx() as conn:
            if self.prepare:
                try:
                    values = self.prepare(conn, values)
                except Invalid as e:
                    return error(str(e))
            done = conn.execute(f"UPDATE {self.table} SET {', '.join(f'{c} = %s' for c in values)} WHERE {self.pk} = %s",
                                [*values.values(), record_id]).rowcount
            if not done:
                return error("Not found", 404)
            row = self._one(conn, record_id)
            if self.after_write:
                self.after_write(conn, row)
        return jsonify(row)

    def delete(self, record_id):
        with db.tx() as conn:
            row = self._one(conn, record_id)
            if not row:
                return error("Not found", 404)
            conn.execute(f"DELETE FROM {self.table} WHERE {self.pk} = %s", (record_id,))
            if self.after_write:
                self.after_write(conn, row)
        return jsonify({"ok": True})
