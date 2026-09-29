"""Runtime settings for Nibbles, the customer-facing assistant."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = APP_ROOT.parents[1]


def load_env() -> None:
    """Load the env file once, without overriding real process variables."""
    explicit = os.environ.get("ENV_FILE")
    candidates = [Path(explicit)] if explicit else []
    candidates += [REPO_ROOT / ".env", APP_ROOT / ".env"]
    for path in candidates:
        if path and path.is_file():
            load_dotenv(path, override=False)
            return


load_env()


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


APP_NAME = "Nibbles"
COMPANY_NAME = os.getenv("COMPANY_NAME", "Cashew4Nuts")
CURRENCY = os.getenv("CURRENCY", "SGD")
PORT = int(os.getenv("NIBBLES_PORT", os.getenv("PORT", "5001")))
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

# Set to 1 once the site is served over HTTPS so the session cookie is never
# sent in clear text. Leave at 0 on plain HTTP or the cookie is dropped.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

DATA_DIR = _path(os.getenv("DATA_DIR", "data/Team_Cashew_Synthetic_Data"))
LEADS_FILE = _path(os.getenv("LEADS_FILE", "data/runtime/nibbles_leads.csv"))

# Customers wait in real time, so latency beats depth here. On a reasoning
# model, minimal effort keeps first-token time short.
REASONING_EFFORT = os.getenv("NIBBLES_REASONING_EFFORT", "minimal")
VERBOSITY = os.getenv("NIBBLES_VERBOSITY", "low")
TEMPERATURE = float(os.getenv("NIBBLES_TEMPERATURE", "0.55"))
MAX_TOKENS = int(os.getenv("NIBBLES_MAX_TOKENS", "700"))

HISTORY_TURNS = int(os.getenv("NIBBLES_HISTORY_TURNS", "8"))
MAX_ITEMS = int(os.getenv("NIBBLES_MAX_ITEMS", "5"))
FREE_SHIPPING_THRESHOLD = float(os.getenv("FREE_SHIPPING_THRESHOLD", "50"))
