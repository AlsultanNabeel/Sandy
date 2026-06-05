"""C2 — متتبّع الاهتمامات (Interests Tracker).

يستخرج اهتمامات المستخدم من رسائله ويحفظها كـ keyword frequency في sandy_memories.
يُستخدم لاحقاً لـ:
  - مشاركة محتوى ذكي (research_web on top topic)
  - تخصيص الردود

التحقيق ذو مرحلتين:
  1. detect_interest_keywords() — regex على الرسالة، يستخرج keywords مرشحة
  2. bump_interest() — يزيد عداد كل keyword في mongo (sandy_memories label='interest')
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_COLL = "sandy_memories"
_LABEL = "interest"

# مؤشرات الاهتمام — "بحب X" / "مهتم بـ X" / "حابب X" / "متابع X"
_INTEREST_RE = re.compile(
    r"(?:بحب|أحب|احب|مهتم\s+بـ?|حابب|متابع|بدرس|بتابع)\s+(?:ال)?([ء-ي]{3,30}(?:\s+[ء-ي]{3,15})?)",
)

# stopwords — مش اهتمامات حقيقية
_STOPWORDS = {
    "الموضوع", "الكلام", "الحكي", "الفكرة", "اشي", "شي", "الشي",
    "هاي", "هاد", "كذا", "هيك", "وقت", "اشياء",
}


def _normalize_keyword(keyword: str) -> str:
    return " ".join(str(keyword or "").split()).strip().lower()


def detect_interest_keywords(message: str) -> List[str]:
    """يستخرج كلمات-اهتمام مرشحة من الرسالة."""
    if not message:
        return []

    found = []
    for m in _INTEREST_RE.finditer(message):
        kw = m.group(1).strip()
        if kw in _STOPWORDS or len(kw) < 3:
            continue
        found.append(kw)
    return found


def bump_interest(
    chat_id: str,
    user_id: str,
    keyword: str,
    mongo_db=None,
) -> bool:
    """يزيد عداد keyword في sandy_memories. idempotent."""
    if mongo_db is None or not keyword.strip():
        return False
    try:
        now = datetime.now(timezone.utc)
        mongo_db[_COLL].update_one(
            {"chat_id": str(chat_id), "label": _LABEL, "keyword": keyword.strip()},
            {
                "$inc": {"count": 1},
                "$set": {"user_id": str(user_id), "last_seen": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.debug(f"[interests] bump failed: {exc}")
        return False


def get_top_interests(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    limit: int = 5,
) -> List[str]:
    """يرجع أعلى اهتمامات المستخدم — مرتبة بالـ count."""
    if mongo_db is None:
        return []
    try:
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "label": _LABEL},
            {"_id": 0, "keyword": 1, "count": 1},
            sort=[("count", -1)],
            limit=limit,
        ))
        return [d["keyword"] for d in docs if d.get("keyword")]
    except Exception:
        return []


def get_interest_frequencies(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """يرجع الاهتمامات مع عدد التكرار، مرتبة تنازلياً."""
    if mongo_db is None:
        return []
    try:
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "label": _LABEL},
            {"_id": 0, "keyword": 1, "count": 1, "last_seen": 1},
            sort=[("count", -1), ("last_seen", -1)],
            limit=limit,
        ))
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for d in docs:
        keyword = str(d.get("keyword") or "").strip()
        if not keyword:
            continue
        try:
            count = int(d.get("count") or 0)
        except Exception:
            count = 0
        out.append(
            {
                "keyword": keyword,
                "normalized_keyword": _normalize_keyword(keyword),
                "count": count,
                "last_seen": d.get("last_seen"),
            }
        )
    return out


def get_proactive_interest_candidate(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    min_count: int = 3,
    limit: int = 5,
) -> Optional[str]:
    """يرجع أول اهتمام موثق يكفي للتعامل معه بشكل استباقي."""
    for item in get_interest_frequencies(chat_id, user_id, mongo_db, limit=limit):
        if item.get("count", 0) >= min_count:
            return item["keyword"]
    return None


def track_message_interests(
    chat_id: str,
    user_id: str,
    message: str,
    mongo_db=None,
) -> int:
    """شامل: يكتشف ويزيد العدّاد. يُستدعى من graph.py في background."""
    keywords = detect_interest_keywords(message)
    bumped = 0
    for kw in keywords[:3]:  # حد أعلى لكل رسالة
        if bump_interest(chat_id, user_id, kw, mongo_db):
            bumped += 1
    return bumped


def get_interests_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
) -> Optional[str]:
    """يرجع اهتمامات المستخدم كـ hint لـ soul_node — لتخصيص الردود."""
    top = get_top_interests(chat_id, user_id, mongo_db, limit=5)
    if not top:
        return None
    return "[اهتمامات: " + " · ".join(top) + "]"
