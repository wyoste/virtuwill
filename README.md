# VirtuWill

A personal digital life dashboard — private journal, music portfolio, garden journal, and more.
Built with Flask + modular vanilla JS. No build step required.

## Private Finance and Health trackers

Sign in to **Admin → Finances** or **Admin → Health goals**. These tabs host the
existing Yoste tracker apps, preserving their full interfaces and import/export
formats. The admin overview also links to both trackers. Updates save to the
server automatically; a status bar confirms saves or explains failures.

Before using these tabs, set `SECRET_KEY` to a random value of at least 32
characters and `ADMIN_PASSWORD` to a unique value of at least 12 characters.
The repository's documented development defaults cannot unlock tracker storage.
The existing admin UI uses username `admin`. Use HTTPS for a deployed app.

On first visit, upload the original **Yoste-Finance.html** and **Yoste-Health.html**
into their respective tabs. Their embedded records, settings, food references,
recipes, and calculations are preserved. If you have newer entries stored in the
standalone apps' browser storage, export a JSON backup there and restore it in
the corresponding admin tracker after uploading the HTML.

Alternatively, import on the deployment host:

```bash
python scripts/import_trackers.py --finance /private/Yoste-Finance.html --health /private/Yoste-Health.html
```

Personal HTML and JSON state live in the Lakebase `virtuwill.trackers` table.
Neither the source HTML nor private records are shipped in the public repository
or public static assets. Do not add your original HTML files to `static/`,
`templates/`, or the public portfolio uploader. Use the trackers' JSON exports
for portable backups.

The imported apps run in sandboxed frames with no network or parent-page access.
Admin-only endpoints, CSRF tokens, no-store responses, and revision checks protect
the bridge to server storage. A stale tab cannot silently overwrite newer data:
export its unsaved edits, reload, and reconcile using the backup. Failed saves
remain visible in the tracker until reload; closing or reloading while a save is
pending triggers a warning. Existing browser-local data is not read automatically
across origins or devices.

### Deploying on Databricks Apps

- Merging to GitHub does not update the app. Redeploy it (Apps UI → Deploy, or
  `databricks apps deploy`) from the updated source, then hard-refresh the browser.
- The Finance and Health tabs appear in the Admin sidebar only after signing in as admin.
- `app.yaml` reads `SECRET_KEY` and `ADMIN_PASSWORD` from app secret resources with
  the resource keys `secret-key` and `admin-password` (app → Edit → Resources →
  Secret). Deployment fails if either resource is missing. The admin password is
  the `admin-password` secret value, and the trackers need `SECRET_KEY` of 32+
  characters and `ADMIN_PASSWORD` of 12+ characters.
- Attach a Lakebase database so data survives redeploys (see below).

### Data: one relational model in Lakebase

Every page reads and writes the relational model in [`db/schema/`](db/schema)
(documented in [`db/README.md`](db/README.md) and, with diagrams,
[`docs/lakebase-model.html`](docs/lakebase-model.html)). The Python package
`virtuwill/` has one module per domain — `journal`, `health`, `finance`, `garden`,
`music`, `content`, `travel`, `site` — each with its queries and its API routes.
The page API shapes did not change.

A database is required. Without one, pages load but every data request returns
503 with a clear message; Admin → Settings → Diagnostics says what is missing.

Set it up once:

1. Create a Lakebase database instance in the workspace (Compute → Lakebase / Database
   instances → Create), if you don't have one.
2. Open the app → **Edit** → **Resources** → **Add resource** → **Database**. Choose the
   instance and database (`databricks_postgres` by default) with permission
   **Can connect and create**. Save. Databricks then sets `PGHOST`, `PGDATABASE`,
   `PGUSER` and related variables; the app signs in with its own OAuth token.
3. Deploy. On first start the app creates the schemas and moves existing data in.

**Schema changes are versioned.** `db/schema/*.sql` run once each, in name order,
recorded with a checksum in `virtuwill.schema_versions`, under an advisory lock so
several workers starting together never race. A file that has run is never edited:
a change to the model is a new, higher-numbered file. An edited file is logged and
flagged in Diagnostics rather than re-run.

**The one-time move** (`virtuwill/migrate.py`, recorded as `relational_v1`) runs on
the first start of this version and copies, without deleting anything:

- journal entries, tags, habits, meals and account balances from the earlier
  `journal`/`health` tables, which are renamed to `legacy_journal_v0` /
  `legacy_health_v0` and kept;
- workouts and weigh-ins logged on the dashboard;
- garden, garden photos, music catalog, blog, messages, portfolio uploads, account
  template, travel pins and visited places, and garden gallery text from the earlier
  `virtuwill.collections` documents (or, on a new database, the committed `data/*.json`);
- uploaded files from `virtuwill.media` into `core.media_assets`;
- the Finance and Health tracker states, re-projected into the finance, health and
  journal tables.

A step that fails is logged and reported in Diagnostics without blocking the rest.

**The trackers stay the editors for their data** until native screens replace them.
Each save projects the tracker's records into the relational tables. Every tracker
record keeps one row and one id across saves: unchanged records are left alone, new
ones are added, deleted ones removed. The save response and the tracker status bar
report separately whether the record saved and whether the dashboards updated. Goals
derived from the Health tracker's settings (weight, BMI, daily calories) are changed
in the tracker; the dashboard refuses to edit them so a later save can't revert it.

**Files.** Every file is a `core.media_assets` row. Uploads also keep their bytes in
the database and are written back under `static/` after a redeploy. Only public
assets are served to visitors. Uploaded portfolio pages are served with a CSP
sandbox so their scripts never run on the site's origin.

**Settings.** Admin → Settings holds site switches (the "Chat with Will" button is
off by default) and Diagnostics: deployed commit, database, schema version, tracker
installs and the latest sync of each tracker.

Tests run against a scratch Postgres database whose app schemas they reset:

```bash
VIRTUWILL_TEST_PG="host=localhost dbname=scratch user=app password=pw sslmode=disable" \
  python -m unittest discover -s tests -t . -v
for f in static/js/*.js; do node --check "$f"; done
```

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # then set the PG* lines to a local PostgreSQL database
python app.py
# → http://127.0.0.1:5000
```

Any PostgreSQL 14+ works for development, e.g.
`createdb virtuwill` and `PGHOST=localhost PGDATABASE=virtuwill PGUSER=$USER PGPASSWORD=… PGSSLMODE=disable`.
The first start builds the schema and loads the committed `data/*.json`.

---

## Project structure

```
virtuwill/
│
├── app.py               Entry point (gunicorn app:app)
├── config.py            Environment variable loader
├── virtuwill/           Flask app: one module per domain (queries + API routes),
│                        db.py (pool, versioned schema), migrate.py (one-time moves)
├── db/schema/           The data model, applied in name order (see db/README.md)
├── requirements.txt
├── .env.example         → copy to .env
│
├── templates/
│   └── index.html       SPA shell — pure HTML, no inline JS or CSS
│
├── static/
│   ├── css/
│   │   ├── theme.css    Design tokens + shared components (nav, buttons, toasts, modals)
│   │   ├── home.css     Home page styles
│   │   ├── journal.css  Journal sidebar, timeline, entry cards, gate
│   │   ├── music.css    Music page — dark carousel, player, tracklist
│   │   └── garden.css   Garden page — sunflower animation layout
│   │
│   ├── js/
│   │   ├── app.js       Router (go), toast, bootstrap — loads last
│   │   ├── journal.js   Journal module  — window.VW.Journal
│   │   ├── music.js     Music module    — window.VW.Music
│   │   └── garden.js    Garden module   — window.VW.Garden
│   │
│   └── music/           Drop real album folders here (see music/README.md)
│
├── mock_data/
│   ├── journal_entries.json   11 sample entries (loaded if data/ is empty)
│   └── music_library.json     5 albums + singles (always used as music source)
│
└── data/                      Content loaded into a new database on first start
```

---

## Architecture

### Frontend — modular SPA

`index.html` is a pure HTML shell — navigation, page divs, two modals, four `<script>` tags.
It contains zero inline JavaScript and zero inline styles.

Each JS module is an IIFE that registers itself on `window.VW` before `app.js` loads:

| Module | Responsibility |
|---|---|
| `garden.js` → `VW.Garden` | Sunflower canvas animation lifecycle |
| `music.js`  → `VW.Music`  | Library data, carousel, player, tracklist |
| `journal.js`→ `VW.Journal`| Auth, CRUD, timeline, search, OCR upload |
| `app.js`    → global      | `go()` router, `toast()`, bootstrap |

CSS is split by feature — `theme.css` owns design tokens and shared components; page-specific
files own only their own selectors.

### Backend — Flask API

`virtuwill.create_app()` serves the page shell and the JSON API, one blueprint per
domain. Page navigation happens client-side via `go()` in `app.js`; `#page` links
(e.g. `/#resume`) open that page, and a private page opens after sign-in.

### Data layer

Lakebase (PostgreSQL); see "Data: one relational model in Lakebase" above.

---

## API reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/journal/unlock` | — | Validate passphrase, set session |
| POST | `/api/journal/logout` | — | Clear journal session |
| GET  | `/api/journal/status` | — | Check if session is active |
| GET  | `/api/journal/entries` | Session | Return all entries |
| POST | `/api/journal/entry` | Session | Create or update entry (upsert by id) |
| DELETE | `/api/journal/entry/<id>` | Session | Delete entry |
| GET  | `/api/journal/check/<date>` | Session | Check if date exists |
| POST | `/api/journal/ocr` | Session | Proxy image to Claude Vision OCR |
| GET  | `/api/music` | — | Return music library |

### Entry schema

All fields use camelCase — consistent between frontend and backend:

```json
{
  "id":          "uuid-or-client-generated-string",
  "date":        "YYYY-MM-DD",
  "quote":       "string",
  "quoteAuthor": "string",
  "meals":       { "B": "Breakfast", "L": "Lunch", "D": "Dinner" },
  "freeWrite":   "string",
  "source":      "manual | photo",
  "createdAt":   "ISO 8601"
}
```

---

## Photo OCR

The Upload Photo button in the journal opens a drag-and-drop modal.
Selected images are read as base64 client-side, then POST'd to `/api/journal/ocr`.
Flask proxies the request to Claude Vision — your Anthropic API key never reaches the browser.

To enable:
```env
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Supported formats: JPEG, PNG, WEBP, HEIC (remapped to JPEG on the server).

---

## Adding real music

Drop files into `static/music/`:

```
static/music/
  quiet-hours/
    cover.jpg
    01 - Morning Haze.mp3
    02 - Still Water.mp3
  Wanderer.mp3
  Wanderer.jpg
```

Then update `mock_data/music_library.json` — set `artUrl` to the static path (e.g.
`/static/music/quiet-hours/cover.jpg`) and `src` on each track.
If `artUrl` is `null`, the music module generates procedural canvas art from `artColor`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-in-production` | Flask session signing key |
| `FLASK_DEBUG` | `true` | Enable debug mode |
| `JOURNAL_PASSWORD` | `virtuwill2026` | Journal gate passphrase |
| `ANTHROPIC_API_KEY` | *(empty)* | Required for photo OCR feature |

---

## Deployment (Databricks Apps)

See the original README for full Databricks Apps deployment instructions.
For any WSGI host (gunicorn, Render, Railway, Fly.io):

```bash
gunicorn app:app --bind 0.0.0.0:5000
```

Set all environment variables via the host's secrets manager rather than `.env`.
