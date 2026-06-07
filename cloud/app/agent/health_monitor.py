"""#1 — Health & Wellness: رصد أنماط السهر.

Sandy ترصد توقيت رسائل المستخدم وتكتشف أنماط السهر المتأخر.
تُحفظ في MongoDB وتُستخدم في soul_node وproactive_context.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_COLL = "sandy_activity"
_LATE_HOUR_START = 0   # منتصف الليل
_LATE_HOUR_END = 4     # الرابعة صباحاً
_STREAK_THRESHOLD = 3  # عدد الليالي المتتالية للتنبيه


def record_activity(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    now: Optional[datetime] = None,
) -> None:
    """سجّل توقيت النشاط الحالي — يُستدعى من graph.py بشكل خفي."""
    if mongo_db is None:
        return
    try:
        from app.utils.time import USER_TZ
        ts = now or datetime.now(USER_TZ)
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "hour": ts.hour,
            "date": ts.strftime("%Y-%m-%d"),
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as exc:
        logger.debug(f"[health_monitor] record failed: {exc}")


def get_late_night_streak(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    days: int = 7,
) -> int:
    """يرجع عدد الليالي المتتالية التي سهر فيها المستخدم بعد منتصف الليل."""
    if mongo_db is None:
        return 0
    try:
        from app.utils.time import USER_TZ
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "hour": {"$gte": _LATE_HOUR_START, "$lte": _LATE_HOUR_END}},
            {"_id": 0, "date": 1},
            sort=[("created_at", -1)],
            limit=days * 5,
        ))
        if not docs:
            return 0

        # احسب الأيام الفريدة المتتالية
        late_dates = sorted({d["date"] for d in docs}, reverse=True)

        streak = 0
        check = datetime.now(USER_TZ).date()
        for date_str in late_dates:
            d = date.fromisoformat(date_str)
            if d == check or d == check - timedelta(days=1):
                streak += 1
                check = d - timedelta(days=1)
                if streak >= days:
                    break
            else:
                break
        return streak
    except Exception as exc:
        logger.debug(f"[health_monitor] streak check failed: {exc}")
        return 0


def get_sleep_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
) -> Optional[str]:
    """يرجع context موجز لـ soul_node إذا كان المستخدم سهران متأخراً.

    يرجع None إذا لا يوجد نمط يستحق الذكر.
    """
    try:
        from app.utils.time import USER_TZ
        now = datetime.now(USER_TZ)
        is_late = _LATE_HOUR_START <= now.hour <= _LATE_HOUR_END
        if not is_late:
            return None

        streak = get_late_night_streak(chat_id, user_id, mongo_db)
        if streak >= _STREAK_THRESHOLD:
            return f"[ملاحظة: سهران متأخر {streak} ليالي متتالية — تعامل برفق]"
        if streak >= 1:
            return "[ملاحظة: سهران متأخر الآن]"
        return None
    except Exception:
        return None


def ensure_ttl_index(mongo_db=None, ttl_days: int = 30) -> None:
    """أنشئ TTL index على sandy_activity عند أول تشغيل."""
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index(
            "created_at", expireAfterSeconds=ttl_days * 86400, background=True
        )
    except Exception:
        pass


def get_avg_activity_hour(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    days: int = 7,
) -> Optional[float]:
    """يرجع متوسط ساعة النشاط خلال آخر N أيام — يُستخدم في Anomaly Detection."""
    if mongo_db is None:
        return None
    try:
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id)},
            {"_id": 0, "hour": 1},
            sort=[("created_at", -1)],
            limit=days * 10,
        ))
        if len(docs) < 5:
            return None
        # Hour is circular (23 and 1 are close), so a plain mean is wrong.
        # Average the hours as unit vectors, then convert the angle back.
        angles = [d["hour"] / 24.0 * 2 * math.pi for d in docs]
        mean_sin = sum(math.sin(a) for a in angles) / len(angles)
        mean_cos = sum(math.cos(a) for a in angles) / len(angles)
        avg = math.atan2(mean_sin, mean_cos) / (2 * math.pi) * 24.0
        return avg % 24.0
    except Exception:
        return None
