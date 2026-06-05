"""ذاكرة الأسلوب: Sandy بتتعلّم تفضيلات المستخدم وتصحيحاته وبتطبّقها.

بنحفظ التفضيل عبر save_style_preference() لما المستخدم يقول شي زي
"ما أريد كذا" أو "فضّلي كذا".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_COLL = "sandy_memories"
_LABEL = "style_memory"

# كلمات بتدل على تصحيح أسلوبي، maestro بيكتشفها
CORRECTION_SIGNALS = [
    "لا تـ", "لا تستخدم", "لا تكتب", "ما أريد", "ما أحب", "ما يعجبني",
    "قلل", "أطل", "اختصر", "بدون رموز", "بدون ايموجي", "بدون ايموجيات",
    "ردودك طويلة", "ردودك قصيرة", "بدي ردود", "أريدك أن", "فضلي",
    "تذكري إني", "خليك", "كوني",
]


def save_style_preference(
    chat_id: str,
    user_id: str,
    preference: str,
    source_message: str = "",
    mongo_db=None,
) -> bool:
    """يحفظ تفضيل أسلوب جديد في MongoDB.

    بيستدعيه الـ graph pipeline لما يكتشف رسالة تصحيح. يرجّع True لو نجح.
    """
    if mongo_db is None or not preference.strip():
        return False
    try:
        from app.agent.ltm_crypto import encrypt_field
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "label": _LABEL,
            "preference": encrypt_field(str(preference).strip()[:300]),
            "source_message": encrypt_field(str(source_message)[:200]),
            "created_at": datetime.now(timezone.utc),
        })
        logger.info(f"[style_memory] saved preference for {chat_id}: {preference[:60]}")
        return True
    except Exception as exc:
        logger.debug(f"[style_memory] save failed: {exc}")
        return False


def detect_style_correction(message: str) -> bool:
    """يشوف لو الرسالة فيها تصحيح أسلوبي.

    بنستخدمه في route_with_fc قبل ما نبعت الرسالة لـ Gemini.
    """
    msg_lower = message.lower()
    return any(signal in msg_lower for signal in CORRECTION_SIGNALS)
