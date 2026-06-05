"""Phase 8 — أدوات ألبوم الصور.

ساندي بتستعملها لما المستخدم يطلب صورة محفوظة: عرض / قائمة / حذف / إعادة تسمية.
الحفظ التلقائي بيصير في telegram_handlers عند وصول الصورة — مش هون.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)


def _chat_id(ctx: "DispatchContext") -> str:
    return str((ctx.state or {}).get("chat_id", "") or "")


def save_photo(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحفظ الصورة الحالية (آخر صورة وصلت) في الألبوم لما يطلب المستخدم."""
    from app.features import photo_album

    chat_id = _chat_id(ctx)
    session = ctx.session or {}
    image_state = session.get("image_state") or {}
    img = image_state.get("active_image_bytes") or session.get("last_image_bytes")
    if not img:
        return {"handled": True, "reply": "ما في صورة حالية أحفظها — ابعتلي صورة الأول 🖼"}

    name = str(args.get("name") or "").strip() or None
    uid = image_state.get("active_image_uid")
    saved = photo_album.save_photo(
        chat_id, img, file_unique_id=uid, name=name, user_caption=name or ""
    )
    if not saved:
        return {"handled": True, "reply": "ما قدرت أحفظ الصورة."}

    # وسوم ذكية بالخلفية (ما نأخّر الرد)
    if not saved.get("ai_caption"):
        fn = ctx.create_chat_completion_fn

        def _bg(pid, b):
            cap, tags = photo_album.generate_tags(b, fn)
            if cap or tags:
                photo_album.set_ai_metadata(pid, cap, tags)

        threading.Thread(target=_bg, args=(saved["_id"], img), daemon=True).start()

    nm = saved.get("name") or "الصورة"
    return {"handled": True, "reply": f"حفظت الصورة «{nm}» بالألبوم ✅"}


def show_photo(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يلاقي صورة محفوظة بالاسم/الوصف ويرجّعها كصورة."""
    from app.features import photo_album

    query = str(args.get("query") or "").strip()
    chat_id = _chat_id(ctx)
    if not chat_id:
        return {"handled": True, "reply": "ما قدرت أحدد المحادثة."}

    found = photo_album.get_photo_bytes(chat_id, query)
    if not found:
        return {"handled": True, "reply": "ما لقيت صورة بهالوصف 🖼"}
    data, doc = found
    caption = doc.get("name") or doc.get("ai_caption") or "صورتك 🖼"
    return {
        "handled": True,
        "reply": f"تفضّل: {caption} 🖼",
        "image_bytes": data,
        "image_source": "album",
        "caption": caption,
    }


def list_photos(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض قائمة الصور المحفوظة (اختياري: حسب وسم)."""
    from app.features import photo_album

    chat_id = _chat_id(ctx)
    tag = str(args.get("tag") or "").strip() or None
    docs = photo_album.find_photos(chat_id, tag=tag, limit=20)
    if not docs:
        return {"handled": True, "reply": "ما في صور محفوظة لسا 🖼"}

    lines = []
    for i, d in enumerate(docs, 1):
        name = d.get("name") or d.get("ai_caption") or "بدون اسم"
        tags = d.get("tags") or []
        tag_str = f"  ({'، '.join(tags[:4])})" if tags else ""
        lines.append(f"{i}. {name}{tag_str}")
    total = photo_album.count_photos(chat_id)
    header = f"📸 عندك {total} صورة" + (f" بوسم «{tag}»" if tag else "") + ":\n"
    return {"handled": True, "reply": header + "\n".join(lines)}


def delete_photo(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحذف صورة محفوظة بالاسم/الوصف."""
    from app.features import photo_album

    chat_id = _chat_id(ctx)
    query = str(args.get("query") or "").strip()
    ok, msg = photo_album.delete_photo(chat_id, query)
    if ok:
        return {"handled": True, "reply": f"حذفت «{msg}» ✅"}
    return {"handled": True, "reply": msg}


def rename_photo(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعيد تسمية صورة محفوظة."""
    from app.features import photo_album

    chat_id = _chat_id(ctx)
    query = str(args.get("query") or "").strip()
    new_name = str(args.get("new_name") or "").strip()
    ok, msg = photo_album.rename_photo(chat_id, query, new_name)
    if ok:
        return {"handled": True, "reply": f"صار اسمها «{msg}» ✅"}
    return {"handled": True, "reply": msg}


PHOTO_TOOLS = [
    {
        "name": "save_photo",
        "description": (
            "احفظي الصورة الحالية (آخر صورة بعتها المستخدم) في الألبوم. "
            "استعمليها لما يقول «احفظي هاي الصورة» أو «خزّنيها» أو «احفظيها باسم كذا». "
            "name اختياري — اسم تعطيه الصورة."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "اسم للصورة (اختياري)"},
            },
            "required": [],
        },
        "handler": save_photo,
    },
    {
        "name": "show_photo",
        "description": (
            "اعرضي صورة محفوظة من ألبوم المستخدم. استعمليها لما يطلب صورة "
            "(مثلاً: «ورجيني صورة العائلة»، «الصورة اللي بعتها امبارح»). "
            "حطّي وصفه في query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "وصف/اسم/وسم الصورة المطلوبة كما ذكره المستخدم",
                },
            },
            "required": ["query"],
        },
        "handler": show_photo,
    },
    {
        "name": "list_photos",
        "description": (
            "اعرضي قائمة الصور المحفوظة في ألبوم المستخدم. "
            "استعمليها لما يسأل «شو الصور المحفوظة؟» أو «كم صورة عندي؟». "
            "tag اختياري لتصفية حسب وسم."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "وسم للتصفية (اختياري)"},
            },
            "required": [],
        },
        "handler": list_photos,
    },
    {
        "name": "delete_photo",
        "description": "احذفي صورة محفوظة من الألبوم بالاسم/الوصف.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "وصف/اسم الصورة المراد حذفها"},
            },
            "required": ["query"],
        },
        "handler": delete_photo,
    },
    {
        "name": "rename_photo",
        "description": "أعيدي تسمية صورة محفوظة في الألبوم.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "وصف/اسم الصورة الحالي"},
                "new_name": {"type": "string", "description": "الاسم الجديد"},
            },
            "required": ["query", "new_name"],
        },
        "handler": rename_photo,
    },
]
