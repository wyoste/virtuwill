# RunKeeper → Lakebase → VirtuWill's workouts

```
activity_logs/ ─▶ runkeeper_activities_to_bronze ─▶ prod.bronze.raw_runkeeper_activities ─▶ bronze.runkeeper_activities ─┐
gpx_maps/      ─▶ runkeeper_gpx_to_bronze        ─▶ prod.bronze.raw_runkeeper_gpx        ─▶ bronze.runkeeper_gpx      ─┤
                  └─ two notebooks ─┘                                                       └─ synced tables ─┘       │
                                                        journal.workouts + workout_routes ◀─ the app, every hour ────┘
```

1. **Two notebooks** read the export from the stage volume. Each MERGEs into its own bronze
   table:
   - [`jobs/runkeeper_activities_to_bronze.ipynb`](../jobs/runkeeper_activities_to_bronze.ipynb)
     loads the activity log, one row per activity.
   - [`jobs/runkeeper_gpx_to_bronze.ipynb`](../jobs/runkeeper_gpx_to_bronze.ipynb) loads the GPS
     tracks, one row per file.
2. **Two synced tables** copy them into the app's Lakebase database as `bronze.runkeeper_activities`
   and `bronze.runkeeper_gpx`. The app can only read them.
3. **The app** checks them every `RUNKEEPER_SYNC_MINUTES` (60 in `app.yaml`). It loads each new
   or changed activity as a workout on the date it happened, and draws its route from its track
   ([`virtuwill/runkeeper_synced.py`](../virtuwill/runkeeper_synced.py)). **Today**, **Health**
   and the **journal** show it on that date. A run with a track gets the 🗺 Map button, and a
   run ticks the Run habit.

## The export

```
/Volumes/prod/bronze/stage/runkeeper/activity_logs/cardioActivities.csv   the full history
/Volumes/prod/bronze/stage/runkeeper/gpx_maps/2026-09-28-200444.gpx       one track per activity, named for its start
```

### `runkeeper_activities_to_bronze` → `prod.bronze.raw_runkeeper_activities`

- It reads every CSV whose name contains `cardioActivities`, in any case and in any subfolder.
  You can drop a newer full export next to the old one. Each Activity Id is read once, and the
  CSV with the newest file time wins.
- Bronze stays raw. It keeps every value as the text RunKeeper wrote it. The units in the
  headers become `distance_unit` (mi/km), `speed_unit` and `climb_unit`. A column the notebook
  doesn't know goes into `_extra` (JSON).
- Its primary key is `activity_id`.

### `runkeeper_gpx_to_bronze` → `prod.bronze.raw_runkeeper_gpx`

- One row per GPX file. Its primary key is `gpx_file`, the file name, and `gpx` holds the whole
  file as text.
- `activity_start` is the start time in the file name, written the way the activity log writes
  its `Date`: `2026-09-28-200444.gpx` → `2026-09-28 20:04:44`.
- `track_started_at` is the track's first time (UTC), and `points` counts its track points. They
  let you check a file without parsing it.
- It reads a file only when it's about to write it. If two folders hold a file with the same
  name, the newest one wins.

### Both notebooks

- **Only new or changed rows are written.** Each notebook MERGEs on its key and compares
  `_row_hash`. For an activity, the hash covers its CSV values. For a track, it covers the file's
  name and size. Each written row gets a fresh `_ingested_at`, so the app loads only what
  changed.
- Each table is created with **Change Data Feed** on and a **primary key**. A synced table needs
  both.
- Each notebook takes its settings as widgets, which a job task's parameters override:
  - `activities_dir` (activities) or `gpx_dir` (tracks);
  - `table`;
  - `dry_run`: `true` counts what would be written, writes nothing and creates no table.
- Each prints a summary and returns it as the run's output (`dbutils.notebook.exit`).
- The cell tagged `run` runs the load. The tests run the other cells without Spark.

## How a track finds its activity

A track belongs to the activity whose **GPX File** column names it. If no activity names it, it
belongs to the activity whose `Date` matches the start time in the track's file name.

The two tables can land in either order:

- When an activity loads, it brings its track along if the track has already landed.
- When a track lands later, it's added to its activity's workout.
- If the track table can't be read yet, workouts load without routes, and the status page says
  why. Once the table can be read, the app reads it from the start.

## What goes where

| Bronze | VirtuWill (`journal.workouts`, source `runkeeper`) |
|---|---|
| `activity_id` | `source_ref`: a reload updates the same workout |
| `activity_date`, the time where you ran | `workout_date`: the log's local date. The GPX times are UTC, so they aren't used for the date. |
| `type` | `activity` (Running, Walking, Cycling…), and `workout_type`: Strength Training → Strength, Circuit Training/CrossFit/Bootcamp → HIIT, Yoga/Pilates/Stretching → Mobility / recovery, Other → Other, everything else → Cardio |
| `duration` (`28:30`, `1:05:12`) | `minutes`. Left blank when it's over 24 hours, e.g. a run whose timer was left going. |
| `distance`, `distance_unit` | `distance` (cardio only; 0 is left blank). If it's blank and there's a track, the track's length is used. |
| `notes` | `note` |
| pace, speed, calories, climb, heart rate, route name, GPX file | `details.runkeeper` |
| the track's `gpx` | `journal.workout_routes`: the simplified line for the map, distance, climb, start and end |

**Edits you make in the app are kept.** When an activity changes in bronze, the reload updates
its minutes, distance, details and route. It keeps the type, name, note and date you set. A
route you uploaded yourself is never replaced. If you delete a RunKeeper workout in the app, a
`{"reload": true}` load brings it back.

Walks come in as **Cardio**, so they count toward the workout goal. To mark one as a dog walk,
edit it.

## Setting it up

1. **Run the notebooks.** Import both `.ipynb` files into the workspace, or run them from a Git
   folder. Then create a Databricks job with two notebook tasks. They don't depend on each other,
   so they can run in parallel. Serverless works.

   The job needs `READ VOLUME` on `prod.bronze.stage`, and `CREATE TABLE` and `MODIFY` on
   `prod.bronze`. The defaults point at the paths above.

   The history doesn't change, so run the job once, then again whenever you drop in a new
   export. You can also schedule it daily, or trigger it when files arrive in the
   `runkeeper` folder.
2. **Create the synced tables.** Do this the same way as for Plaid. In Catalog Explorer, open
   each bronze table and choose **Create → Synced table**. Pick the database instance and the
   `virtuwill` database, then use:

   | Bronze table | Synced table | Primary key |
   |---|---|---|
   | `prod.bronze.raw_runkeeper_activities` | `bronze.runkeeper_activities` | `activity_id` |
   | `prod.bronze.raw_runkeeper_gpx` | `bronze.runkeeper_gpx` | `gpx_file` |

   **Triggered** mode is enough. Refresh both after the job runs, or add pipeline-refresh tasks
   to the job. The names are fixed in `TABLES` at the top of `virtuwill/runkeeper_synced.py`.
3. **Let the app read them.** In the Lakebase SQL editor, connect to the `virtuwill` database,
   signed in as the synced tables' owner. Run
   [`db/lakebase/grant_runkeeper_synced_tables.sql`](../db/lakebase/grant_runkeeper_synced_tables.sql).
   Every check should read `true`. If a synced table is ever deleted and made again, run the
   script again.
4. **Deploy the app from this branch.** On start it applies `db/schema/109_runkeeper.sql` and
   `110_runkeeper_gpx.sql`. They allow the `runkeeper` workout source and add
   `journal.runkeeper_bronze_load`, which stores how far each table has been read. Then the app
   starts the hourly loop.
5. **Check it.** Signed in, open `/api/v1/health/runkeeper-sync`. For each table, it shows the
   rows, the newest, and how many aren't loaded yet. It also shows how many workouts came from
   RunKeeper (with a route, and the first and last date) and the last load.
   - `POST {"dry_run": true}` previews the first batch.
   - `POST {}` loads now.
   - `POST {"reload": true}` reads everything again.

   **Settings → Diagnostics** shows the latest load as **Sync · runkeeper_bronze**.

The app loads 200 rows per transaction, and each batch saves how far it got. If a long first
load stops partway, it carries on from there. `RUNKEEPER_SYNC_MINUTES=0` turns the loop off.

**Already logged by hand?** If you logged some of these runs in the app before, they will now
appear twice on those days. Delete the copy you don't want.
