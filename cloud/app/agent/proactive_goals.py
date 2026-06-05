"""Proactive goal follow-up: nudge Sandy to check in on stale active goals.

Fires when an active goal hasn't been touched in _STALE_DAYS and adds a
soft directive to soul_node so Sandy asks about progress on her own.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_STALE_DAYS = 7


def get_goals_followup_context(chat_id: str, user_id: str, mongo_db) -> Optional[str]:
    """Return a goal follow-up directive if stale active goals exist, else None."""
    if mongo_db is None or not chat_id:
        return None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
        goals = list(mongo_db["sandy_goals"].find(
            {
                "chat_id": str(chat_id),
                "status": "active",
                "$or": [
                    {"updated_at": {"$lt": cutoff}},
                    {"updated_at": {"$exists": False}, "created_at": {"$lt": cutoff}},
                ],
            },
            {"_id": 0, "text": 1},
            limit=3,
        ))
        texts = [g["text"] for g in goals if g.get("text")]
        if not texts:
            return None
        return (
            "[متابعة أهداف: اسأل بشكل طبيعي عن التقدم في: "
            + " | ".join(texts[:2])
            + " — اندمج بالمحادثة ولا تكوني مباشرة]"
        )
    except Exception as exc:
        logger.debug("[proactive_goals] failed: %s", exc)
        return None
