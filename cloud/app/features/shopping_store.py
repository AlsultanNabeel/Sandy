"""قائمة التسوق — Mongo، متاحة من الشات والصوت والويب.

Collection: sandy_shopping
  {_id, text, done (انشترى؟), created_at, bought_at}

بسيطة عمداً: ضيف، اعرض، اشطب (انشترى)، احذف، فضّي المشتراة.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.utils.user_profiles import active_profile_allows_privileged_access

_COLL = "sandy_shopping"
_mongo_db = None


def init_shopping_store(mongo_db) -> None:
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index([("done", 1), ("created_at", 1)], background=True)
        print("[ShoppingStore] ready")
    except Exception as e:  # noqa: BLE001
        print(f"[ShoppingStore] index skipped: {e}")


def _coll():
    return _mongo_db[_COLL] if _mongo_db is not None else None


def _require_owner() -> None:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")


def add_items(texts: List[str]) -> int:
    """يضيف عنصر أو أكثر؛ يتجاهل المكرر النشط. يرجّع عدد المضاف."""
    _require_owner()
    coll = _coll()
    if coll is None:
        return 0
    existing = {
        (d.get("text", "") or "").strip().lower()
        for d in coll.find({"done": False}, {"text": 1})
    }
    added = 0
    for raw in texts:
        text = str(raw or "").strip()
        if not text or text.lower() in existing:
            continue
        coll.insert_one(
            {
                "_id": uuid.uuid4().hex,
                "text": text,
                "done": False,
                "created_at": datetime.now(timezone.utc),
                "bought_at": None,
            }
        )
        existing.add(text.lower())
        added += 1
    return added


def list_items(include_bought: bool = False) -> List[Dict[str, Any]]:
    _require_owner()
    coll = _coll()
    if coll is None:
        return []
    q = {} if include_bought else {"done": False}
    out = []
    for d in coll.find(q).sort("created_at", 1).limit(200):
        out.append({"id": d["_id"], "text": d.get("text", ""), "done": bool(d.get("done"))})
    return out


def _match(coll, text: str):
    """أقرب عنصر نشط لنص معطى (احتواء، غير حساس لحالة الأحرف)."""
    tl = str(text or "").strip().lower()
    if not tl:
        return None
    for d in coll.find({"done": False}):
        if tl in (d.get("text", "") or "").lower():
            return d
    return None


def check_item(text: str) -> str:
    """يشطب عنصر (انشترى). يرجّع اسم المشطوب أو ""."""
    _require_owner()
    coll = _coll()
    if coll is None:
        return ""
    d = _match(coll, text)
    if not d:
        return ""
    coll.update_one(
        {"_id": d["_id"]},
        {"$set": {"done": True, "bought_at": datetime.now(timezone.utc)}},
    )
    return d.get("text", "")


def remove_item(text: str) -> str:
    """يحذف عنصر نهائياً (مش انشترى — انحذف). يرجّع اسمه أو ""."""
    _require_owner()
    coll = _coll()
    if coll is None:
        return ""
    d = _match(coll, text)
    if not d:
        return ""
    coll.delete_one({"_id": d["_id"]})
    return d.get("text", "")


def check_item_by_id(item_id: str) -> bool:
    _require_owner()
    coll = _coll()
    if coll is None or not item_id:
        return False
    r = coll.update_one(
        {"_id": item_id},
        {"$set": {"done": True, "bought_at": datetime.now(timezone.utc)}},
    )
    return r.matched_count > 0


def delete_item_by_id(item_id: str) -> bool:
    _require_owner()
    coll = _coll()
    if coll is None or not item_id:
        return False
    return coll.delete_one({"_id": item_id}).deleted_count > 0


def clear_bought() -> int:
    _require_owner()
    coll = _coll()
    if coll is None:
        return 0
    return coll.delete_many({"done": True}).deleted_count
