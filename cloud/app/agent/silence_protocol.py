"""متى تسكت Sandy وما تبعت رسالة استباقية.

بنفحص التقويم قبل أي رسالة استباقية، وكمان بنحترم ساعات الهدوء
(quiet hours). لو المستخدم في اجتماع أو ضمن ساعات الهدوء، ما بنزعجه.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# بنسكت من دقيقتين قبل الحدث لحد ما يخلص
_PRE_EVENT_BUFFER_MINUTES = 2

# cache لنتيجة فحص التقويم عشان نقلّل الاستدعاءات (افتراضي 120 ثانية)
_CALENDAR_CACHE_TTL = int(os.getenv("SANDY_CALENDAR_CACHE_TTL", "120"))
_calendar_cache: dict = {"timestamp": 0.0, "result": False}

# ساعات الهدوء الافتراضية لو الـ env فاضي: من 23:00 لـ 07:00
_DEFAULT_QUIET_START = 23
_DEFAULT_QUIET_END = 7


def _parse_quiet_hours() -> Tuple[int, int]:
    """يقرأ SANDY_QUIET_HOURS من البيئة بصيغة 'HH-HH' مثل '23-7'."""
    raw = os.getenv("SANDY_QUIET_HOURS", "").strip()
    if not raw:
        return _DEFAULT_QUIET_START, _DEFAULT_QUIET_END
    try:
        start_s, end_s = raw.split("-", 1)
        start = max(0, min(23, int(start_s.strip())))
        end = max(0, min(23, int(end_s.strip())))
        return start, end
    except Exception:
        return _DEFAULT_QUIET_START, _DEFAULT_QUIET_END


def is_quiet_hours(now: Optional[datetime] = None) -> bool:
    """True لو الوقت الحالي ضمن ساعات الهدوء."""
    try:
        from app.utils.time import USER_TZ
        if now is None:
            now = datetime.now(USER_TZ)
        start, end = _parse_quiet_hours()
        h = now.hour
        if start < end:
            return start <= h < end
        # النطاق بيعبر منتصف الليل، زي من 23 لـ 7
        return h >= start or h < end
    except Exception:
        return False


def get_quiet_window_end(now: Optional[datetime] = None) -> Optional[datetime]:
    """يرجّع نهاية فترة الهدوء الحالية، أو None لو مش ضمن ساعات الهدوء."""
    try:
        from app.utils.time import USER_TZ
        if now is None:
            now = datetime.now(USER_TZ)
        if not is_quiet_hours(now):
            return None
        _, end_h = _parse_quiet_hours()
        end_dt = now.replace(hour=end_h, minute=0, second=0, microsecond=0)
        if end_dt <= now:
            end_dt = end_dt + timedelta(days=1)
        return end_dt
    except Exception:
        return None


def should_stay_silent(now: Optional[datetime] = None) -> bool:
    """يفحص الاجتماعات وساعات الهدوء، وأي واحد فيهم True معناه نسكت."""
    if is_quiet_hours(now):
        logger.debug("[silence_protocol] quiet hours active, staying silent")
        return True
    return is_user_in_meeting(now)


def is_user_in_meeting(now: Optional[datetime] = None) -> bool:
    """يشوف لو في حدث شغّال في التقويم هلأ.

    بيستدعيه Pulse Monitor قبل أي رسالة استباقية. True معناه نسكت،
    False معناه نقدر نبعت. النتيجة بتتخزّن في cache مدتها
    SANDY_CALENDAR_CACHE_TTL (افتراضي 120 ثانية) عشان نقلّل استدعاءات التقويم.
    """
    # نشوف الـ cache أول، بس لما now تكون None (الوقت الصريح بيتخطّى الـ cache)
    if now is None and _calendar_cache["timestamp"]:
        age = time.time() - _calendar_cache["timestamp"]
        if age < _CALENDAR_CACHE_TTL:
            return _calendar_cache["result"]

    try:
        from app.features.google_calendar import _get_calendar_service, calendar_id
        from app.utils.time import USER_TZ

        if now is None:
            now = datetime.now(USER_TZ)

        t_min = (now - timedelta(hours=8)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        t_max = (now + timedelta(minutes=_PRE_EVENT_BUFFER_MINUTES)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        service = _get_calendar_service()
        resp = service.events().list(
            calendarId=calendar_id(),
            timeMin=t_min,
            timeMax=t_max,
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = resp.get("items", [])
        result = False
        for event in events:
            if _is_active_now(event, now):
                summary = event.get("summary", "حدث")
                logger.info(f"[silence_protocol] في حدث شغّال، نسكت: '{summary}'")
                result = True
                break

        # خزّن النتيجة في الـ cache
        _calendar_cache["timestamp"] = time.time()
        _calendar_cache["result"] = result
        return result

    except Exception as exc:
        # لو فشل التقويم، ما بنسكت لأن التواصل أولى
        logger.debug(f"[silence_protocol] calendar check failed: {exc}")
        # خزّن النتيجة fail-open كمان لفترة قصيرة
        _calendar_cache["timestamp"] = time.time()
        _calendar_cache["result"] = False
        return False


def _is_active_now(event: dict, now: datetime) -> bool:
    """يشوف لو الحدث متداخل مع الوقت الحالي."""
    try:
        from app.utils.time import USER_TZ

        start_raw = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
        end_raw = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")

        if not start_raw or not end_raw:
            return False

        # all-day events (date only, no time)
        if "T" not in start_raw:
            start_dt = datetime.fromisoformat(start_raw).replace(tzinfo=USER_TZ)
            end_dt = datetime.fromisoformat(end_raw).replace(tzinfo=USER_TZ)
        else:
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(USER_TZ)
            end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(USER_TZ)

        buffer_start = start_dt - timedelta(minutes=_PRE_EVENT_BUFFER_MINUTES)
        return buffer_start <= now <= end_dt

    except Exception:
        return False


def get_next_free_window(minutes_ahead: int = 120) -> Optional[datetime]:
    """يلاقي أقرب وقت فاضي بعد الأحداث الحالية.

    بنستخدمه عشان نأجّل الرسائل الاستباقية بدل ما نحذفها.
    يرجّع None لو ما في أحداث أو فشل التقويم.
    """
    try:
        from app.features.google_calendar import _get_calendar_service, calendar_id
        from app.utils.time import USER_TZ

        now = datetime.now(USER_TZ)
        t_min = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        t_max = (now + timedelta(minutes=minutes_ahead)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        service = _get_calendar_service()
        resp = service.events().list(
            calendarId=calendar_id(),
            timeMin=t_min,
            timeMax=t_max,
            maxResults=5,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = [e for e in resp.get("items", []) if _is_active_now(e, now) or _starts_soon(e, now)]
        if not events:
            return None

        # رجّع نهاية آخر حدث
        latest_end = None
        for event in events:
            end_raw = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")
            if not end_raw:
                continue
            if "T" not in end_raw:
                end_dt = datetime.fromisoformat(end_raw).replace(tzinfo=USER_TZ)
            else:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(USER_TZ)
            if latest_end is None or end_dt > latest_end:
                latest_end = end_dt

        return latest_end

    except Exception:
        return None


def _starts_soon(event: dict, now: datetime) -> bool:
    """يشوف لو الحدث رح يبدأ خلال دقيقتين."""
    try:
        from app.utils.time import USER_TZ
        start_raw = (event.get("start") or {}).get("dateTime")
        if not start_raw:
            return False
        start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(USER_TZ)
        return now <= start_dt <= now + timedelta(minutes=_PRE_EVENT_BUFFER_MINUTES)
    except Exception:
        return False
