"""أدوات إدارة قناة Telegram.

ساندي بتستعملها لنشر/تعديل/حذف/تثبيت رسائل في القناة الرسمية.
النشر مش فوري — بتعرض معاينة وتطلب تأكيد.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)


def _chat_id(ctx: "DispatchContext") -> str:
    return str((ctx.state or {}).get("chat_id", "") or "")


def _not_configured_reply() -> Dict[str, Any]:
    return {
        "handled": True,
        "reply": (
            "القناة مش مضبوطة بعد ⚙️\n"
            "اضبط `SANDY_SOCIAL_TG_CHANNEL` في متغيّرات البيئة "
            "(مثلاً: @mychannel أو معرّف رقمي) وتأكّد إن البوت أدمن بالقناة."
        ),
    }


# معالجات الأدوات

def tg_channel_post(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يقترح نشر رسالة في القناة ويطلب تأكيداً قبل النشر."""
    from app.integrations.social_telegram import is_configured, propose_post

    if not is_configured():
        return _not_configured_reply()

    text = str(args.get("text") or "").strip()
    photo_url = str(args.get("photo_url") or "").strip() or None
    if not text:
        return {"handled": True, "reply": "شو نص المنشور اللي بدك أنشره؟"}

    chat_id = _chat_id(ctx)
    if not chat_id:
        return {"handled": True, "reply": "ما قدرت أحدد المحادثة."}

    preview = propose_post(chat_id, text, photo_url)
    return {
        "handled": True,
        "reply": (
            f"{preview}\n\n"
            "⚠️ هاد المنشور رح يطلع على القناة العامة.\n"
            "قوليلي «أكّدي النشر» لو بدك أنشر، أو «ألغي» لو غيّرت رأيك."
        ),
    }


def tg_channel_confirm(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """ينشر المنشور المعلّق بعد تأكيد المستخدم."""
    from app.integrations.social_telegram import (
        confirm_post,
        has_pending,
        is_configured,
    )

    if not is_configured():
        return _not_configured_reply()

    chat_id = _chat_id(ctx)
    if not chat_id:
        return {"handled": True, "reply": "ما قدرت أحدد المحادثة."}

    if not has_pending(chat_id):
        return {
            "handled": True,
            "reply": "ما في منشور معلّق أنشره — استعمل «انشري على القناة» الأول.",
        }

    ok, mid, msg = confirm_post(chat_id)
    if ok:
        return {
            "handled": True,
            "reply": f"✅ تم النشر بنجاح! رقم الرسالة: `{mid}`",
        }
    return {
        "handled": True,
        "reply": f"ما قدرت أنشر المنشور ❌\nالسبب: {msg}",
    }


def tg_channel_edit(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعدّل رسالة موجودة في القناة."""
    from app.integrations.social_telegram import edit_message, is_configured

    if not is_configured():
        return _not_configured_reply()

    raw_id = args.get("message_id")
    text = str(args.get("text") or "").strip()
    if not raw_id or not text:
        return {"handled": True, "reply": "محتاجة رقم الرسالة والنص الجديد."}

    try:
        message_id = int(raw_id)
    except (ValueError, TypeError):
        return {"handled": True, "reply": "رقم الرسالة غلط — لازم يكون رقم صحيح."}

    ok, msg = edit_message(message_id, text)
    if ok:
        return {"handled": True, "reply": f"✅ عدّلت الرسالة {message_id}."}
    return {"handled": True, "reply": f"ما قدرت أعدّل الرسالة ❌\nالسبب: {msg}"}


def tg_channel_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحذف رسالة من القناة."""
    from app.integrations.social_telegram import delete_message, is_configured

    if not is_configured():
        return _not_configured_reply()

    raw_id = args.get("message_id")
    if not raw_id:
        return {"handled": True, "reply": "محتاجة رقم الرسالة اللي بدك أحذفها."}

    try:
        message_id = int(raw_id)
    except (ValueError, TypeError):
        return {"handled": True, "reply": "رقم الرسالة غلط — لازم يكون رقم صحيح."}

    ok, msg = delete_message(message_id)
    if ok:
        return {"handled": True, "reply": f"✅ حذفت الرسالة {message_id}."}
    return {"handled": True, "reply": f"ما قدرت أحذف الرسالة ❌\nالسبب: {msg}"}


def tg_channel_pin(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يثبّت رسالة في القناة."""
    from app.integrations.social_telegram import is_configured, pin_message

    if not is_configured():
        return _not_configured_reply()

    raw_id = args.get("message_id")
    if not raw_id:
        return {"handled": True, "reply": "محتاجة رقم الرسالة اللي بدك أثبّتها."}

    try:
        message_id = int(raw_id)
    except (ValueError, TypeError):
        return {"handled": True, "reply": "رقم الرسالة غلط — لازم يكون رقم صحيح."}

    ok, msg = pin_message(message_id)
    if ok:
        return {"handled": True, "reply": f"✅ ثبّتت الرسالة {message_id} بالقناة."}
    return {"handled": True, "reply": f"ما قدرت أثبّت الرسالة ❌\nالسبب: {msg}"}


def tg_channel_recent(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحاول جلب آخر منشورات القناة."""
    from app.integrations.social_telegram import is_configured, recent_posts

    if not is_configured():
        return _not_configured_reply()

    posts = recent_posts(limit=5)
    if not posts:
        return {
            "handled": True,
            "reply": (
                "ما قدرت أجلب المنشورات الأخيرة 📭\n"
                "Bot API لا يدعم قراءة تاريخ القناة مباشرة — "
                "هاد الخيار مش متاح بالوضع الحالي."
            ),
        }
    lines = []
    for i, p in enumerate(posts, 1):
        mid = p.get("message_id", "؟")
        txt = (p.get("text") or "")[:80]
        lines.append(f"{i}. [{mid}] {txt}")
    return {"handled": True, "reply": "📋 آخر المنشورات:\n\n" + "\n".join(lines)}


# تصدير الأدوات

SOCIAL_TG_TOOLS = [
    {
        "name": "tg_channel_post",
        "description": (
            "اقترحي نشر رسالة جديدة على قناة Telegram الرسمية لما يطلب المستخدم "
            "«انشري كذا على القناة» أو «ضيفي هاد المنشور للقناة». "
            "text = نص المنشور (مطلوب). photo_url = رابط صورة (اختياري). "
            "ما تنشري فوراً — اعرضي معاينة واطلبي تأكيداً."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "نص المنشور المراد نشره في القناة",
                },
                "photo_url": {
                    "type": "string",
                    "description": "رابط صورة ترفق مع المنشور (اختياري)",
                },
            },
            "required": ["text"],
        },
        "handler": tg_channel_post,
    },
    {
        "name": "tg_channel_confirm",
        "description": (
            "انشري المنشور المعلّق بعد ما يأكّد المستخدم (مثلاً «أكّدي النشر»، "
            "«آه انشري»، «تمام نشري»). استعمليها فقط بعد tg_channel_post."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": tg_channel_confirm,
    },
    {
        "name": "tg_channel_edit",
        "description": (
            "عدّلي رسالة موجودة في القناة لما يطلب «عدّلي الرسالة رقم كذا» "
            "أو «غيّري نص المنشور كذا». تحتاج message_id والنص الجديد."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "رقم تعريف الرسالة في القناة",
                },
                "text": {
                    "type": "string",
                    "description": "النص الجديد للرسالة",
                },
            },
            "required": ["message_id", "text"],
        },
        "handler": tg_channel_edit,
    },
    {
        "name": "tg_channel_delete",
        "description": (
            "احذفي رسالة من القناة لما يطلب «احذفي الرسالة رقم كذا» "
            "أو «امسحي المنشور كذا من القناة». تحتاج message_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "رقم تعريف الرسالة المراد حذفها",
                },
            },
            "required": ["message_id"],
        },
        "handler": tg_channel_delete,
    },
    {
        "name": "tg_channel_pin",
        "description": (
            "ثبّتي رسالة في القناة لما يطلب «ثبّتي الرسالة رقم كذا» "
            "أو «اعمليها بنر/مثبّتة». تحتاج message_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "رقم تعريف الرسالة المراد تثبيتها",
                },
            },
            "required": ["message_id"],
        },
        "handler": tg_channel_pin,
    },
    {
        "name": "tg_channel_recent",
        "description": (
            "اعرضي آخر منشورات القناة لما يسأل «شو آخر منشورات القناة؟» "
            "أو «ورجيني المنشورات الأخيرة». قد لا تكون متاحة بسبب قيود Bot API."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": tg_channel_recent,
    },
]
