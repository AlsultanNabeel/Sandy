"""قائمة التسوق — Mongo، متاحة من الشات والصوت والويب.

Collection: sandy_shopping
  {_id, text, category, done (انشترى؟), price, qty, created_at, bought_at}

ضيف (بتصنيف)، اعرض، اشطب (انشترى — ولو فيه سعر بنضيفه للمصاريف تلقائياً)،
احذف، فضّي المشتراة.
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


def add_item(text: str, category: str = "") -> bool:
    """يضيف عنصر واحد مع تصنيف اختياري؛ يتجاهل المكرر النشط."""
    _require_owner()
    coll = _coll()
    if coll is None:
        return False
    text = str(text or "").strip()
    if not text:
        return False
    existing = {
        (d.get("text", "") or "").strip().lower()
        for d in coll.find({"done": False}, {"text": 1})
    }
    if text.lower() in existing:
        return False
    coll.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "text": text,
            "category": str(category or "").strip(),
            "done": False,
            "price": 0.0,
            "qty": 1,
            "unit": "",
            "created_at": datetime.now(timezone.utc),
            "bought_at": None,
        }
    )
    return True


def add_items(texts: List[str]) -> int:
    """يضيف عنصر أو أكثر (نص فقط — للشات والصوت)؛ يرجّع عدد المضاف."""
    return sum(1 for raw in texts if add_item(raw))


def list_items(include_bought: bool = False) -> List[Dict[str, Any]]:
    _require_owner()
    coll = _coll()
    if coll is None:
        return []
    q = {} if include_bought else {"done": False}
    out = []
    for d in coll.find(q).sort("created_at", 1).limit(200):
        out.append({
            "id": d["_id"],
            "text": d.get("text", ""),
            "done": bool(d.get("done")),
            "category": d.get("category", ""),
            "price": d.get("price", 0) or 0,
            "qty": d.get("qty", 1) or 1,
            "unit": d.get("unit", ""),
        })
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


def check_item_by_id(item_id: str, price=None, qty=None) -> Dict[str, Any]:
    """يشطب عنصر (انشترى). لو فيه سعر (ممرّر الآن أو محفوظ) بنضيفه تلقائياً
    للمصاريف بتصنيف العنصر. يرجّع {ok, expense_added}."""
    _require_owner()
    coll = _coll()
    if coll is None or not item_id:
        return {"ok": False, "expense_added": False}
    d = coll.find_one({"_id": item_id})
    if not d:
        return {"ok": False, "expense_added": False}

    set_fields = {"done": True, "bought_at": datetime.now(timezone.utc)}
    eff_price = d.get("price", 0) or 0
    eff_qty = d.get("qty", 1) or 1
    if price is not None:
        try:
            eff_price = float(price)
            set_fields["price"] = eff_price
        except (TypeError, ValueError):
            pass
    if qty is not None:
        try:
            eff_qty = max(1, int(qty))
            set_fields["qty"] = eff_qty
        except (TypeError, ValueError):
            pass
    coll.update_one({"_id": item_id}, {"$set": set_fields})

    expense_added = False
    if eff_price and eff_price > 0:
        unit = d.get("unit", "")
        note = d.get("text", "")
        if eff_qty and eff_qty != 1:
            note = f"{note} ({eff_qty}{(' ' + unit) if unit else ''})"
        try:
            from app.features.expenses_store import add_expense
            expense_added = add_expense(
                eff_price * eff_qty,
                note=note,
                category=d.get("category", ""),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[ShoppingStore] expense link failed: {e}")
    return {"ok": True, "expense_added": expense_added}


def set_item_purchase(item_id: str, price=None, qty=None, unit=None) -> bool:
    """يحدّد سعر/كمية/وحدة عنصر بدون ما يشطبه — للإجمالي التقديري قبل الشراء."""
    _require_owner()
    coll = _coll()
    if coll is None or not item_id:
        return False
    set_fields: Dict[str, Any] = {}
    if price is not None:
        try:
            set_fields["price"] = float(price)
        except (TypeError, ValueError):
            pass
    if qty is not None:
        try:
            set_fields["qty"] = max(1, int(qty))
        except (TypeError, ValueError):
            pass
    if unit is not None:
        set_fields["unit"] = str(unit or "").strip()
    if not set_fields:
        return False
    return coll.update_one({"_id": item_id}, {"$set": set_fields}).matched_count > 0


def last_price_for(text: str) -> float:
    """آخر سعر مدفوع لصنف بنفس الاسم (من عنصر مشترى سابقاً). 0 لو ما في."""
    _require_owner()
    coll = _coll()
    if coll is None:
        return 0
    text = str(text or "").strip()
    if not text:
        return 0
    d = coll.find_one(
        {"text": text, "done": True, "price": {"$gt": 0}},
        sort=[("bought_at", -1)],
    )
    return float(d.get("price", 0)) if d else 0


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
