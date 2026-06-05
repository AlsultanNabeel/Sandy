"""D2 — الدروس المستفادة (Lessons Learned LTM).

Sandy تكتشف عبارات "تعلمت كذا" / "اكتشفت إن" وتحفظها كدروس في sandy_memories،
ثم تذكر المستخدم بها عند الحاجة (proactive_context).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_COLL = "sandy_memories"
_LABEL = "lesson_learned"

# مؤشرات الدروس — العبارة + ما بعدها هو الدرس
_LESSON_SIGNALS = [
    "تعلمت إن", "تعلمت ان", "تعلمت اليوم", "اكتشفت إن", "اكتشفت ان",
    "فهمت إن", "فهمت ان", "الدرس إن", "الدرس ان",
    "صار في بالي", "استفدت إن", "استفدت ان",
    "i learned", "lesson learned",
]

_LESSON_RE = re.compile(
    r"(?:" + "|".join(re.escape(s) for s in _LESSON_SIGNALS) + r")\s+(.{8,200})",
    re.IGNORECASE,
)


def detect_lesson(message: str) -> Optional[str]:
    """يستخرج نص الدرس بعد العبارة المؤشرة."""
    if not message:
        return None
    m = _LESSON_RE.search(message)
    if not m:
        return None
    text = m.group(1).strip().rstrip(".،؛!? ")
    return text[:200] if len(text) >= 8 else None


def save_lesson(
    chat_id: str,
    user_id: str,
    lesson: str,
    mongo_db=None,
) -> bool:
    """احفظ درساً جديداً."""
    if mongo_db is None or not lesson.strip():
        return False
    try:
        from app.agent.ltm_crypto import encrypt_field
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "label": _LABEL,
            "lesson": encrypt_field(lesson.strip()[:200]),
            "created_at": datetime.now(timezone.utc),
        })
        logger.info(f"[lessons] saved: {lesson[:50]}")
        return True
    except Exception as exc:
        logger.debug(f"[lessons] save failed: {exc}")
        return False


def get_lessons_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    limit: int = 3,
) -> Optional[str]:
    """يرجع آخر دروس كـ context لـ soul_node — للتذكير."""
    if mongo_db is None:
        return None
    try:
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "label": _LABEL},
            {"_id": 0, "lesson": 1},
            sort=[("created_at", -1)],
            limit=limit,
        ))
    except Exception:
        return None

    if not docs:
        return None

    from app.agent.ltm_crypto import decrypt_field
    lessons = [decrypt_field(d["lesson"]) for d in docs if d.get("lesson")]
    return "[دروس سابقة: " + " | ".join(lessons) + "]" if lessons else None


def save_detected_lesson(
    chat_id: str,
    user_id: str,
    message: str,
    mongo_db=None,
) -> bool:
    """شامل: يكتشف ويحفظ. يُستدعى من graph.py في background."""
    lesson = detect_lesson(message)
    if not lesson:
        return False
    return save_lesson(chat_id, user_id, lesson, mongo_db)
