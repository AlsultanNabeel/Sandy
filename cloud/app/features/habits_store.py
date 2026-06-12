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
