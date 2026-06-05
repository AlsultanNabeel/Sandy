"""#2 — Anomaly Detection: كشف الشذوذ في عادات المستخدم.

يقارن نشاط اليوم بالمتوسط التاريخي.
إذا كان هناك انحراف كبير → Sandy تُنبَّه لتتعامل بوعي.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_HOUR_DEVIATION_THRESHOLD = 3.0  # ساعات — انحراف يُعتبر شاذاً


def detect_habit_anomaly(
    chat_id: str,
    user_id: str,
    mongo_db=None,
) -> Optional[str]:
    """يكتشف إذا كان النشاط الحالي شاذاً مقارنةً بالعادة.

    يرجع وصف الشذوذ أو None إذا كل شي طبيعي.
    """
    if mongo_db is None:
        return None
    try:
        from app.utils.time import USER_TZ
        from app.agent.health_monitor import get_avg_activity_hour
        from datetime import datetime

        now = datetime.now(USER_TZ)
        current_hour = now.hour

        avg_hour = get_avg_activity_hour(chat_id, user_id, mongo_db, days=7)
        if avg_hour is None:
            return None

        deviation = abs(current_hour - avg_hour)
        # تعديل للساعات التي تتجاوز منتصف الليل (مثلاً: avg=22, current=2 → dev=4 لا 20)
        if deviation > 12:
            deviation = 24 - deviation

        if deviation >= _HOUR_DEVIATION_THRESHOLD:
            avg_str = f"{int(avg_hour):02d}:00"
            now_str = now.strftime("%H:%M")
            if current_hour < avg_hour:
                return f"[شذوذ: نشط مبكراً ({now_str}) مقارنةً بعادته ({avg_str})]"
            else:
                return f"[شذوذ: نشط متأخراً ({now_str}) مقارنةً بعادته ({avg_str})]"
        return None
    except Exception as exc:
        logger.debug(f"[anomaly_detector] check failed: {exc}")
        return None


def get_wellness_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
) -> Optional[str]:
    """يجمع كل سياق الصحة (سهر + شذوذ) في سطر واحد لـ soul_node."""
    from app.agent.health_monitor import get_sleep_context
    sleep = get_sleep_context(chat_id, user_id, mongo_db)
    anomaly = detect_habit_anomaly(chat_id, user_id, mongo_db)

    parts = [p for p in (sleep, anomaly) if p]
    return " ".join(parts) if parts else None
