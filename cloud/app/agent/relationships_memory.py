"""تذكّر أسماء العلاقات المهمة.

Sandy بتطلع أسماء الأهل والأصحاب والزملاء من رسائل المستخدم، بتخزّنهم
في sandy_memories، وبترجّعهم في soul_node عشان تغني persona_snippet.

نفس نمط الحفظ اللي في emotional_ltm.py و style_memory.py، بـ label="relationship".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_COLL = "sandy_memories"
_LABEL = "relationship"

# كلمة العلاقة زي ما يكتبها المستخدم، والكلمة الموحّدة اللي بنخزّنها
_RELATION_TERMS = {
    "أبوي": "والد", "ابوي": "والد", "بابا": "والد", "والدي": "والد",
    "أمي": "والدة", "امي": "والدة", "ماما": "والدة", "والدتي": "والدة",
    "أخوي": "أخ", "اخوي": "أخ", "أخويا": "أخ", "اخويا": "أخ",
    "أختي": "أخت", "اختي": "أخت", "أختا": "أخت",
    "زوجتي": "زوجة", "مرتي": "زوجة", "حرمتي": "زوجة",
    "زوجي": "زوج", "جوزي": "زوج",
    "ابني": "ابن", "ولدي": "ابن",
    "بنتي": "بنت", "ابنتي": "بنت",
    "صديقي": "صديق", "صاحبي": "صديق", "رفيقي": "صديق",
    "صديقتي": "صديقة", "صاحبتي": "صديقة",
    "جاري": "جار", "جارتي": "جارة",
    "مديري": "مدير", "زميلي": "زميل", "زميلتي": "زميلة",
    "حبيبي": "شريك", "حبيبتي": "شريكة",
    "خالي": "خال", "خالتي": "خالة", "عمي": "عم", "عمتي": "عمة",
}

# بنقبل بس الحالات الواضحة عشان نقلّل الـ false positives، يعني لازم
# يكون في marker صريح زي "اسمه" أو "اسمها"، مثل "صديقي اسمه أحمد".
# أي شي بدون marker بنتجاهله.
_RELATION_NAME_RE = re.compile(
    r"(?:^|[\s،,])[وبلف]?(?P<rel>" + "|".join(_RELATION_TERMS.keys()) + r")\s+"
    r"(?:اسمه|اسمها|يسمى|تسمى)\s+(?P<name>[ء-ي]{2,15})\b"
)


def detect_relationships(message: str) -> List[Tuple[str, str]]:
    """يطلّع (relation, name) من الرسالة، أو قائمة فاضية لو ما في.

    >>> detect_relationships("أخوي اسمه محمد")
    [('أخ', 'محمد')]
    """
    found: List[Tuple[str, str]] = []
    if not message:
        return found

    for m in _RELATION_NAME_RE.finditer(message):
        rel_word = m.group("rel")
        name = m.group("name").strip()
        relation = _RELATION_TERMS.get(rel_word, rel_word)
        if name in _RELATION_TERMS or len(name) < 2:
            continue
        found.append((relation, name))
    return found


def save_relationship(
    chat_id: str,
    user_id: str,
    relation: str,
    name: str,
    mongo_db=None,
) -> bool:
    """يحفظ علاقة جديدة. ما يكرّر نفس (relation, name) لو موجودة."""
    if mongo_db is None or not relation or not name:
        return False
    try:
        existing = mongo_db[_COLL].find_one(
            {"chat_id": str(chat_id), "label": _LABEL, "relation": relation, "name": name},
            {"_id": 1},
        )
        if existing:
            return False
        mongo_db[_COLL].insert_one({
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "label": _LABEL,
            "relation": relation,
            "name": name,
            "created_at": datetime.now(timezone.utc),
        })
        logger.info(f"[relationships] saved: {relation}={name}")
        return True
    except Exception as exc:
        logger.debug(f"[relationships] save failed: {exc}")
        return False


def get_relationships_context(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    limit: int = 10,
) -> Optional[str]:
    """يرجّع علاقات المستخدم كـ context لـ soul_node."""
    if mongo_db is None:
        return None
    try:
        docs = list(mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "label": _LABEL},
            {"_id": 0, "relation": 1, "name": 1},
            sort=[("created_at", -1)],
            limit=limit,
        ))
    except Exception:
        return None

    if not docs:
        return None

    parts = [f"{d['relation']} {d['name']}" for d in docs if d.get("name")]
    return "[علاقات: " + " · ".join(parts) + "]" if parts else None


def save_detected_relationships(
    chat_id: str,
    user_id: str,
    message: str,
    mongo_db=None,
) -> int:
    """يكتشف ويحفظ بخطوة وحدة. بيستدعيه graph.py في thread بالخلفية."""
    saved = 0
    for relation, name in detect_relationships(message):
        if save_relationship(chat_id, user_id, relation, name, mongo_db):
            saved += 1
    return saved
