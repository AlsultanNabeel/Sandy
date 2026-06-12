"""متى تسكت Sandy وما تبعت رسالة استباقية.

بنحترم ساعات الهدوء (quiet hours): ضمنها ما بنزعج المستخدم بأي رسالة
استباقية. (فحص «هو في اجتماع؟» راح مع تقويم جوجل — ما عاد عندنا تقويم،
والتذكيرات المحلية مش مواعيد مشغولية.)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ساعات الهدوء الافتراضية لو الـ env فاضي: من 23:00 لـ 07:00
_DEFAULT_QUIET_START = 23
_DEFAULT_QUIET_END = 7


def _parse_quiet_hours() -> Tuple[int, int]:
    """يقرأ SANDY_QUIET_HOURS بصيغة "23-7" ويرجّع (بداية، نهاية)."""
    raw = os.getenv("SANDY_QUIET_HOURS", "").strip()
    if raw:
        try:
            start_s, end_s = raw.split("-", 1)
            start, end = int(start_s), int(end_s)
            if 0 <= start <= 23 and 0 <= end <= 23:
                return start, end
        except Exception:
            logger.warning(f"[silence_protocol] bad SANDY_QUIET_HOURS: {raw!r}")
    return _DEFAULT_QUIET_START, _DEFAULT_QUIET_END


def is_quiet_hours(now: Optional[datetime] = None) -> bool:
    """True لو الوقت الحالي ضمن ساعات الهدوء."""
    from app.utils.time import USER_TZ

    if now is None:
        now = datetime.now(USER_TZ)
    start, end = _parse_quiet_hours()
    hour = now.hour
    if start <= end:
        return start <= hour < end
    # نافذة بتلف منتصف الليل (مثل 23-7)
    return hour >= start or hour < end


def get_quiet_window_end(now: Optional[datetime] = None) -> Optional[datetime]:
    """يرجّع نهاية نافذة الهدوء الحالية، أو None لو ما إحنا فيها."""
    from app.utils.time import USER_TZ

    if now is None:
        now = datetime.now(USER_TZ)
    if not is_quiet_hours(now):
        return None
    _, end = _parse_quiet_hours()
    candidate = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def should_stay_silent(now: Optional[datetime] = None) -> bool:
    """True معناه نسكت (ساعات هدوء)."""
    if is_quiet_hours(now):
        logger.debug("[silence_protocol] quiet hours active, staying silent")
        return True
    return False
