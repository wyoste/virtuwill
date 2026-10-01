# RunKeeper → Lakebase → VirtuWill's workouts

```
stage volume ─▶ prod.bronze.raw_runkeeper_activities ─▶ Lakebase synced table ─▶ journal.workouts + workout_routes
└─ jobs/runkeeper_to_bronze.py ─┘                       └─ synced-table pipeline ─┘  └─ the app, every hour ─┘
```

1. **The job** (`jobs/runkeeper_to_bronze.py`) reads the export from the stage volume and MERGEs
   it into `prod.bronze.raw_runkeeper_activities`, one row per activity, with its GPX file.
2. **A synced table** copies that bronze table into the app's Lakebase database as
   `bronze.runkeeper_activity`. The app can only read it.
3. **The app** checks the synced table every `RUNKEEPER_SYNC_MINUTES` (60 in `app.yaml`). It loads
   each new or changed activity as a workout on the date it happened, with its route
   ([`virtuwill/runkeeper_synced.py`](../virtuwill/runkeeper_synced.py)). **Today**, **Health** and
   the **journal** show it on that date. A run with a track gets the 🗺 Map button, and a run
   ticks the Run habit.

## The export

```
/Volumes/prod/bronze/stage/runkeeper/activity_logs/cardioActivities.csv   the full history
/Volumes/prod/bronze/stage/runkeeper/gpx_maps/2026-09-28-200444.gpx      one track per activity, named for its start
```

- The job reads every CSV whose name contains `cardioActivities`, in any case and in any
  subfolder. You can drop a newer full export next to the old one. Each Activity Id is read
  once, and the CSV with the newest file time wins.
- It finds each activity's track from the CSV's **GPX File** column. If that column is empty, it
  looks for the file named for the activity's start time (`Date` 2026-09-28 20:04:44 →
  `2026-09-28-200444.gpx`).
- Bronze stays raw. It keeps every CSV value as the text RunKeeper wrote it, and the whole GPX
  file as text in `gpx`. The units in the headers become `distance_unit` (mi/km),
  `speed_unit` and `climb_unit`. A column the job doesn't know goes into `_extra` (JSON).
- **Only new or changed activities are written.** `_row_hash` covers the CSV values plus the
  GPX file's name and size. An unchanged activity isn't written again, so its `_ingested_at`
  stays the same and the app doesn't load it again.
- The table is created with **Change Data Feed** on and `activity_id` as its **primary key**. A
  synced table needs both.

## What goes where

| Bronze | VirtuWill (`journal.workouts`, source `runkeeper`) |
|---|---|
| `activity_id` | `source_ref`: a reload updates the same workout |
| `activity_date`, the time where you ran | `workout_date`: the CSV's local date. The GPX times are UTC, so they aren't used for the date. |
| `type` | `activity` (Running, Walking, Cycling…), and `workout_type`: Strength Training → Strength, Circuit Training/CrossFit/Bootcamp → HIIT, Yoga/Pilates/Stretching → Mobility / recovery, Other → Other, everything else → Cardio |
| `duration` (`28:30`, `1:05:12`) | `minutes`. Left blank when it's over 24 hours, e.g. a run whose timer was left going. |
| `distance`, `distance_unit` | `distance` (cardio only; 0 is left blank). If it's blank and there's a track, the track's length is used. |
| `notes` | `note` |
| pace, speed, calories, climb, heart rate, route name, GPX file | `details.runkeeper` |
| `gpx` | `journal.workout_routes`: the simplified line for the map, distance, climb, start and end |

**Edits you make in the app are kept.** If the bronze row for an activity changes, the reload
updates its minutes, distance, details and route. It keeps the type, name, note and date you
set. A route you uploaded yourself is never replaced. If you delete a RunKeeper workout in the
app, a `{"reload": true}` load brings it back.

Walks come in as **Cardio**, so they count toward the workout goal. To mark one as a dog walk,
edit it.

## Setting it up

1. **Run the job.** Create a Databricks job with one task. Use a Python script task on
   `jobs/runkeeper_to_bronze.py` from the repo, or import the file as a notebook (it starts with
   `# Databricks notebook source`). Serverless works. The job needs `READ VOLUME` on
   `prod.bronze.stage` and `CREATE TABLE`/`MODIFY` on `prod.bronze`. The defaults point at the
   paths above. Change them with the `RUNKEEPER_ACTIVITIES_DIR`, `RUNKEEPER_GPX_DIR` and
   `RUNKEEPER_TABLE` job parameters or environment variables. `--dry-run` counts what it would
   write and writes nothing. Each run prints a summary: activities, how many have a track, new or
   changed, unchanged, and GPX files with no activity.

   The history doesn't change, so run the job once, then again whenever you drop a new export.
   You can also schedule it daily, or trigger it on file arrival on the `activity_logs` folder.
2. **Create the synced table.** Do this the same way as for Plaid. In Catalog Explorer, open
   `prod.bronze.raw_runkeeper_activities` and choose **Create → Synced table**. Pick the
   database instance and the `virtuwill` database, name it `bronze.runkeeper_activity`, and use
   primary key `activity_id`. **Triggered** mode is enough; refresh it after the job runs (or
   add a pipeline-refresh task to the job). The name is fixed in `TABLE` at the top of
   `virtuwill/runkeeper_synced.py`.
3. **Let the app read it.** In the Lakebase SQL editor, connected to the `virtuwill` database
   and signed in as the synced table's owner, run
   [`db/lakebase/grant_runkeeper_synced_table.sql`](../db/lakebase/grant_runkeeper_synced_table.sql).
   Every check should read `true`. If the synced table is ever deleted and made again, run the
   script again.
4. **Deploy the app from this branch.** On start it applies `db/schema/109_runkeeper.sql`,
   which allows the `runkeeper` workout source and adds `journal.runkeeper_bronze_load`, then
   starts the hourly loop.
5. **Check it.** Signed in, open `/api/v1/health/runkeeper-sync`. It shows the synced table's
   rows, how many have a track, how many aren't loaded yet, how many workouts came from
   RunKeeper (with the first and last date), and the last load. `POST` `{"dry_run": true}`
   previews the first batch. `POST {}` loads now. `{"reload": true}` reads everything again.
   **Settings → Diagnostics** shows the latest load as **Sync · runkeeper_bronze**.

The first load runs 200 activities per transaction, and each batch saves how far it got. If a
long first load stops partway, it carries on from there. `RUNKEEPER_SYNC_MINUTES=0` turns the
loop off.

**Already logged by hand?** If you logged some of these runs in the app before, they will now
appear twice on those days. Delete the copy you don't want.
