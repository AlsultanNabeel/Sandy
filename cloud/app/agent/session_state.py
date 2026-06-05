"""Cross-session user state: mood, platform, recent topics, shared across channels.

So Telegram, web and voice all know the user's last state and Sandy stays
consistent when the user switches channel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_COLLECTION = "sandy_session_state"


def get_session_state(chat_id: str, mongo_db) -> Optional[Dict[str, Any]]:
    """Return persisted session state for this user, or None."""
    if mongo_db is None or not chat_id:
        return None
    try:
        return mongo_db[_COLLECTION].find_one({"chat_id": str(chat_id)}, {"_id": 0})
    except Exception as exc:
        logger.debug("[session_state] get failed: %s", exc)
        return None


def update_session_state(
    chat_id: str,
    mongo_db,
    *,
    mood: Optional[str] = None,
    platform: Optional[str] = None,
    topic: Optional[str] = None,
) -> None:
    """Upsert cross-session state for this user (call after every turn)."""
    if mongo_db is None or not chat_id:
        return
    try:
        set_fields: Dict[str, Any] = {"last_active_at": datetime.now(timezone.utc)}
        if mood and mood not in ("neutral", ""):
            set_fields["last_mood"] = mood
        if platform:
            set_fields["last_platform"] = platform

        if topic:
            mongo_db[_COLLECTION].update_one(
                {"chat_id": str(chat_id)},
                {
                    "$set": set_fields,
                    "$push": {"recent_topics": {"$each": [topic], "$slice": -5}},
                },
                upsert=True,
            )
        else:
            mongo_db[_COLLECTION].update_one(
                {"chat_id": str(chat_id)},
                {"$set": set_fields},
                upsert=True,
            )
    except Exception as exc:
        logger.debug("[session_state] update failed: %s", exc)
