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


def start_focus(focus_min: int = 25, label: str = "", break_min: int = 0,
                cycles: int = 1, scene: str = "", end_scene: str = "") -> Dict[str, Any]:
    """يبدأ جلسة تركيز/بومودورو ويشغّل مشهد الغرفة المربوط فيها.

    focus_min/break_min/cycles كلها يحددها المالك. `scene` بيتطبّق عند البداية.
    `end_scene` (اختياري) بيتطبّق لما تخلص الجلسة كلها — وبدونه الغرفة بتضل
    على حالها فما يطفّي إشي وإنت لسا موجود. انتقالات الأطوار بتمر عبر
    advance_focus_phase() اللي بتنده الجدولة كل دقيقة.
    """
    _require_owner()
    if _mongo_db is None:
        return {"ok": False}
    if active_focus():
        return {"ok": False, "error": "already_active"}
    focus_min = max(1, min(240, int(focus_min or 25)))
    break_min = max(0, min(120, int(break_min or 0)))
    cycles = max(1, min(12, int(cycles or 1)))
    now = datetime.now(timezone.utc)

    scene_result = None
    if scene:
        try:
            from app.features.scene_store import apply_scene
            scene_result = apply_scene(scene)
        except Exception as e:  # noqa: BLE001
            print(f"[FocusStore] scene apply failed: {e}")

    _mongo_db[_COLL].insert_one(
        {
            "_id": uuid.uuid4().hex,
            "label": str(label or "").strip(),
            "scene": str(scene or "").strip().lower(),
            "end_scene": str(end_scene or "").strip().lower(),
            "focus_min": focus_min,
            "break_min": break_min,
            "cycles": cycles,
            "cycle_idx": 1,
            "phase": "focus",
            "phase_ends_at": now + timedelta(minutes=focus_min),
            "started_at": now,
            "state": "active",
        }
    )
    _robot("start")
    return {
        "ok": True, "focus_min": focus_min, "break_min": break_min,
        "cycles": cycles, "label": label, "scene": scene,
        "scene_online": bool(scene_result and scene_result.get("online")),
    }


def _aware(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def stop_focus(completed: bool = True) -> Dict[str, Any]:
    """ينهي الجلسة. completed=True إنجاز (احتفال)، False = إلغاء.
    لو الجلسة مربوط فيها end_scene بيتطبّق عند الإنجاز."""
    _require_owner()
    s = active_focus()
    if not s:
        return {"ok": False, "error": "no_session"}

    now = datetime.now(timezone.utc)
    started = _aware(s["started_at"])
    elapsed_min = max(0, int((now - started).total_seconds() / 60))

    _mongo_db[_COLL].update_one(
        {"_id": s["_id"]},
        {"$set": {"state": "done" if completed else "cancelled", "ended_at": now}},
    )
    if completed and s.get("end_scene"):
        try:
            from app.features.scene_store import apply_scene
            apply_scene(s["end_scene"])
        except Exception:
            pass

    _robot("celebrate" if completed else "idle")
    return {
        "ok": True,
        "minutes": elapsed_min,
        "planned": s.get("focus_min", s.get("minutes", 0)),
        "label": s.get("label", ""),
        "completed": completed,
    }


def advance_focus_phase() -> Optional[Dict[str, Any]]:
    """تنقل جلسة البومودورو لطورها التالي لو خلص وقت الطور الحالي.

    بترجع حدث {event: focus|break|done, ...} للجدولة تبعت إشعاره، أو None لو ما
    في شي مستحق. عند الرجوع للتركيز بتعيد تطبيق المشهد (لأن الراحة أو مؤقت
    داخل المشهد ممكن يكون غيّر الغرفة).
    """
    if _mongo_db is None:
        return None
    s = active_focus()
    if not s or s.get("state") != "active":
        return None
    pe = _aware(s.get("phase_ends_at"))
    now = datetime.now(timezone.utc)
    if pe is None or now < pe:
        return None

    phase = s.get("phase", "focus")
    cycle_idx = int(s.get("cycle_idx", 1))
    cycles = int(s.get("cycles", 1))
    focus_min = int(s.get("focus_min", 25))
    break_min = int(s.get("break_min", 0))
    label = s.get("label", "")

    # خلص آخر طور تركيز → إنهاء الجلسة كلها
    if phase == "focus" and cycle_idx >= cycles:
        r = stop_focus(completed=True)
        return {"event": "done", "label": label, "cycles": cycles,
                "minutes": r.get("minutes", 0)}

    # خلص تركيز وفي راحة → ادخل طور الراحة
    if phase == "focus" and break_min > 0:
        _mongo_db[_COLL].update_one(
            {"_id": s["_id"]},
            {"$set": {"phase": "break", "phase_ends_at": now + timedelta(minutes=break_min)}},
        )
        _robot("idle")
        return {"event": "break", "break_min": break_min,
                "cycle_idx": cycle_idx, "cycles": cycles, "label": label}

    # خلصت راحة (أو تركيز بدون راحة) → دورة تركيز جديدة
    cycle_idx += 1
    _mongo_db[_COLL].update_one(
        {"_id": s["_id"]},
        {"$set": {"phase": "focus", "cycle_idx": cycle_idx,
                  "phase_ends_at": now + timedelta(minutes=focus_min)}},
    )
    if s.get("scene"):
        try:
            from app.features.scene_store import apply_scene
            apply_scene(s["scene"])
        except Exception:
            pass
    _robot("start")
    return {"event": "focus", "cycle_idx": cycle_idx, "cycles": cycles,
            "focus_min": focus_min, "label": label}


def focus_status() -> Dict[str, Any]:
    _require_owner()
    s = active_focus()
    if not s:
        return {"active": False}
    pe = _aware(s.get("phase_ends_at"))
    remaining = max(0, int((pe - datetime.now(timezone.utc)).total_seconds() / 60)) if pe else 0
    return {
        "active": True,
        "label": s.get("label", ""),
        "scene": s.get("scene", ""),
        "phase": s.get("phase", "focus"),
        "cycle_idx": int(s.get("cycle_idx", 1)),
        "cycles": int(s.get("cycles", 1)),
        "focus_min": int(s.get("focus_min", s.get("minutes", 0))),
        "break_min": int(s.get("break_min", 0)),
        "remaining_min": remaining,
    }
