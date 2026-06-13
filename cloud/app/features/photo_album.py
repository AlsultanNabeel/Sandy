"""ألبوم الصور (Photo Album).

ساندي تحفظ الصور اللي بتوصلها على تيليجرام، تعمل لها وصف ووسوم ذكية
(عبر Vision)، وتقدر ترجّعها لاحقاً بالاسم أو الوسم أو الوصف.

التخزين:
  بايتات الصورة في GridFS (مجموعة sandy_photo_files) عشان يتحمّل أي حجم.
  الميتاداتا في مجموعة sandy_photos:
    {chat_id, name, grid_id, file_unique_id, user_caption, ai_caption, tags[], created_at}

لو Mongo مش متاح كل دالة بترجّع فاضي بهدوء.
عرض الصور على شاشة ESP32 لاحق، هون السحابة وتيليجرام بس.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_META = "sandy_photos"
_FILES_COLLECTION = "sandy_photo_files"

_mongo_db = None
_gridfs = None


# إعداد المخزن
def init_photo_album(mongo_db) -> None:
    """يُستدعى مرّة عند الإقلاع (زي init_speaker_store)."""
    global _mongo_db, _gridfs
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        from gridfs import GridFS
        _gridfs = GridFS(mongo_db, collection=_FILES_COLLECTION)
        mongo_db[_META].create_index([("chat_id", 1), ("created_at", -1)], background=True)
        mongo_db[_META].create_index([("chat_id", 1), ("file_unique_id", 1)], background=True)
        logger.info("[photo_album] ready")
    except Exception as e:  # noqa: BLE001
        logger.warning("[photo_album] init failed: %s", e)
        _gridfs = None


def is_available() -> bool:
    return _mongo_db is not None and _gridfs is not None


# الحفظ
def save_photo(
    chat_id: Any,
    image_bytes: bytes,
    *,
    file_unique_id: Optional[str] = None,
    name: Optional[str] = None,
    user_caption: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """يخزّن صورة (بايتات + ميتاداتا). يتجاهل التكرار حسب file_unique_id.

    الوسوم/الوصف الذكي يتعمّلوا لاحقاً عبر `set_ai_metadata` (ممكن بالخلفية).
    يرجّع الميتاداتا المخزّنة، أو None لو فشل/تكرار.
    """
    if not is_available() or not image_bytes:
        return None
    cid = str(chat_id)

    # تجاهل لو نفس الصورة محفوظة (نفس المستخدم)
    if file_unique_id:
        existing = _mongo_db[_META].find_one(
            {"chat_id": cid, "file_unique_id": file_unique_id}
        )
        if existing:
            return existing

    try:
        grid_id = _gridfs.put(image_bytes, filename=f"{cid}_{file_unique_id or 'photo'}")
    except Exception as e:  # noqa: BLE001
        logger.warning("[photo_album] gridfs put failed: %s", e)
        return None

    caption = (user_caption or "").strip()
    doc = {
        "chat_id": cid,
        "name": (name or "").strip() or _default_name(caption),
        "grid_id": grid_id,
        "file_unique_id": file_unique_id,
        "user_caption": caption,
        "ai_caption": "",
        "tags": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    res = _mongo_db[_META].insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


def _default_name(caption: str) -> str:
    if caption:
        return caption[:40]
    return "صورة " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def set_ai_metadata(photo_id: Any, caption: str, tags: List[str]) -> None:
    """يحدّث الوصف + الوسوم الذكية بعد توليدها (ممكن من thread بالخلفية)."""
    if not is_available():
        return
    update: Dict[str, Any] = {}
    if caption:
        update["ai_caption"] = caption.strip()
    if tags:
        update["tags"] = [t.strip() for t in tags if t and t.strip()][:12]
    if not update:
        return
    try:
        _mongo_db[_META].update_one({"_id": photo_id}, {"$set": update})
    except Exception as e:  # noqa: BLE001
        logger.debug("[photo_album] set_ai_metadata failed: %s", e)


def generate_tags(image_bytes: bytes, create_chat_completion_fn) -> Tuple[str, List[str]]:
    """Vision → (وصف قصير بالعربي، قائمة وسوم). يرجّع ("", []) لو فشل."""
    if not image_bytes or create_chat_completion_fn is None:
        return "", []
    import base64

    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64}"
        prompt = (
            "حلّلي الصورة وأرجعي JSON فقط بهذا الشكل بدون أي نص إضافي:\n"
            '{"caption": "وصف قصير بالعربية بجملة واحدة", '
            '"tags": ["وسم1", "وسم2", "..."]}\n'
            "الوسوم كلمات مفتاحية عربية مفردة (أشخاص، مكان، مناسبة، ألوان، أشياء بارزة) — من 3 لـ 8 وسوم."
        )
        response = create_chat_completion_fn(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0.4,
            max_tokens=300,
            prefer_azure=True,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        caption = str(data.get("caption", "")).strip()
        tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
        return caption, tags
    except Exception as e:  # noqa: BLE001
        logger.info("[photo_album] generate_tags failed (no tags saved): %s", e)
        return "", []


# الاسترجاع
def _matches(doc: Dict[str, Any], query: str) -> bool:
    q = query.lower()
    hay = " ".join([
        str(doc.get("name", "")),
        str(doc.get("user_caption", "")),
        str(doc.get("ai_caption", "")),
        " ".join(doc.get("tags", []) or []),
    ]).lower()
    return all(tok in hay for tok in q.split())


def find_photos(
    chat_id: Any,
    query: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """يرجّع ميتاداتا الصور المطابقة (الأحدث أولاً). بدون query/tag → كل الصور."""
    if not is_available():
        return []
    cid = str(chat_id)
    mongo_filter: Dict[str, Any] = {"chat_id": cid}
    if tag:
        mongo_filter["tags"] = tag.strip()
    try:
        cursor = _mongo_db[_META].find(mongo_filter).sort("created_at", -1)
        docs = list(cursor.limit(500))
    except Exception as e:  # noqa: BLE001
        logger.warning("[photo_album] find failed: %s", e)
        return []
    if query and query.strip():
        docs = [d for d in docs if _matches(d, query)]
    return docs[:limit]


def get_photo_bytes(chat_id: Any, query: str) -> Optional[Tuple[bytes, Dict[str, Any]]]:
    """يرجّع (بايتات، ميتاداتا) لأفضل صورة مطابقة، أو None."""
    matches = find_photos(chat_id, query=query, limit=1)
    if not matches:
        return None
    doc = matches[0]
    try:
        data = _gridfs.get(doc["grid_id"]).read()
    except Exception as e:  # noqa: BLE001
        logger.warning("[photo_album] gridfs get failed: %s", e)
        return None
    return data, doc


def count_photos(chat_id: Any) -> int:
    if not is_available():
        return 0
    try:
        return _mongo_db[_META].count_documents({"chat_id": str(chat_id)})
    except Exception:  # noqa: BLE001
        return 0


def delete_photo(chat_id: Any, query: str) -> Tuple[bool, str]:
    """يحذف أفضل صورة مطابقة (ميتاداتا + بايتات). يرجّع (تمّ؟، الاسم/رسالة)."""
    matches = find_photos(chat_id, query=query, limit=1)
    if not matches:
        return False, "ما لقيت صورة بهالوصف."
    doc = matches[0]
    try:
        _gridfs.delete(doc["grid_id"])
    except Exception as e:  # noqa: BLE001
        logger.debug("[photo_album] gridfs delete: %s", e)
    _mongo_db[_META].delete_one({"_id": doc["_id"]})
    return True, str(doc.get("name", "الصورة"))


def rename_photo(chat_id: Any, query: str, new_name: str) -> Tuple[bool, str]:
    """يعيد تسمية أفضل صورة مطابقة. يرجّع (تمّ؟، رسالة)."""
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "لازم اسم جديد."
    matches = find_photos(chat_id, query=query, limit=1)
    if not matches:
        return False, "ما لقيت صورة بهالوصف."
    doc = matches[0]
    _mongo_db[_META].update_one({"_id": doc["_id"]}, {"$set": {"name": new_name}})
    return True, new_name
