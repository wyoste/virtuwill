"""
config.py — VirtuWill configuration
Load all settings from environment / .env file.
Copy .env.example → .env and fill in your values.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Flask ──────────────────────────────────────────────────────────────────────
SECRET_KEY  = os.environ.get("SECRET_KEY",  "dev-secret-change-in-production")
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "true").lower() == "true"

# ── Auth ───────────────────────────────────────────────────────────────────────
JOURNAL_PASSWORD = os.environ.get("JOURNAL_PASSWORD", "virtuwill2026")
ADMIN_USER       = os.environ.get("ADMIN_USER",       "admin")
ADMIN_PASSWORD   = os.environ.get("ADMIN_PASSWORD",   "virtuwill2026")

# ── Anthropic (journal photo OCR) ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
