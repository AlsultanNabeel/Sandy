"""أدوات Instagram — نشر صور، قراءة منشورات، تعليقات، رد، حذف تعليق، ذكر.

ساندي تستعملها لإدارة حساب Instagram Business/Creator عبر Graph API.
النشر يحتاج تأكيد صريح قبل ما يصير (confirm-before-publish).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = (
    "Instagram مش متصل لسا. ضيف متغيّرَي البيئة:\n"
    "• `IG_ACCESS_TOKEN`\n"
    "• `IG_BUSINESS_ACCOUNT_ID`"
)


def _chat_id(ctx: "DispatchContext") -> str:
    return str((ctx.state or {}).get("chat_id", "") or "")


# هاندلرات الأدوات

def _ig_post(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يقترح نشر صورة ويطلب التأكيد — ما ينشر مباشرة."""
    from app.integrations.social_instagram import is_configured, propose_publish

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    image_url = str(args.get("image_url") or "").strip()
    caption = str(args.get("caption") or "").strip()

    if not image_url:
        return {"handled": True, "reply": "عطيني رابط الصورة (URL عامّ)."}

    chat_id = _chat_id(ctx)
    propose_publish(chat_id, image_url, caption)

    preview_caption = f"\n\n📝 الكابشن:\n{caption}" if caption else "\n\n(بدون كابشن)"
    return {
        "handled": True,
        "reply": (
            f"تمام، جاهزة أنشر هاي الصورة على Instagram:\n"
            f"🖼 {image_url}"
            f"{preview_caption}\n\n"
            "قوليلي «أكّدي النشر» لمّا تكوني متأكد، أو «ألغي النشر» لو بدك تتراجع."
        ),
    }


def _ig_confirm(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """ينفّذ اقتراح النشر المعلّق بعد التأكيد."""
    from app.integrations.social_instagram import confirm_publish, is_configured

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    chat_id = _chat_id(ctx)
    ok, result = confirm_publish(chat_id)
    if ok:
        return {
            "handled": True,
            "reply": f"نُشرت الصورة على Instagram ✅\nمعرّف المنشور: `{result}`",
        }
    return {"handled": True, "reply": f"ما قدرت أنشر: {result}"}


def _ig_recent(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يجلب آخر منشورات الحساب."""
    from app.integrations.social_instagram import is_configured, recent_media

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    limit = int(args.get("limit") or 5)
    limit = max(1, min(limit, 20))
    ok, data = recent_media(limit)
    if not ok:
        return {"handled": True, "reply": f"ما قدرت أجلب المنشورات: {data}"}
    if not data:
        return {"handled": True, "reply": "ما في منشورات على الحساب لسا."}

    lines = []
    for i, item in enumerate(data, 1):
        ts = (item.get("timestamp") or "")[:10]
        caption_text = (item.get("caption") or "").strip()
        short_cap = caption_text[:60] + "…" if len(caption_text) > 60 else caption_text
        link = item.get("permalink") or ""
        media_id = item.get("id") or ""
        line = f"{i}. [{ts}] {short_cap or '(بدون كابشن)'}"
        if link:
            line += f"\n   🔗 {link}"
        if media_id:
            line += f"\n   ID: `{media_id}`"
        lines.append(line)

    return {"handled": True, "reply": "📸 آخر منشوراتك على Instagram:\n\n" + "\n\n".join(lines)}


def _ig_comments(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يجلب التعليقات على منشور بمعرّفه."""
    from app.integrations.social_instagram import is_configured, media_comments

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    media_id = str(args.get("media_id") or "").strip()
    if not media_id:
        return {"handled": True, "reply": "عطيني معرّف المنشور (media_id)."}

    ok, data = media_comments(media_id)
    if not ok:
        return {"handled": True, "reply": f"ما قدرت أجلب التعليقات: {data}"}
    if not data:
        return {"handled": True, "reply": "ما في تعليقات على هاد المنشور."}

    lines = []
    for item in data:
        username = item.get("username") or "مجهول"
        text = item.get("text") or ""
        cid = item.get("id") or ""
        lines.append(f"@{username}: {text}\n   ID: `{cid}`")

    return {
        "handled": True,
        "reply": f"💬 تعليقات المنشور `{media_id}`:\n\n" + "\n\n".join(lines),
    }


def _ig_reply(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يرد على تعليق بمعرّفه."""
    from app.integrations.social_instagram import is_configured, reply_comment

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    comment_id = str(args.get("comment_id") or "").strip()
    message = str(args.get("message") or "").strip()

    if not comment_id:
        return {"handled": True, "reply": "عطيني معرّف التعليق (comment_id)."}
    if not message:
        return {"handled": True, "reply": "شو الرد اللي بدك أكتبه؟"}

    ok, result = reply_comment(comment_id, message)
    if ok:
        return {"handled": True, "reply": f"انبعت الرد ✅ (ID: `{result}`)"}
    return {"handled": True, "reply": f"ما قدرت أرد: {result}"}


def _ig_delete_comment(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحذف تعليقًا بمعرّفه."""
    from app.integrations.social_instagram import delete_comment, is_configured

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    comment_id = str(args.get("comment_id") or "").strip()
    if not comment_id:
        return {"handled": True, "reply": "عطيني معرّف التعليق (comment_id)."}

    ok, result = delete_comment(comment_id)
    if ok:
        return {"handled": True, "reply": f"حُذف التعليق `{comment_id}` بنجاح ✅"}
    return {"handled": True, "reply": f"ما قدرت أحذف التعليق: {result}"}


def _ig_mentions(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يجلب المنشورات التي تمّ وسم الحساب فيها."""
    from app.integrations.social_instagram import is_configured, tagged_media

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    limit = int(args.get("limit") or 10)
    limit = max(1, min(limit, 50))
    ok, data = tagged_media(limit)
    if not ok:
        return {"handled": True, "reply": f"الذكر غير متاح: {data}"}
    if not data:
        return {"handled": True, "reply": "ما في منشورات موسومة حالياً."}

    lines = []
    for i, item in enumerate(data, 1):
        username = item.get("username") or "مجهول"
        caption_text = (item.get("caption") or "").strip()
        short_cap = caption_text[:60] + "…" if len(caption_text) > 60 else caption_text
        link = item.get("permalink") or ""
        mid = item.get("id") or ""
        line = f"{i}. @{username}: {short_cap or '(بدون كابشن)'}"
        if link:
            line += f"\n   🔗 {link}"
        if mid:
            line += f"\n   ID: `{mid}`"
        lines.append(line)

    return {
        "handled": True,
        "reply": "📌 منشورات وسمتك على Instagram:\n\n" + "\n\n".join(lines),
    }


def _ig_stats(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يجلب إحصائيات أساسية للحساب (reach + follower_count)."""
    from app.integrations.social_instagram import insights, is_configured

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}
    ok, data = insights()
    if not ok:
        return {"handled": True, "reply": f"الإحصائيات غير متاحة حالياً — {data}"}
    if not data:
        return {"handled": True, "reply": "ما في إحصائيات متاحة حالياً."}
    lines = []
    for metric in data:
        name = metric.get("name") or ""
        values = metric.get("values") or []
        if values:
            lines.append(f"• {name}: {values[-1].get('value', '—')}")
    if not lines:
        return {"handled": True, "reply": "ما قدرت أقرأ الأرقام — جرّب بعدين."}
    return {"handled": True, "reply": "📊 إحصائيات Instagram (آخر يوم):\n" + "\n".join(lines)}


# قائمة الأدوات المُصدَّرة

SOCIAL_IG_TOOLS = [
    {
        "name": "ig_post",
        "description": (
            "اقترحي نشر صورة على Instagram لما يطلب المستخدم «انشري هاي الصورة» أو "
            "«بوستي على إنستا» أو «نشري كابشن كذا». "
            "بتعرضي معاينة + تطلبي تأكيد («أكّدي النشر») قبل ما تنشري. "
            "image_url يجب يكون رابط عامّ للصورة. caption اختياري."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": "رابط عامّ (HTTPS) للصورة المراد نشرها",
                },
                "caption": {
                    "type": "string",
                    "description": "نص الكابشن (اختياري)",
                },
            },
            "required": ["image_url"],
        },
        "handler": _ig_post,
    },
    {
        "name": "ig_confirm",
        "description": (
            "نشري الصورة المعلّقة بعد ما يأكّد المستخدم. "
            "استعمليها لما يقول «أكّدي النشر» أو «آه انشري» أو «تمام» بعد اقتراح ig_post."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": _ig_confirm,
    },
    {
        "name": "ig_recent",
        "description": (
            "اعرضي آخر منشورات Instagram للحساب. استعمليها لما يسأل «شو آخر منشوراتي» "
            "أو «ورجيني البوستات الأخيرة». limit اختياري (افتراضي 5، أقصاه 20)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "عدد المنشورات المطلوبة (1-20، افتراضي 5)",
                },
            },
            "required": [],
        },
        "handler": _ig_recent,
    },
    {
        "name": "ig_comments",
        "description": (
            "اجلبي التعليقات على منشور بمعرّفه. استعمليها لما يسأل «شو التعليقات على المنشور» "
            "أو «ورجيني كومنتات البوست كذا». media_id تاخذيه من ig_recent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "media_id": {
                    "type": "string",
                    "description": "معرّف المنشور (من ig_recent)",
                },
            },
            "required": ["media_id"],
        },
        "handler": _ig_comments,
    },
    {
        "name": "ig_reply",
        "description": (
            "ردّي على تعليق Instagram بمعرّفه. استعمليها لما يطلب «ردّي على هاد الكومنت» "
            "أو «اكتبيلهم كذا». comment_id تاخذيه من ig_comments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {
                    "type": "string",
                    "description": "معرّف التعليق (من ig_comments)",
                },
                "message": {
                    "type": "string",
                    "description": "نص الرد",
                },
            },
            "required": ["comment_id", "message"],
        },
        "handler": _ig_reply,
    },
    {
        "name": "ig_delete_comment",
        "description": (
            "احذفي تعليقًا من منشور Instagram بمعرّفه. استعمليها لما يطلب «احذفي هاد الكومنت» "
            "أو «شيلي هاد التعليق». comment_id تاخذيه من ig_comments. "
            "ملاحظة: Instagram لا يدعم حذف المنشورات الأصلية — فقط التعليقات."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {
                    "type": "string",
                    "description": "معرّف التعليق المراد حذفه (من ig_comments)",
                },
            },
            "required": ["comment_id"],
        },
        "handler": _ig_delete_comment,
    },
    {
        "name": "ig_mentions",
        "description": (
            "اعرضي المنشورات التي وسم فيها أحد الحساب على Instagram. "
            "استعمليها لما يسأل «مين وسمني» أو «شو الذكر عندي» أو «منشورات التاق». "
            "لو API ما وفّر البيانات بسبب صلاحيات ناقصة بترجّعي رسالة «غير متاح» بدون اختلاق."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "عدد المنشورات المطلوبة (1-50، افتراضي 10)",
                },
            },
            "required": [],
        },
        "handler": _ig_mentions,
    },
    {
        "name": "ig_stats",
        "description": (
            "اعرضي إحصائيات Instagram الأساسية (reach + followers). "
            "استعمليها لما يسأل «كم متابع عندي» أو «شو إحصائيات الإنستا» أو «الستاتس». "
            "لو API ما وفّر البيانات بترجّعي رسالة «غير متاح» بدون أرقام وهمية."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": _ig_stats,
    },
]
