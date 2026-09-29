"""Runtime settings for Clarity, the internal analytics assistant."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent

# In the repository this file sits at apps/<name>/config.py, so the project
# root is two levels up. The Dockerfile copies apps/<name>/ to /app, which
# flattens that away and leaves a path with no grandparent. Fall back to the
# app directory there, which is what the container treats as its root.
REPO_ROOT = (
    APP_ROOT.parents[1] if len(APP_ROOT.parents) > 1 else APP_ROOT
)


def load_env() -> None:
    """Load the env file once, without overriding real process variables.

    Container deployments inject settings directly, so a file on disk must not
    win over them. Search order: ENV_FILE, then the repo root, then the app.
    """
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


APP_NAME = "Clarity"
COMPANY_NAME = os.getenv("COMPANY_NAME", "Cashew4Nuts")
CURRENCY = os.getenv("CURRENCY", "SGD")
PORT = int(os.getenv("CLARITY_PORT", os.getenv("PORT", "5000")))
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

# Set to 1 once the site is served over HTTPS so the session cookie is never
# sent in clear text. Leave at 0 on plain HTTP or the cookie is dropped.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

# /healthz is reachable publicly, so it reports liveness only. Turn this on
# to include the resolved endpoint, deployment name and table counts while
# diagnosing, then turn it off.
HEALTH_DETAIL = os.getenv("HEALTH_DETAIL", "0") == "1"

DATA_DIR = _path(os.getenv("DATA_DIR", "data/Team_Cashew_Synthetic_Data"))

# Reasoning models trade latency for analytical quality. Clarity answers a
# handful of internal users, so the trade is worth making here.
REASONING_EFFORT = os.getenv("CLARITY_REASONING_EFFORT", "medium")
VERBOSITY = os.getenv("CLARITY_VERBOSITY", "medium")
TEMPERATURE = float(os.getenv("CLARITY_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.getenv("CLARITY_MAX_TOKENS", "4000"))

# How many analyse-then-query rounds before the model must answer.
MAX_TOOL_ROUNDS = int(os.getenv("CLARITY_MAX_TOOL_ROUNDS", "3"))
HISTORY_TURNS = int(os.getenv("CLARITY_HISTORY_TURNS", "12"))
