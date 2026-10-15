"""Settings and API-key lookup.

Where your TinyFish API key goes (pick ONE):
  1. A local file named `.env` next to app.py       ->  TINYFISH_API_KEY=sk-tinyfish-...
  2. Streamlit secrets (.streamlit/secrets.toml, or the "Secrets" box on Streamlit Cloud)
  3. Paste it into the sidebar while the app is running (kept only for that browser session)

The placeholder text below is treated as "no key yet".
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_KEY_PLACEHOLDER = "PASTE_YOUR_TINYFISH_API_KEY_HERE"

DB_PATH = Path(os.getenv("DEAL_RADAR_DB", ROOT / "data" / "deal_radar.db"))

# Safety rails so a public demo cannot burn through your credits.
MAX_RUNS_PER_SESSION = int(os.getenv("MAX_RUNS_PER_SESSION", "40"))
RUN_TIMEOUT_SECONDS = int(os.getenv("RUN_TIMEOUT_SECONDS", "240"))
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))

RUN_URL = "https://agent.tinyfish.ai/runs/{run_id}"
KEYS_URL = "https://agent.tinyfish.ai/api-keys"


def clean_key(value: str | None) -> str | None:
    value = (value or "").strip().strip('"').strip("'")
    if not value or "PASTE_YOUR" in value:
        return None
    return value


def _secret(name: str) -> str | None:
    try:
        import streamlit as st

        value = st.secrets.get(name)
        return str(value) if value else None
    except Exception:
        return None


def resolve_api_key(typed_key: str | None = None) -> tuple[str | None, str]:
    """Return (key, source). Source is 'sidebar', 'secrets', 'env' or 'none'."""
    key = clean_key(typed_key)
    if key:
        return key, "sidebar"
    key = clean_key(_secret("TINYFISH_API_KEY"))
    if key:
        return key, "secrets"
    key = clean_key(os.getenv("TINYFISH_API_KEY"))
    if key:
        return key, "env"
    return None, "none"


def app_password() -> str | None:
    """Optional password gate. Set APP_PASSWORD in .env or secrets to enable it."""
    return _secret("APP_PASSWORD") or os.getenv("APP_PASSWORD") or None
