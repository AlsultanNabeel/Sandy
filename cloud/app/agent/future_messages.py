"""E3 — الرسائل المجدولة للمستقبل (Future Messages).

المستخدم يطلب: "ذكريني بعد شهر إني كنت قلت كذا" أو "اكتبيلي رسالة لنفسي بعد سنة"
نحفظ الرسالة + تاريخ التسليم. عند أول رسالة بعد التاريخ → تُسلَّم Sandy للمستخدم.

التسليم: passive — Sandy تذكرها في الرد التالي بعد تاريخ التسليم.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

_COLL = "sandy_future_messages"


def schedule_future_message(
    chat_id: str,
    user_id: str,
    text: str,
    deliver_at: datetime,
    mongo_db=None,
) -> bool:
    """احفظ رسالة لتسلَّم في وقت لاحق."""
    if mongo_db is None or not text.strip():
        return False
    try:
        from app.agent.ltm_crypto import encrypt_field
        deliver_utc = deliver_at.astimezone(timezone.utc) if deliver_at.tzinfo else deliver_at.replace(tzinfo=timezone.utc)
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "text": encrypt_field(text.strip()[:500]),
            "deliver_at": deliver_utc,
            "delivered": False,
            "created_at": datetime.now(timezone.utc),
        })
        logger.info(f"[future_messages] scheduled for {deliver_utc.isoformat()}: {text[:40]}")
        return True
    except Exception as exc:
        logger.debug(f"[future_messages] schedule failed: {exc}")
        return False


def pop_due_messages(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    limit: int = 3,
) -> List[dict]:
    """يجلب الرسائل المستحقة الآن ويعلّمها delivered=True (atomic per message).

    يُستدعى مرة عند كل رسالة من المستخدم — passive delivery.
    """
    if mongo_db is None:
        return []
    try:
        now = datetime.now(timezone.utc)
        delivered = []
        for _ in range(limit):
            doc = mongo_db[_COLL].find_one_and_update(
                {
                    "chat_id": str(chat_id),
                    "delivered": False,
                    "deliver_at": {"$lte": now},
                },
                {"$set": {"delivered": True, "delivered_at": now}},
                projection={"_id": 0, "text": 1, "deliver_at": 1, "created_at": 1},
                sort=[("deliver_at", 1)],
            )
            if not doc:
                break
            delivered.append(doc)
        return delivered
    except Exception as exc:
        logger.debug(f"[future_messages] pop failed: {exc}")
        return []


def get_future_messages_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
) -> Optional[str]:
    """إذا في رسائل مستحقة، يُسلَّمها كـ context لـ Sandy لتذكر المستخدم بها."""
    due = pop_due_messages(chat_id, user_id, mongo_db)
    if not due:
        return None

    from app.agent.ltm_crypto import decrypt_field
    parts = []
    for d in due:
        created = d.get("created_at")
        created_str = created.strftime("%Y/%m/%d") if hasattr(created, "strftime") else ""
        msg = decrypt_field(d.get("text", ""))[:200]
        parts.append(f"({created_str}): {msg}" if created_str else msg)

    return "[رسالة مجدولة من المستخدم لنفسه: " + " | ".join(parts) + "]"
