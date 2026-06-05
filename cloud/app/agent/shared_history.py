"""تاريخ مشترك: يحفظ المعالم المهمة مع تواريخها.

بنخزّن الإنجازات والأحداث والذكريات مع تاريخها، وبنرجّع تذكير لما يرجع
نفس التاريخ في سنة لاحقة. نمط الحفظ بـ label="milestone" و event_date.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_COLL = "sandy_memories"
_LABEL = "milestone"

# كلمات بتدل على معلم مهم
_MILESTONE_SIGNALS = (
    "تخرجت", "تجوزت", "خطبت", "انتقلت", "استقلت", "تعينت",
    "وقّعت العقد", "بدأت شغل", "بدأت دراسة", "تركت", "بعت",
    "اشتريت بيت", "اشتريت سيارة", "خلصت", "تخرجنا",
    "نجحت", "ربحت", "أنجبت", "ولدت",
    "ذكرى", "مرت سنة", "مرت شهور",
)

_MILESTONE_RE = re.compile(
    r"(" + "|".join(re.escape(s) for s in _MILESTONE_SIGNALS) + r")"
    r"\s+(.{3,150})"
)


def detect_milestone(message: str) -> Optional[Tuple[str, str]]:
    """يرجّع (signal, context) لو في معلم مهم بالرسالة."""
    if not message:
        return None
    m = _MILESTONE_RE.search(message)
    if not m:
        return None
    signal = m.group(1)
    context = m.group(2).strip().rstrip(".،؛!? ")[:150]
    if len(context) < 3:
        return None
    return signal, context


def save_milestone(
    chat_id: str,
    user_id: str,
    signal: str,
    context: str,
    event_date: Optional[date] = None,
    mongo_db=None,
) -> bool:
    """يحفظ معلم مع تاريخه."""
    if mongo_db is None or not signal:
        return False
    try:
        from app.agent.ltm_crypto import encrypt_field
        now_utc = datetime.now(timezone.utc)
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "label": _LABEL,
            "signal": signal,
            "context": encrypt_field(context.strip()[:200]),
            "event_date": (event_date or now_utc.date()).isoformat(),
            "created_at": now_utc,
        })
        logger.info(f"[shared_history] milestone saved: {signal} {context[:40]}")
        return True
    except Exception as exc:
        logger.debug(f"[shared_history] save failed: {exc}")
        return False


def get_anniversaries(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    today: Optional[date] = None,
) -> List[dict]:
    """يرجّع ذكريات اليوم: نفس الشهر واليوم بس من سنين سابقة."""
    if mongo_db is None:
        return []
    try:
        today = today or date.today()
        target_mm_dd = today.strftime("-%m-%d")  # "-05-16"
        docs = list(mongo_db[_COLL].find(
            {
                "chat_id": str(chat_id),
                "label": _LABEL,
                "event_date": {"$regex": f"{target_mm_dd}$"},
            },
            {"_id": 0, "signal": 1, "context": 1, "event_date": 1},
            limit=5,
        ))
        # شيل ذكريات نفس السنة الحالية
        today_iso = today.isoformat()
        return [d for d in docs if d.get("event_date") != today_iso]
    except Exception as exc:
        logger.debug(f"[shared_history] anniversary check failed: {exc}")
        return []


def get_anniversary_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
) -> Optional[str]:
    """يرجّع سطر ذكرى للرسائل الاستباقية، أو None لو ما في."""
    anniv = get_anniversaries(chat_id, user_id, mongo_db)
    if not anniv:
        return None
    first = anniv[0]
    try:
        from app.agent.ltm_crypto import decrypt_field
        year = first["event_date"][:4]
        years_ago = date.today().year - int(year)
        if years_ago <= 0:
            return None
        context = decrypt_field(first.get("context", ""))[:80]
        return f"[ذكرى مرت {years_ago} سنة: {first.get('signal')} {context}]"
    except Exception:
        return None


def save_detected_milestone(
    chat_id: str,
    user_id: str,
    message: str,
    mongo_db=None,
) -> bool:
    """يكتشف ويحفظ بخطوة وحدة. بيستدعيه graph.py بالخلفية."""
    detected = detect_milestone(message)
    if not detected:
        return False
    signal, context = detected
    return save_milestone(chat_id, user_id, signal, context, mongo_db=mongo_db)
