"""متتبع المصاريف — «صرفت عشرين ع غدا» وملخص آخر الشهر.

Collection: sandy_expenses
  {_id, amount (float), note, category, at (datetime UTC)}

التصنيف اختياري وبسيط؛ الملخص بجمع حسب التصنيف لو موجود.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.utils.time import USER_TZ
from app.utils.user_profiles import active_profile_allows_privileged_access

_COLL = "sandy_expenses"
_mongo_db = None


def init_expenses_store(mongo_db) -> None:
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index([("at", -1)], background=True)
        print("[ExpensesStore] ready")
    except Exception as e:  # noqa: BLE001
        print(f"[ExpensesStore] index skipped: {e}")


def _require_owner() -> None:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")


def add_expense(amount: float, note: str = "", category: str = "") -> bool:
    _require_owner()
    if _mongo_db is None:
        return False
    try:
        amount = float(amount)
    except Exception:
        return False
    if amount <= 0:
        return False
    _mongo_db[_COLL].insert_one(
        {
            "_id": uuid.uuid4().hex,
            "amount": amount,
            "note": str(note or "").strip(),
            "category": str(category or "").strip(),
            "at": datetime.now(timezone.utc),
        }
    )
    return True


def list_expenses(days: int = 30, limit: int = 100) -> List[Dict[str, Any]]:
    _require_owner()
    if _mongo_db is None:
        return []
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    out = []
    for d in _mongo_db[_COLL].find({"at": {"$gte": since}}).sort("at", -1).limit(limit):
        at = d.get("at")
        if at and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        out.append(
            {
                "id": d["_id"],
                "amount": d.get("amount", 0),
                "note": d.get("note", ""),
                "category": d.get("category", ""),
                "at": at.astimezone(USER_TZ).isoformat() if at else "",
            }
        )
    return out


def delete_expense(expense_id: str) -> bool:
    _require_owner()
    if _mongo_db is None or not expense_id:
        return False
    return _mongo_db[_COLL].delete_one({"_id": expense_id}).deleted_count > 0


def month_summary(days: int = 30) -> Dict[str, Any]:
    """{total, count, by_category: {cat: total}, top: [(note, amount)]}"""
    _require_owner()
    items = list_expenses(days=days, limit=1000)
    total = sum(x["amount"] for x in items)
    by_cat: Dict[str, float] = {}
    for x in items:
        cat = x["category"] or "غير مصنف"
        by_cat[cat] = by_cat.get(cat, 0) + x["amount"]
    top = sorted(items, key=lambda x: -x["amount"])[:5]
    return {
        "total": round(total, 2),
        "count": len(items),
        "by_category": {k: round(v, 2) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])},
        "top": [(x["note"] or x["category"] or "؟", x["amount"]) for x in top],
    }
