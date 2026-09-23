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

Personal HTML and JSON state live in `data/private-trackers/trackers.sqlite3`,
which is ignored by Git. Neither the source HTML nor private records are shipped
in the public repository or public static assets. Do not add your original HTML
files to `static/`, `templates/`, or the public portfolio uploader.

Set `TRACKER_DATA_DIR` to a directory on a **persistent writable volume** when
deploying. All workers must use the same local SQLite database. Separate replicas
with separate disks are not supported; use a shared database implementation
before scaling across hosts. Back up the private SQLite store and use the
trackers' JSON exports for portable copies. Redeploys with ephemeral storage will
lose imports and records.

The imported apps run in sandboxed frames with no network or parent-page access.
Admin-only endpoints, CSRF tokens, no-store responses, and revision checks protect
the bridge to server storage. A stale tab cannot silently overwrite newer data:
export its unsaved edits, reload, and reconcile using the backup. Failed saves
remain visible in the tracker until reload; closing or reloading while a save is
pending triggers a warning. Existing browser-local data is not read automatically
across origins or devices.

### Deploying the trackers on Databricks Apps

- Merging to GitHub does not update the app. Redeploy it (Apps UI → Deploy, or
  `databricks apps deploy`) from the updated source, then hard-refresh the browser.
- The Finance and Health tabs appear in the Admin sidebar only after signing in as admin.
- `app.yaml` reads `SECRET_KEY` and `ADMIN_PASSWORD` from app secret resources with
  the resource keys `secret-key` and `admin-password` (app → Edit → Resources →
  Secret). Deployment fails if either resource is missing. The admin password is
  the `admin-password` secret value, and the trackers need `SECRET_KEY` of 32+
  characters and `ADMIN_PASSWORD` of 12+ characters.
- The app's local filesystem does not persist across redeploys. Imported trackers
  are lost unless `TRACKER_DATA_DIR` points to persistent storage.

Validation:

```bash
python -m unittest discover -s tests -v
node --check static/js/trackers.js
node --check static/js/tracker-frame.js
```

---

## Quick start

```bash
pip install flask python-dotenv
cp .env.example .env
python app.py
# → http://127.0.0.1:5000
```

Works immediately with the bundled mock data. No database, no API keys needed.

**Journal passphrase:** `virtuwill2026`

---

## Project structure

```
virtuwill/
│
├── app.py               Flask application — all API routes
├── config.py            Environment variable loader
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
└── data/                      Auto-created on first write
    └── journal_entries.json   Your real journal entries (git-ignored)
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

`app.py` serves one route (`/`) and exposes JSON API endpoints. It has no knowledge of
the SPA routing — all page navigation happens client-side via `go()` in `app.js`.

### Data layer

Journal entries persist to `data/journal_entries.json`. If that file doesn't exist,
`mock_data/journal_entries.json` is returned instead. No other configuration needed
for local development.

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
