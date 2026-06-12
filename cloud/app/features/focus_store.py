"""وضع التركيز — مؤقت دراسة/شغل، والروبوت بساير الجلسة.

Collection: sandy_focus
  {_id, label, minutes, started_at, ends_at, state: active|done|cancelled,
   reminder_id}

التنبيه عند النهاية بمر عبر نظام التذكيرات نفسه (مخزَّن في Mongo) — يعني
بنجو من إعادة تشغيل السيرفر، وبوصل تيليجرام بأزرار الغفوة العادية.
الروبوت: وجه مركّز عند البداية، احتفال عند الإنهاء — لو متصل، ولو لأ
الجلسة بتشتغل عادي.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.utils.time import USER_TZ
from app.utils.user_profiles import active_profile_allows_privileged_access

_COLL = "sandy_focus"
_mongo_db = None


def init_focus_store(mongo_db) -> None:
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is not None:
        print("[FocusStore] ready")


def _require_owner() -> None:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")


def _robot(action: str) -> None:
    """إشارة للروبوت — فشلها ما بيوقف الجلسة أبداً."""
    try:
        from app.integrations.sandy_device import get_sandy_device_client

        device = get_sandy_device_client()
        if not device or not device.available:
            return
        if action == "start":
            device.set_mood("focused")
        elif action == "celebrate":
            device.set_mood("happy")
            device.play_buzzer("happy")
        elif action == "idle":
            device.set_mood("idle")
    except Exception:
        pass


def active_focus() -> Optional[Dict[str, Any]]:
    if _mongo_db is None:
        return None
    return _mongo_db[_COLL].find_one({"state": "active"})


def start_focus(minutes: int = 25, label: str = "") -> Dict[str, Any]:
    _require_owner()
    if _mongo_db is None:
        return {"ok": False}
    if active_focus():
        return {"ok": False, "error": "already_active"}
    minutes = max(5, min(240, int(minutes or 25)))
    now = datetime.now(timezone.utc)
    ends = now + timedelta(minutes=minutes)

    reminder_id = ""
    try:
        from app.features.reminders_store import add_reminder

        label_txt = f" ({label})" if label else ""
        r = add_reminder(
            text=f"🎉 خلصت جلسة التركيز{label_txt} — {minutes} دقيقة! خذ استراحة",
            remind_at_iso=ends.astimezone(USER_TZ).isoformat(),
        )
        if r.get("success"):
            reminder_id = r.get("id", "")
    except Exception as e:  # noqa: BLE001
        print(f"[FocusStore] end reminder failed: {e}")

    _mongo_db[_COLL].insert_one(
        {
            "_id": uuid.uuid4().hex,
            "label": str(label or "").strip(),
            "minutes": minutes,
            "started_at": now,
            "ends_at": ends,
            "state": "active",
            "reminder_id": reminder_id,
        }
    )
    _robot("start")
    return {"ok": True, "minutes": minutes, "label": label}


def stop_focus(completed: bool = True) -> Dict[str, Any]:
    """ينهي الجلسة. completed=True بتنحسب إنجاز (احتفال)، False = إلغاء."""
    _require_owner()
    s = active_focus()
    if not s:
        return {"ok": False, "error": "no_session"}

    now = datetime.now(timezone.utc)
    started = s["started_at"]
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed_min = max(0, int((now - started).total_seconds() / 60))

    _mongo_db[_COLL].update_one(
        {"_id": s["_id"]},
        {"$set": {"state": "done" if completed else "cancelled", "ended_at": now}},
    )
    # التذكير ما عاد لازم لو وقفنا بدري
    rid = s.get("reminder_id", "")
    if rid:
        try:
            from app.features.reminders_store import delete_reminder

            delete_reminder(rid)
        except Exception:
            pass

    _robot("celebrate" if completed else "idle")
    return {
        "ok": True,
        "minutes": elapsed_min,
        "planned": s.get("minutes", 0),
        "label": s.get("label", ""),
        "completed": completed,
    }


def focus_status() -> Dict[str, Any]:
    _require_owner()
    s = active_focus()
    if not s:
        return {"active": False}
    ends = s["ends_at"]
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    remaining = max(0, int((ends - datetime.now(timezone.utc)).total_seconds() / 60))
    return {
        "active": True,
        "label": s.get("label", ""),
        "minutes": s.get("minutes", 0),
        "remaining_min": remaining,
    }
