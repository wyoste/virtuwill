# VirtuWill

A personal digital life dashboard — private journal, music portfolio, garden journal, and more.
Built with Flask + modular vanilla JS. No build step required.

## How the site is organized

**The public site** is read-only and has real URLs:

| Page | URL | What it shows |
|---|---|---|
| Home | `/` | Introduction, latest writing, links to work and music |
| Music | `/music`, `/music/<song>` | Every song (search, album filter), albums, photos; each song's story, versions, lyrics and chords. A player bar keeps playing while you browse. |
| Projects | `/projects`, `/projects/<id>` | Technology projects to explore by technology; each with overview, outcomes and timeline, or an uploaded walkthrough in a sandboxed frame |
| Writing | `/writing`, `/writing/<id>` | Posts, each with its own page |
| Garden, Travel | `/garden`, `/travel` | The garden map and gallery; the travel map |
| Resume | `/resume` | The CV |
| Say hi | `/contact` | Leave a note |

**The workspace** at `/app` is where the owner records and edits everything, with
one sidebar and one screen at a time:

| Section | Screens | Owns |
|---|---|---|
| Today | one day: stats, habits, quick logging, account balances, daily spending, that day's money | — (links into the others) |
| Journal | entries by date, editor, habits, balance check-in, photo transcription | `journal.entries`, tags, habit logs, journal balance snapshots |
| Health | Overview · Activity · Food · Body · Goals | workouts, meals, weigh-ins, drinks, foods, profile, goals |
| Money | Overview (full analysis, or goals only) · Transactions · Receipts · Accounts & balances · Budgets & bills · Goals & retirement · Imports · Finance tracker | bank activity, statements, paychecks and receipts through Imports; budgets, goals and the pay plan in the Finance tracker |
| Site | Music · Projects · Writing · Garden · Travel · Messages | everything the public site shows |
| Settings | site switches, journal check-in accounts, diagnostics | `core.settings` |

Each record has one editor. The same logging forms (workout, meal, weigh-in, drink)
open from Today, Health and the Journal, so there is one set of validation.
Habits like run, lift and drink tick themselves from the day's records; a habit set
by hand wins. Sign in at `/app` (the footer's "Sign in" link) with the admin password.

## The Finance and Health trackers

The **Health tracker is retired**: Health in the workspace owns those records. Its
document and last state stay in the database (and exportable from Settings); saves
to it are refused so nothing overwrites records edited natively.

The **Finance tracker** still edits budgets, bills, savings goals and the pay plan:
open it at **Money › Finance tracker**. Every save projects its records into the
`finance` tables that the Money screens read, and the status bar reports separately
whether the record saved and whether the screens updated. Bank activity and receipts
it holds are replaced by imported copies of the same rows (below): once a transaction
or receipt has been imported, the tracker's copy is not re-created.

## Finance imports

Portal exports become structured data, then load into Lakebase in two steps: extract
and review, then commit. **Money › Imports** does both; the scripts do the same from
a terminal.

| File | Read by | Gives |
|---|---|---|
| Chase Spending Summary report (PDF) | `chase.spending_report` | card charges and refunds with categories; category totals checked |
| Chase card statement (PDF) | `chase.card_statement` | the statement, opening/closing balances, charges and payments; statement totals checked |
| Payroll earning statements (PDF) | `payroll.earning_statements` | one paycheck per advice: earnings, deductions, taxes, and the account (last four only) each deposit went to; net pay checked |
| Grocery receipts + receipt items (CSV) | `grocery_receipts_csv`, `grocery_items_csv` | receipts with line items; each receipt's lines checked against its net |
| `.finance.json`, or the CSVs below | `canonical` | anything above, or data from another source written in the same shape |

The structured form (`virtuwill-finance/1`, `virtuwill/importers/canonical.py`) is one
JSON document, or one CSV per record type: `accounts`, `statements`, `balances`,
`transactions`, `receipts`, `receipt_items`, `paychecks`, `paycheck_lines`,
`paycheck_deposits`. Amounts use the site's sign (positive is money leaving an
account); accounts are identified by their last four digits only.

```bash
python scripts/extract_finance.py statement.pdf --csv out/     # → statement.finance.json and out/*.csv
python scripts/load_finance.py statement.finance.json --dry-run  # what it would add, match or replace
python scripts/load_finance.py statement.finance.json            # stage and commit
```

Committing is safe to repeat: a file already loaded (same SHA-256) changes nothing; a
transaction already in the model (same account and amount, within three days) is
matched rather than added; a tracker row for it is taken over, keeping its id,
category and receipt links. The uploaded originals are kept as private media.

Today and the Money overview show each account's latest known balance. When both
charges and payments have loaded since that balance, they show an estimate carried
forward; otherwise the balance with how much was charged since. **Record a balance**
adds one read off the bank's app. The overview's **Full analysis / Goals only**
switch is saved as the `money.overview_mode` setting.

The trackers need `SECRET_KEY` of 32+ characters and `ADMIN_PASSWORD` of 12+
characters; the documented development defaults cannot unlock them. To install the
original HTML on the deployment host:

```bash
python scripts/import_trackers.py --finance /private/Yoste-Finance.html
```

Personal HTML and JSON state live in the Lakebase `virtuwill.trackers` table, never
in the repository or public static files. The imported apps run in sandboxed frames
with no network or parent-page access; admin-only endpoints, CSRF tokens, no-store
responses and revision checks protect the bridge.

### Deploying on Databricks Apps

- Merging to GitHub does not update the app. Redeploy it (Apps UI → Deploy, or
  `databricks apps deploy`) from the updated source, then hard-refresh the browser.
- The workspace is at `/app`; `/admin` redirects there.
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
503 with a clear message; the workspace's Settings → Diagnostics says what is missing.

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

**Settings.** The workspace's Settings holds site switches (the "Chat with Will" button is
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
├── app.py                 Entry point (gunicorn app:app)
├── config.py              Environment variable loader
├── virtuwill/             Flask app: one module per domain, each with its queries and routes
│   ├── db.py, migrate.py  Connection pool, versioned schema, one-time data moves
│   ├── records.py         Record-level API resources (validated list/create/update/delete)
│   ├── today.py           The Today screen's day across every domain
│   ├── importers/         Portal exports → structured finance data (chase, payroll, kroger, canonical, load)
│   ├── money_imports.py   Money › Imports: upload, preview, commit, download
│   └── journal.py, health.py, finance.py, music.py, content.py, garden.py, travel.py, site.py, trackers.py
├── db/schema/             The data model, applied in name order, once each (see db/README.md)
├── templates/
│   ├── index.html         Public site shell; pages/ holds each page's markup
│   └── workspace.html     The workspace shell (/app)
├── static/
│   ├── app/               The workspace: main.js (router, sidebar), lib.js, forms.js, moneyparts.js, screens/*.js
│   ├── js/                Public pages: app.js (router), music.js (pages + player), projects.js,
│   │                      writing.js, garden.js/viewer.js/gallery.js, travel.js, resume.js, contact.js
│   └── css/               theme.css (tokens), site.css (public pages), page styles
└── data/                  Content loaded into a new database on first start
```

## Architecture

### Front ends

The public site (`templates/index.html`) and the workspace (`templates/workspace.html`)
are separate shells over the same API. Neither needs a build step: the public pages are
plain scripts, and the workspace is ES modules loaded by the browser, one per screen.
Links use real paths; the server returns the right shell for any page path.

### Backend — Flask API

`virtuwill.create_app()` serves both shells and the JSON API, one blueprint per domain.
The workspace uses the record-level `/api/v1/...` endpoints; the public pages read
`/api/v1/music`, `/api/v1/projects`, `/api/v1/posts` and `/api/v1/travel`, which return
only published content.

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
| GET  | `/api/v1/today?date=` | Owner | One day across journal, health and money |
| GET/POST/PUT/DELETE | `/api/v1/health/{workouts,meals,weigh-ins,drinks}` | Owner | Health records (list by `?date=` or `?from=&to=`) |
| GET/PUT | `/api/v1/health/{overview,profile,goals,foods,recipes,days}` | Owner | Health screens |
| GET | `/api/v1/money/{overview,transactions,receipts,accounts,budgets,goals,months}` | Owner | Money screens |
| GET/POST | `/api/v1/money/balances` | Owner | Balances now; POST `{account_id, balance, as_of}` records one |
| GET/POST | `/api/v1/money/imports` | Owner | Recent imports; POST files to extract and stage one |
| GET/DELETE | `/api/v1/money/imports/<id>` | Owner | One import's preview and report; DELETE discards a staged one |
| POST | `/api/v1/money/imports/<id>/commit` | Owner | Load a staged import |
| GET | `/api/v1/money/imports/<id>/bundle[?format=csv]` | Owner | Its structured data as JSON or zipped CSVs |
| POST | `/api/v1/money/extract[?format=csv]` | Owner | Files → structured data, nothing staged |
| GET | `/api/v1/music`, `/api/v1/music/songs/<slug>` | — | Published songs, versions and albums (`?view=owner` for everything) |
| POST/PUT/DELETE | `/api/v1/music/{songs,recordings,albums}` | Owner | Songs, versions, albums |
| GET/PUT/DELETE | `/api/v1/projects[/<id>]` | — / Owner | Projects |
| GET | `/api/v1/posts[/<id>]`, `/api/v1/travel`, `/api/v1/site-text/<key>` | — | Writing, travel, page text |

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
