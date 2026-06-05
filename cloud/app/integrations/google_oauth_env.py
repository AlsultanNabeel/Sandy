"""Unified resolution of Google user OAuth secrets from environment variables.

Priority order allows one consolidated token (GOOGLE_USER_TOKEN_JSON) while keeping
legacy variable names (GOOGLE_TASKS_TOKEN_JSON, GOOGLE_CALENDAR_TOKEN_JSON) working.
"""

from __future__ import annotations

import os
from typing import Optional

# Unified OAuth scopes for all Google APIs (Tasks + Calendar + Gmail)
UNIFIED_SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def user_oauth_token_json_raw() -> Optional[str]:
    """Return the first non-empty user OAuth token JSON string from known env keys."""
    for key in (
        "GOOGLE_USER_TOKEN_JSON",
        "GOOGLE_TASKS_TOKEN_JSON",
        "GOOGLE_CALENDAR_TOKEN_JSON",
    ):
        v = os.getenv(key)
        if v and str(v).strip():
            return str(v).strip()
    return None


def user_oauth_client_json_raw() -> Optional[str]:
    """Return the first non-empty OAuth *client* JSON (installed app) from env."""
    for key in ("GOOGLE_USER_OAUTH_JSON", "GOOGLE_TASKS_OAUTH_JSON"):
        v = os.getenv(key)
        if v and str(v).strip():
            return str(v).strip()
    return None
