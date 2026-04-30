# VirtuWill

Personal digital life dashboard — music, garden, journal, travel, resume, and blog.  
Built with Flask + vanilla JS. No build step. Runs locally and on Databricks Apps.

---

## Quick start (local)

```bash
# 1. Clone the repo
git clone git@github.com:YOUR_USERNAME/virtuwill.git
cd virtuwill

# 2. Install dependencies
pip install flask

# 3. Run
python app.py
# → http://localhost:5000
```

Default credentials: `admin` / `virtuwill2026`  
Override with environment variables — see [Configuration](#configuration).

---

## Setting up a fresh private repo on GitHub (first time)

Do this on your Mac mini after copying the project folder over.

### 1. Delete the old repo on GitHub

Go to **github.com → your old repo → Settings → Danger Zone → Delete this repository**.  
Type the repo name to confirm. This is permanent.

### 2. Create the new private repo

Go to **github.com → New repository**:
- Name: `virtuwill` (or whatever you prefer)
- Visibility: **Private**
- **Do NOT** initialize with README, .gitignore, or license — you'll push your own

Copy the SSH URL shown: `git@github.com:YOUR_USERNAME/virtuwill.git`

### 3. Make sure your SSH key is set up on the Mac mini

```bash
# Check if you already have a key
ls ~/.ssh/id_ed25519.pub

# If not, generate one
ssh-keygen -t ed25519 -C "your@email.com"

# Print the public key and copy it
cat ~/.ssh/id_ed25519.pub
```

Add it to GitHub: **Settings → SSH and GPG keys → New SSH key** → paste and save.

Test it:
```bash
ssh -T git@github.com
# Should say: Hi YOUR_USERNAME! You've successfully authenticated...
```

### 4. Initialize and push the project

Run these commands from inside the project folder on your Mac mini:

```bash
cd /path/to/virtuwill

# Remove any old git history
rm -rf .git

# Start fresh
git init
git branch -M main

# Connect to your new private repo
git remote add origin git@github.com:YOUR_USERNAME/virtuwill.git

# Stage everything
git add .

# First commit
git commit -m "Initial commit — VirtuWill v1"

# Push
git push -u origin main
```

---

## Adding your music directory

Your music files (MP3s, WAVs, recordings) go in `static/audio/`.  
The app scans this directory automatically on startup.

```bash
# Copy your music folder into the project
cp -r /path/to/your/music/* static/audio/

# Subdirectory layout the app expects:
static/
  audio/
    Album Name/
      01 - Track Name.mp3
      02 - Track Name.mp3
      folder.jpg          ← album art (optional, any image file)
    Another Album/
      ...
    single-track.mp3      ← singles can sit at the top level
```

After copying, commit and push:

```bash
git add static/audio/
git commit -m "Add music library"
git push
```

> **Note:** Audio files can get large. If your repo approaches GitHub's 1 GB soft limit,  
> consider adding large audio files to `.gitignore` and hosting them on the Databricks  
> Volume instead (see [Databricks deployment](#databricks-deployment) below).

---

## .gitignore

Make sure this file exists in the project root. It prevents data files, secrets,  
and large media from accidentally being committed:

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
.env

# Data files (user content — not source code)
data/*.json

# Uploaded media (large files — store on Volume in production)
# Comment these lines out if you want audio/photos in the repo
# static/audio/
# static/garden/photos/
# static/portfolio/
# static/blog/

# OS
.DS_Store
Thumbs.db
```

Create it if it doesn't exist:
```bash
cat > .gitignore << 'EOF'
__pycache__/
*.pyc
.env
data/*.json
.DS_Store
EOF
```

---

## Day-to-day Git workflow

After making changes locally:

```bash
# See what changed
git status

# Stage changes
git add .

# Commit with a message
git commit -m "Describe what you changed"

# Push to GitHub
git push
```

Pull the latest from GitHub onto another machine:

```bash
git pull
```

---

## Configuration

Set these as environment variables — never commit them to the repo.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ADMIN_PASSWORD` | Yes | `virtuwill2026` | Admin portal password |
| `SECRET_KEY` | Yes | `dev-secret-...` | Flask session signing key |
| `JOURNAL_PASSWORD` | No | Same as `ADMIN_PASSWORD` | Journal unlock password |
| `ANTHROPIC_API_KEY` | No | *(empty)* | Enables journal photo OCR |

On Mac mini for local dev, set them in your shell:

```bash
# Add to ~/.zshrc or ~/.bash_profile
export ADMIN_PASSWORD="your-strong-password"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

Generate a strong secret key once and save it:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Databricks deployment

See `DEPLOY.md` for the full guide. Short version:

```sql
-- Run once in Databricks SQL to create persistent storage
CREATE CATALOG IF NOT EXISTS virtuwill;
CREATE SCHEMA  IF NOT EXISTS virtuwill.app;
CREATE VOLUME  IF NOT EXISTS virtuwill.app.data;
```

Then in Databricks Apps UI:
1. Create App → upload this repo (or connect via GitHub)
2. Set `ADMIN_PASSWORD` and `SECRET_KEY` in Environment Variables
3. The app starts automatically via `app.yaml` → `command: python app.py`

---

## Project structure

```
app.py              Flask server — all API routes
config.py           Environment detection and path resolution
app.yaml            Databricks Apps config
requirements.txt    Python dependencies
data/               JSON data files (auto-created, git-ignored)
static/
  audio/            Music files — add your library here
  garden/photos/    Uploaded garden photos
  css/              Stylesheets
  js/               Frontend modules
templates/
  index.html        Base template
  pages/            Page includes (home, music, garden, planner, ...)
  modals/           Shared modal HTML
```

---

## Dependencies

```
flask>=3.0.0        Required
anthropic>=0.28.0   Optional — journal photo OCR only
```

Install:
```bash
pip install flask
# With OCR:
pip install flask anthropic
```
