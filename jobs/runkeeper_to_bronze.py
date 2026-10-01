# Databricks notebook source
"""RunKeeper → prod.bronze.raw_runkeeper_activities: a Databricks job that lands the RunKeeper export.

The export sits in the stage volume:

    /Volumes/prod/bronze/stage/runkeeper/activities/   CardioActivities.csv (the full history; more than one
                                                       export may be dropped here, in subfolders or not)
    /Volumes/prod/bronze/stage/runkeeper/gpx_maps/     2019-05-04-163509.gpx … one track per activity, named
                                                       for when it started

Each run reads every CardioActivities CSV, keeps one row per Activity Id (the newest file wins), finds the
activity's GPX file (the CSV's "GPX File" column, else the file named for the activity's start), and MERGEs
the result into the bronze table on activity_id. Only new or changed activities are written, and each
written row gets a fresh _ingested_at, so the app (virtuwill/runkeeper_synced.py, reading the Lakebase
synced copy) loads only what changed. The GPX file goes in whole, as text, in the gpx column.

Bronze stays raw: every CSV value is kept as the text RunKeeper wrote. The units in the headers
("Distance (mi)" or "Distance (km)", "Climb (ft)" or "Climb (m)") become distance_unit and climb_unit, and
any column this job doesn't know is kept in _extra (JSON).

    python jobs/runkeeper_to_bronze.py                 land what's new or changed
    python jobs/runkeeper_to_bronze.py --dry-run       count what would be written; nothing saved

Settings (job parameters or environment variables):

    RUNKEEPER_ACTIVITIES_DIR   default /Volumes/prod/bronze/stage/runkeeper/activities
    RUNKEEPER_GPX_DIR          default /Volumes/prod/bronze/stage/runkeeper/gpx_maps
    RUNKEEPER_TABLE            default prod.bronze.raw_runkeeper_activities

The table has Change Data Feed on and activity_id as its primary key, which is what a Lakebase synced
table needs (docs/runkeeper-lakebase.md).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ACTIVITIES_DIR = "/Volumes/prod/bronze/stage/runkeeper/activities"
GPX_DIR = "/Volumes/prod/bronze/stage/runkeeper/gpx_maps"
TABLE = "prod.bronze.raw_runkeeper_activities"

# RunKeeper's headers → bronze columns. A header's unit, when it has one, is kept in the *_unit column.
HEADERS = {
    "activity id": "activity_id", "date": "activity_date", "type": "type", "route name": "route_name",
    "distance": "distance", "duration": "duration", "average pace": "average_pace", "average speed": "average_speed",
    "calories burned": "calories_burned", "climb": "climb", "average heart rate": "average_heart_rate_bpm",
    "friend's tagged": "friends_tagged", "friends tagged": "friends_tagged", "notes": "notes", "gpx file": "gpx_file",
}
UNITS = {"distance": "distance_unit", "climb": "climb_unit", "average_speed": "speed_unit"}
CSV_COLUMNS = ["activity_id", "activity_date", "type", "route_name", "distance", "distance_unit", "duration",
               "average_pace", "average_speed", "speed_unit", "calories_burned", "climb", "climb_unit",
               "average_heart_rate_bpm", "friends_tagged", "notes", "gpx_file"]
# Every column of the bronze table, in order. All text except the two times.
COLUMNS = CSV_COLUMNS + ["gpx", "gpx_bytes", "_extra", "_source_file", "_file_modified_at", "_row_hash", "_ingested_at"]
DDL = f"""CREATE TABLE IF NOT EXISTS {{table}} (
    activity_id STRING NOT NULL COMMENT 'The Activity Id from RunKeeper',
    {", ".join(f"{c} STRING" for c in CSV_COLUMNS[1:])},
    gpx STRING COMMENT 'The whole GPX file, as text; NULL when the activity has none',
    gpx_bytes BIGINT,
    _extra STRING COMMENT 'CSV columns this job does not know, as JSON',
    _source_file STRING,
    _file_modified_at TIMESTAMP,
    _row_hash STRING COMMENT 'What the row was written from; an unchanged activity is not written again',
    _ingested_at TIMESTAMP COMMENT 'When this row was last written',
    CONSTRAINT raw_runkeeper_activities_pk PRIMARY KEY (activity_id)
) USING DELTA
COMMENT 'RunKeeper activities (CardioActivities.csv) with their GPX tracks; landed by jobs/runkeeper_to_bronze.py'
TBLPROPERTIES (delta.enableChangeDataFeed = true)"""


# ── Reading the export (plain Python: the volume is a mounted folder) ────────

def column(header):
    """A RunKeeper header as (bronze column, unit or None): 'Distance (mi)' → ('distance', 'mi')."""
    m = re.match(r"\s*(.*?)\s*(?:\(([^)]*)\))?\s*$", header or "")
    name, unit = m.group(1).lower(), m.group(2)
    known = HEADERS.get(name)
    if known == "average_heart_rate_bpm":
        unit = None                                    # bpm is in the column's name already
    return known, (unit.strip() if unit and known in UNITS else None)


def read_csv(text, source_file, modified_at):
    """One CardioActivities CSV's rows as bronze dicts (without the GPX)."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    headers = next(reader, None) or []
    mapped = [column(h) for h in headers]
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        row = {c: None for c in CSV_COLUMNS}
        extra = {}
        for header, (name, unit), value in zip(headers, mapped, values):
            if name:
                row[name] = value.strip() if value.strip() else None
                if unit:
                    row[UNITS[name]] = unit
            elif value.strip():
                extra[header] = value
        if not row["activity_id"]:
            # Older exports may lack ids: the start time and type name the activity.
            if not row["activity_date"]:
                continue
            row["activity_id"] = f"{row['activity_date']}|{row['type'] or ''}"
        row["_extra"] = json.dumps(extra, sort_keys=True) if extra else None
        row["_source_file"] = source_file
        row["_file_modified_at"] = modified_at
        rows.append(row)
    return rows


def _files(folder, suffix):
    """Every file under folder (any depth) whose name ends with suffix, case aside: [(path, size, modified_at)]."""
    out = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            if name.lower().endswith(suffix):
                path = os.path.join(root, name)
                st = os.stat(path)
                out.append((path, st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)))
    return out


def read_activities(folder):
    """Every activity in the CardioActivities CSVs under folder: one per activity_id, the newest file winning."""
    found = [f for f in _files(folder, ".csv") if "cardioactivities" in os.path.basename(f[0]).lower()]
    latest = {}
    for path, _size, modified_at in sorted(found, key=lambda f: (f[2], f[0])):
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        for row in read_csv(text, path, modified_at):
            latest[row["activity_id"]] = row
    return list(latest.values()), [f[0] for f in found]


def gpx_index(folder):
    """GPX files by lower-case name: {name: (path, size)}."""
    return {os.path.basename(p).lower(): (p, size) for p, size, _ in _files(folder, ".gpx")}


def gpx_name_for(activity_date):
    """The file RunKeeper names for an activity's start: '2019-05-04 16:35:09' → '2019-05-04-163509.gpx'."""
    try:
        start = datetime.fromisoformat(str(activity_date).strip().replace("T", " "))
    except ValueError:
        return None
    return start.strftime("%Y-%m-%d-%H%M%S.gpx")


def match_gpx(row, index):
    """The (path, size) of an activity's GPX file, or None."""
    for name in (row.get("gpx_file"), gpx_name_for(row.get("activity_date"))):
        if name and os.path.basename(name).lower() in index:
            return index[os.path.basename(name).lower()]
    return None


def row_hash(row, gpx):
    """What a row is written from: its CSV values and its GPX file's name and size."""
    basis = [row.get(c) for c in CSV_COLUMNS] + [row.get("_extra"), os.path.basename(gpx[0]) if gpx else None,
                                                 gpx[1] if gpx else None]
    return hashlib.sha256(json.dumps(basis).encode()).hexdigest()


def plan(activities, index, existing):
    """The rows to write (new or changed), each with its GPX path; and a summary.
    existing: {activity_id: _row_hash} already in the table."""
    write, unchanged, with_gpx, matched = [], 0, 0, set()
    for row in activities:
        gpx = match_gpx(row, index)
        if gpx:
            with_gpx += 1
            matched.add(gpx[0])
        h = row_hash(row, gpx)
        if existing.get(row["activity_id"]) == h:
            unchanged += 1
            continue
        write.append({**row, "_row_hash": h, "_gpx_path": gpx[0] if gpx else None, "gpx_bytes": gpx[1] if gpx else None})
    summary = {"activities": len(activities), "with_gpx": with_gpx, "new_or_changed": len(write), "unchanged": unchanged,
               "gpx_files": len(index), "gpx_files_without_activity": len({p for p, _ in index.values()} - matched)}
    return write, summary


def with_gpx_text(row):
    """The row as written: the GPX file's text in gpx."""
    out = {c: row.get(c) for c in COLUMNS if c != "_ingested_at"}
    path = row.get("_gpx_path")
    out["gpx"] = Path(path).read_text(encoding="utf-8", errors="replace") if path else None
    return out


# ── Writing the table (Spark) ────────────────────────────────────────────────

def _spark():
    from pyspark.sql import SparkSession
    return SparkSession.builder.getOrCreate()


def existing_hashes(spark, table, create=True):
    """{activity_id: _row_hash} already landed; the table is made first unless this is a dry run."""
    if create:
        spark.sql(DDL.format(table=table))
    elif not spark.catalog.tableExists(table):
        return {}
    return {r["activity_id"]: r["_row_hash"] for r in spark.table(table).select("activity_id", "_row_hash").collect()}


def merge(spark, table, rows, batch=200):
    """Upsert rows on activity_id, a batch at a time (each GPX file is read only as its batch is written)."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import LongType, StringType, StructField, StructType, TimestampType
    schema = StructType([StructField(c, LongType() if c == "gpx_bytes" else TimestampType() if c == "_file_modified_at"
                                     else StringType()) for c in COLUMNS if c != "_ingested_at"])
    for i in range(0, len(rows), batch):
        part = [with_gpx_text(r) for r in rows[i:i + batch]]
        df = spark.createDataFrame([[r[f.name] for f in schema.fields] for r in part], schema)
        df.withColumn("_ingested_at", F.current_timestamp()).createOrReplaceTempView("runkeeper_batch")
        spark.sql(f"""MERGE INTO {table} t USING runkeeper_batch s ON t.activity_id = s.activity_id
                      WHEN MATCHED AND t._row_hash <> s._row_hash THEN UPDATE SET *
                      WHEN NOT MATCHED THEN INSERT *""")


def _setting(name, default, dbutils=None):
    if os.environ.get(name):
        return os.environ[name]
    if dbutils is not None:
        try:
            dbutils.widgets.text(name, default)
            return dbutils.widgets.get(name) or default
        except Exception:
            pass
    return default


def run(dry_run=False, spark=None):
    try:
        from pyspark.dbutils import DBUtils
        dbutils = DBUtils(spark or _spark())
    except Exception:
        dbutils = None
    activities_dir = _setting("RUNKEEPER_ACTIVITIES_DIR", ACTIVITIES_DIR, dbutils)
    gpx_dir = _setting("RUNKEEPER_GPX_DIR", GPX_DIR, dbutils)
    table = _setting("RUNKEEPER_TABLE", TABLE, dbutils)
    activities, files = read_activities(activities_dir)
    if not files:
        sys.exit(f"No CardioActivities CSV under {activities_dir}")
    spark = spark or _spark()
    rows, summary = plan(activities, gpx_index(gpx_dir), existing_hashes(spark, table, create=not dry_run))
    if not dry_run and rows:
        merge(spark, table, rows)
    print(json.dumps({"table": table, "csv_files": files, "dry_run": dry_run, **summary}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count what would be written; write nothing")
    args, _ = parser.parse_known_args(argv)      # a notebook task passes its own arguments
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    code = main()
    if code:
        sys.exit(code)
