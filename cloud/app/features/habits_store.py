"""متتبع العادات — سلاسل إنجاز يومية على Mongo.

Collections:
  sandy_habits   {_id, name, created_at, archived}
  sandy_habit_log {_id, habit_id, date "YYYY-MM-DD"}  ← تسجيلة واحدة باليوم

السلسلة (streak) تتحسب وقت القراءة من السجل — بدون عدادات تتعفن.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.utils.time import USER_TZ
from app.utils.user_profiles import active_profile_allows_privileged_access

_HABITS = "sandy_habits"
_LOG = "sandy_habit_log"
_mongo_db = None


def init_habits_store(mongo_db) -> None:
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_LOG].create_index([("habit_id", 1), ("date", -1)], background=True)
        print("[HabitsStore] ready")
    except Exception as e:  # noqa: BLE001
        print(f"[HabitsStore] index skipped: {e}")


def _require_owner() -> None:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")


def _today() -> str:
    return datetime.now(USER_TZ).date().isoformat()


def _find_habit(name: str) -> Optional[Dict[str, Any]]:
    nl = str(name or "").strip().lower()
    if not nl or _mongo_db is None:
        return None
    for d in _mongo_db[_HABITS].find({"archived": {"$ne": True}}):
        if nl in (d.get("name", "") or "").lower():
            return d
    return None


def _find_by_id(habit_id: str) -> Optional[Dict[str, Any]]:
    if not habit_id or _mongo_db is None:
        return None
    return _mongo_db[_HABITS].find_one({"_id": habit_id})


def add_habit(name: str) -> bool:
    _require_owner()
    if _mongo_db is None:
        return False
    name = str(name or "").strip()
    if not name or _find_habit(name):
        return False
    _mongo_db[_HABITS].insert_one(
        {
            "_id": uuid.uuid4().hex,
            "name": name,
            "created_at": datetime.now(timezone.utc),
            "archived": False,
        }
    )
    return True


def archive_habit(name: str) -> str:
    _require_owner()
    h = _find_habit(name)
    if not h:
        return ""
    _mongo_db[_HABITS].update_one({"_id": h["_id"]}, {"$set": {"archived": True}})
    return h.get("name", "")


def checkin(name: str, date: str = "") -> Dict[str, Any]:
    """يسجل إنجاز اليوم (أو تاريخ معطى). يرجّع {ok, name, streak, already}."""
    _require_owner()
    h = _find_habit(name)
    if not h or _mongo_db is None:
        return {"ok": False}
    d = (date or _today())[:10]
    key = f"{h['_id']}:{d}"
    already = _mongo_db[_LOG].find_one({"_id": key}) is not None
    if not already:
        _mongo_db[_LOG].insert_one({"_id": key, "habit_id": h["_id"], "date": d})
    return {"ok": True, "name": h.get("name", ""), "streak": _streak(h["_id"]), "already": already}


def _streak(habit_id: str) -> int:
    """أيام متتالية لليوم (أو لمبارح إذا اليوم لسا ما انعمل)."""
    if _mongo_db is None:
        return 0
    dates = {
        d["date"]
        for d in _mongo_db[_LOG].find({"habit_id": habit_id}, {"date": 1}).limit(2000)
    }
    if not dates:
        return 0
    day = datetime.now(USER_TZ).date()
    if day.isoformat() not in dates:
        day = day - timedelta(days=1)   # اليوم لسا بدري — السلسلة محسوبة لمبارح
    streak = 0
    while day.isoformat() in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def list_habits() -> List[Dict[str, Any]]:
    """كل العادات النشطة مع سلسلة كل وحدة وهل انعملت اليوم."""
    _require_owner()
    if _mongo_db is None:
        return []
    today = _today()
    out = []
    for h in _mongo_db[_HABITS].find({"archived": {"$ne": True}}).sort("created_at", 1):
        done_today = _mongo_db[_LOG].find_one({"_id": f"{h['_id']}:{today}"}) is not None
        out.append(
            {
                "id": h["_id"],
                "name": h.get("name", ""),
                "streak": _streak(h["_id"]),
                "done_today": done_today,
            }
        )
    return out


def delete_habit(habit_id: str) -> bool:
    """حذف نهائي: العادة + كل سجلّاتها (مش أرشفة)."""
    _require_owner()
    h = _find_by_id(habit_id)
    if not h or _mongo_db is None:
        return False
    _mongo_db[_LOG].delete_many({"habit_id": habit_id})
    _mongo_db[_HABITS].delete_one({"_id": habit_id})
    return True


def rename_habit(habit_id: str, new_name: str) -> bool:
    """إعادة تسمية العادة (بعد فحص التكرار مع عادة أخرى نشطة)."""
    _require_owner()
    new_name = str(new_name or "").strip()
    h = _find_by_id(habit_id)
    if not h or not new_name or _mongo_db is None:
        return False
    dup = _find_habit(new_name)
    if dup and dup.get("_id") != habit_id:
        return False
    _mongo_db[_HABITS].update_one({"_id": habit_id}, {"$set": {"name": new_name}})
    return True


def uncheckin(habit_id: str, date: str = "") -> Dict[str, Any]:
    """يتراجع عن إنجاز يوم (افتراضياً اليوم). يرجّع {ok, streak, removed}."""
    _require_owner()
    h = _find_by_id(habit_id)
    if not h or _mongo_db is None:
        return {"ok": False}
    d = (date or _today())[:10]
    res = _mongo_db[_LOG].delete_one({"_id": f"{habit_id}:{d}"})
    return {"ok": True, "streak": _streak(habit_id), "removed": res.deleted_count > 0}


def habit_history(habit_id: str, days: int = 35) -> Dict[str, Any]:
    """تفاصيل عادة: أيام الإنجاز بآخر فترة، أطول سلسلة، ونسبة الالتزام منذ الإنشاء."""
    _require_owner()
    h = _find_by_id(habit_id)
    if not h or _mongo_db is None:
        return {"ok": False}
    all_dates = sorted(
        d["date"]
        for d in _mongo_db[_LOG].find({"habit_id": habit_id}, {"date": 1}).limit(5000)
    )
    date_set = set(all_dates)
    today = datetime.now(USER_TZ).date()

    # أيام آخر فترة (للعرض كنقاط) مع علم الإنجاز
    window = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        window.append({"date": day, "done": day in date_set})

    # أطول سلسلة متتالية على الإطلاق
    longest = 0
    run = 0
    prev = None
    for ds in all_dates:
        cur = datetime.fromisoformat(ds).date()
        if prev is not None and (cur - prev).days == 1:
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev = cur

    # نسبة الالتزام منذ الإنشاء (أيام منجزة / أيام منقضية)
    created = h.get("created_at")
    try:
        start = created.astimezone(USER_TZ).date() if created else today
    except Exception:  # noqa: BLE001
        start = today
    elapsed = max(1, (today - start).days + 1)
    rate = round(100 * len(date_set) / elapsed)

    return {
        "ok": True,
        "id": habit_id,
        "name": h.get("name", ""),
        "window": window,
        "longest": longest,
        "rate": min(100, rate),
        "total_done": len(date_set),
    }
