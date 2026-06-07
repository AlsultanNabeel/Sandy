"""مواساة استباقية لما يبان على المستخدم تعب أو سهر.

لما health_monitor و anomaly_detector يطلعوا إشارات تعب، الوحدة هاي
تبدّل persona_intensity لـ empathetic وتحقن توجيه مواساة. بتستدعيها
soul_node بعد ما يجيب wellness، وبترجّع override للـ intensity.

عمداً ما بنلمز للسهر إلا لو الوقت فعلاً متأخر (من منتصف الليل لـ ٤ صباحاً)
أو المستخدم نفسه ذكر تعب أو سهر في رسالته. هيك ما بنفرض افتراض قديم
على واحد مرتاح هلأ.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# كام ليلة سهر متتالية تكفي عشان نتدخّل بغض النظر عن المود
_CRITICAL_STREAK = 3
_LATE_HOUR_START = 0
_LATE_HOUR_END = 4

# لو ذكر المستخدم وحدة من هدول، نسمح للمواساة تشتغل برا ساعات السهر
_FATIGUE_KEYWORDS_AR = (
    "تعبان", "تعبانة", "متعب", "متعبة", "ارهاق", "إرهاق",
    "سهران", "سهرانة", "سهرت", "ما نمت", "ما نام", "نعسان", "نعسانة",
    "مرهق", "مرهقة",
)
_FATIGUE_KEYWORDS_EN = ("tired", "exhausted", "sleepy", "no sleep")


def _user_signals_fatigue(message: str) -> bool:
    msg = (message or "").lower()
    if any(kw in msg for kw in _FATIGUE_KEYWORDS_AR):
        return True
    # English: match whole words so "tired" doesn't fire inside "retired".
    words = set(re.findall(r"[a-z']+", msg))
    for kw in _FATIGUE_KEYWORDS_EN:
        if (kw in msg) if " " in kw else (kw in words):
            return True
    return False


def _is_currently_late() -> bool:
    try:
        from app.utils.time import USER_TZ
        hour = datetime.now(USER_TZ).hour
        return _LATE_HOUR_START <= hour <= _LATE_HOUR_END
    except Exception:
        return False


def get_proactive_comfort(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    message: str = "",
) -> Optional[Tuple[str, str]]:
    """يرجّع (intensity_override, comfort_directive) لو في إشارات حرجة.

    intensity_override بيكون 'empathetic' دايماً وقت التفعيل.
    comfort_directive نص بنحقنه في persona_snippet.
    يرجّع None لو ما في إشارة تستدعي تدخّل.
    """
    if mongo_db is None:
        return None

    # ما نلمز للسهر إلا لو الوقت فعلاً متأخر أو المستخدم ذكر التعب
    if not (_is_currently_late() or _user_signals_fatigue(message)):
        return None

    try:
        from app.agent.health_monitor import get_late_night_streak

        streak = get_late_night_streak(chat_id, user_id, mongo_db)
        if streak >= _CRITICAL_STREAK:
            return (
                "empathetic",
                f"[مواساة استباقية: المستخدم سهران {streak} ليالي متتالية — "
                "قبل أي إجابة، اسأليه عن حاله بلطف، اقترحي راحة قصيرة أو ماء، "
                "لا تشاركيه الإنجازات الصعبة الآن]",
            )

        # إشارة أخف: ما في override للـ intensity، بس تذكير
        from app.agent.anomaly_detector import detect_habit_anomaly
        anomaly = detect_habit_anomaly(chat_id, user_id, mongo_db)
        if anomaly:
            return (
                None,  # type: ignore[return-value]
                "[تذكير لطيف: المستخدم نشط بشكل غير معتاد — راعي ذلك في النبرة]",
            )
    except Exception as exc:
        logger.debug(f"[proactive_comfort] check failed: {exc}")
        return None

    return None
