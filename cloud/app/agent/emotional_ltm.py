"""A1 — الذاكرة العاطفية طويلة الأمد.

يحفظ اللحظات العاطفية المهمة في MongoDB ويسترجعها
لإثراء persona_snippet في soul_node.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_COLL = "sandy_memories"
_LABEL = "emotional_memory"

_SIGNIFICANT_MOODS = {"stressed", "frustrated", "sad", "angry"}
_POSITIVE_MOODS = {"happy", "excited", "playful"}
_ALL_TRACKED = _SIGNIFICANT_MOODS | _POSITIVE_MOODS

_MOOD_AR = {
    "stressed": "متوتر",
    "frustrated": "محبط",
    "sad": "حزين",
    "angry": "غاضب",
    "happy": "سعيد",
    "excited": "متحمس",
    "playful": "مرح",
}


def save_emotional_moment(
    chat_id: str,
    user_id: str,
    mood: str,
    topic: str,
    mongo_db=None,
) -> None:
    """احفظ لحظة عاطفية مهمة في LTM.

    يُستدعى من graph.py بعد response_node عند mood مهم.
    """
    if mongo_db is None or mood not in _ALL_TRACKED:
        return
    try:
        from app.agent.ltm_crypto import encrypt_field
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "label": _LABEL,
            "mood": mood,
            "topic": encrypt_field(str(topic)[:200]),
            "created_at": datetime.now(timezone.utc),
        })
    except Exception:
        pass


def get_emotional_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    limit: int = 3,
) -> Optional[str]:
    """اجلب آخر لحظات عاطفية كـ context موجز لـ soul_node.

    يُرجع None إذا لا يوجد تاريخ عاطفي أو قاعدة البيانات غير متاحة.
    """
    if mongo_db is None:
        return None
    try:
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "label": _LABEL},
            {"_id": 0, "mood": 1, "topic": 1, "created_at": 1},
            sort=[("created_at", -1)],
            limit=limit,
        ))
    except Exception:
        return None

    if not docs:
        return None

    from app.agent.ltm_crypto import decrypt_field
    parts = []
    for d in docs:
        raw_date = d.get("created_at")
        date_str = raw_date.strftime("%m/%d") if hasattr(raw_date, "strftime") else ""
        mood_ar = _MOOD_AR.get(d.get("mood", ""), d.get("mood", ""))
        topic = decrypt_field(d.get("topic", ""))
        parts.append(f"{date_str} {mood_ar}: {topic}" if date_str else f"{mood_ar}: {topic}")

    return "[ذاكرة: " + " | ".join(parts) + "]"
