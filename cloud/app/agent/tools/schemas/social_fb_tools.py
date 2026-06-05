"""أدوات إدارة صفحة Facebook.

ساندي بتستعملها لنشر منشورات (مع تأكيد مسبق)، تعديلها، حذفها (مع تأكيد)،
قراءة المنشورات الأخيرة، عرض التعليقات، الرد عليها، وعرض الإشارات.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = (
    "Facebook مش مضبوط لسا — لازم تضبط FB_PAGE_ACCESS_TOKEN وFB_PAGE_ID "
    "في متغيّرات البيئة (Heroku Config Vars)."
)


def _chat_id(ctx: "DispatchContext") -> str:
    return str((ctx.state or {}).get("chat_id", "") or "")


# هاندلرز

def fb_post(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يقترح نشر منشور ويطلب تأكيداً قبل الإرسال الفعلي."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    message = str(args.get("message") or "").strip()
    photo_url = str(args.get("photo_url") or "").strip() or None

    if not message:
        return {"handled": True, "reply": "شو نص المنشور اللي بدك أنشره؟"}

    chat_id = _chat_id(ctx)
    fb.propose_post(chat_id, message, photo_url)

    preview = f"📝 *معاينة المنشور:*\n\n{message}"
    if photo_url:
        preview += f"\n\n🖼 صورة: {photo_url}"
    preview += "\n\nقوليلي «أكّدي النشر» لنشره، أو «ألغي النشر» للإلغاء."
    return {"handled": True, "reply": preview}


def fb_confirm(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """ينفّذ العملية المعلّقة (نشر أو حذف) بعد تأكيد المستخدم."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    chat_id = _chat_id(ctx)
    if not fb.has_pending(chat_id):
        return {
            "handled": True,
            "reply": "ما في عملية معلّقة — استعملي fb_post أو fb_delete الأول.",
        }

    kind = fb.pending_kind(chat_id)
    ok, result = fb.confirm_post(chat_id)
    if ok:
        if kind == "delete":
            return {"handled": True, "reply": f"انحذف المنشور ✅\nالمعرّف: `{result}`"}
        return {"handled": True, "reply": f"انتشر المنشور ✅\nالمعرّف: `{result}`"}
    logger.warning("[fb_confirm] operation failed: %s", result)
    return {"handled": True, "reply": f"ما قدرت أكمل العملية: {result}"}


def fb_cancel(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يلغي العملية المعلّقة (نشر أو حذف)."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    existed = fb.cancel_pending(_chat_id(ctx))
    if existed:
        return {"handled": True, "reply": "تمام، ألغيت العملية 👍"}
    return {"handled": True, "reply": "ما في عملية معلّقة أصلاً."}


def fb_recent(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض آخر منشورات الصفحة."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    limit = int(args.get("limit") or 5)
    ok, posts, msg = fb.recent_posts(limit=limit)
    if not ok:
        logger.warning("[fb_recent] %s", msg)
        return {"handled": True, "reply": f"ما قدرت أجيب المنشورات: {msg}"}
    if not posts:
        return {"handled": True, "reply": "ما في منشورات على الصفحة لسا."}

    lines = []
    for i, p in enumerate(posts, 1):
        text = (p.get("message") or "(بدون نص)")[:120].replace("\n", " ")
        date = (p.get("created_time") or "")[:10]
        lines.append(f"{i}. [{date}] {text}\n   ID: `{p.get('id','')}`")
    return {"handled": True, "reply": "📄 آخر منشورات الصفحة:\n\n" + "\n\n".join(lines)}


def fb_comments(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض التعليقات على منشور معيّن."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    post_id = str(args.get("post_id") or "").strip()
    if not post_id:
        return {"handled": True, "reply": "محتاج معرّف المنشور (post_id)."}

    ok, comments, msg = fb.post_comments(post_id)
    if not ok:
        logger.warning("[fb_comments] %s", msg)
        return {"handled": True, "reply": f"ما قدرت أجيب التعليقات: {msg}"}
    if not comments:
        return {"handled": True, "reply": "ما في تعليقات على هالمنشور."}

    lines = []
    for i, c in enumerate(comments, 1):
        author = (c.get("from") or {}).get("name") or "مجهول"
        text = (c.get("message") or "")[:150].replace("\n", " ")
        lines.append(f"{i}. *{author}*: {text}\n   ID: `{c.get('id','')}`")
    return {"handled": True, "reply": f"💬 التعليقات ({len(comments)}):\n\n" + "\n\n".join(lines)}


def fb_reply(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يرد على تعليق بالمعرّف."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    comment_id = str(args.get("comment_id") or "").strip()
    message = str(args.get("message") or "").strip()
    if not comment_id:
        return {"handled": True, "reply": "محتاج معرّف التعليق (comment_id)."}
    if not message:
        return {"handled": True, "reply": "شو الرد اللي بدك أكتبه؟"}

    ok, result = fb.reply_comment(comment_id, message)
    if ok:
        return {"handled": True, "reply": f"انبعت الرد ✅\nالمعرّف: `{result}`"}
    logger.warning("[fb_reply] %s", result)
    return {"handled": True, "reply": f"ما قدرت أبعت الرد: {result}"}


def fb_edit(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعدّل نص منشور موجود."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    post_id = str(args.get("post_id") or "").strip()
    message = str(args.get("message") or "").strip()
    if not post_id:
        return {"handled": True, "reply": "محتاج معرّف المنشور (post_id)."}
    if not message:
        return {"handled": True, "reply": "شو النص الجديد للمنشور؟"}

    ok, result = fb.edit_post(post_id, message)
    if ok:
        return {"handled": True, "reply": f"انعدّل المنشور ✅\nالمعرّف: `{result}`"}
    logger.warning("[fb_edit] %s", result)
    return {"handled": True, "reply": f"ما قدرت أعدّل المنشور: {result}"}


def fb_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يطلب تأكيد الحذف ثم يخزّن الطلب — التنفيذ الفعلي عبر fb_confirm."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    post_id = str(args.get("post_id") or "").strip()
    if not post_id:
        return {"handled": True, "reply": "محتاج معرّف المنشور اللي بدك تحذفه (post_id)."}

    chat_id = _chat_id(ctx)
    fb.propose_delete(chat_id, post_id)
    return {
        "handled": True,
        "reply": (
            f"متأكد بدك تحذف المنشور `{post_id}`؟\n"
            "قوليلي «أكّدي الحذف» للمتابعة، أو «ألغي» للإلغاء."
        ),
    }


def fb_mentions(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض الإشارات إلى الصفحة."""
    from app.integrations import social_facebook as fb

    if not fb.is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}

    limit = int(args.get("limit") or 10)
    ok, items, msg = fb.mentions(limit=limit)
    if not ok:
        logger.warning("[fb_mentions] %s", msg)
        # best-effort — الصلاحية ممكن تكون ناقصة
        return {
            "handled": True,
            "reply": "الإشارات غير متاحة حالياً — ممكن التوكن ما عنده الصلاحية المطلوبة.",
        }
    if not items:
        return {"handled": True, "reply": "ما في إشارات للصفحة حالياً."}

    lines = []
    for i, m in enumerate(items, 1):
        author = (m.get("from") or {}).get("name") or "مجهول"
        text = (m.get("message") or "")[:120].replace("\n", " ")
        link = m.get("permalink_url") or ""
        lines.append(f"{i}. *{author}*: {text}\n   {link}")
    return {
        "handled": True,
        "reply": f"🔔 الإشارات ({len(items)}):\n\n" + "\n\n".join(lines),
    }


def fb_stats(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض إحصائيات أساسية لصفحة Facebook (impressions + fans)."""
    from app.integrations.social_facebook import insights, is_configured

    if not is_configured():
        return {"handled": True, "reply": _NOT_CONFIGURED}
    ok, data, msg = insights()
    if not ok:
        return {"handled": True, "reply": f"الإحصائيات غير متاحة حالياً — {msg}"}
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
    return {"handled": True, "reply": "📊 إحصائيات صفحة Facebook:\n" + "\n".join(lines)}


# تصدير

FACEBOOK_TOOLS = [
    {
        "name": "fb_post",
        "description": (
            "اقترحي نشر منشور على صفحة Facebook لما يطلب «انشري على الفيسبوك» أو "
            "«شاركي هالشي على الصفحة». "
            "ما بتنشري مباشرةً — بتعرضي معاينة وبتطلبي تأكيداً. "
            "message إجباري؛ photo_url اختياري (رابط صورة عامة)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "نص المنشور"},
                "photo_url": {
                    "type": "string",
                    "description": "رابط عام للصورة (اختياري)",
                },
            },
            "required": ["message"],
        },
        "handler": fb_post,
    },
    {
        "name": "fb_confirm",
        "description": (
            "نفّذي العملية المعلّقة (نشر أو حذف) بعد ما يأكّد المستخدم "
            "(مثلاً «أكّدي النشر»، «أكّدي الحذف»، «آه اعملي»). "
            "استعمليها فقط بعد fb_post أو fb_delete."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": fb_confirm,
    },
    {
        "name": "fb_cancel",
        "description": (
            "ألغي العملية المعلّقة (نشر أو حذف) لما يقول «ألغي» أو «لا ما بدي» "
            "أو «وقّفي»."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": fb_cancel,
    },
    {
        "name": "fb_recent",
        "description": (
            "اعرضي آخر منشورات صفحة Facebook لما يسأل «شو آخر منشوراتي» أو "
            "«ورجيني المنشورات الأخيرة». limit اختياري (افتراضي 5، أقصى 25)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "عدد المنشورات (اختياري، افتراضي 5)",
                },
            },
            "required": [],
        },
        "handler": fb_recent,
    },
    {
        "name": "fb_comments",
        "description": (
            "اعرضي تعليقات منشور معيّن لما يطلب «ورجيني التعليقات على المنشور كذا» "
            "أو «شو التعليقات؟». تحتاج post_id من fb_recent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "string",
                    "description": "معرّف المنشور (من fb_recent)",
                },
            },
            "required": ["post_id"],
        },
        "handler": fb_comments,
    },
    {
        "name": "fb_reply",
        "description": (
            "ارديّ على تعليق بمعرّفه لما يطلب «ردّي على هالتعليق» أو "
            "«قوليلهم كذا». تحتاج comment_id من fb_comments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {
                    "type": "string",
                    "description": "معرّف التعليق (من fb_comments)",
                },
                "message": {"type": "string", "description": "نص الرد"},
            },
            "required": ["comment_id", "message"],
        },
        "handler": fb_reply,
    },
    {
        "name": "fb_edit",
        "description": (
            "عدّلي نص منشور موجود لما يقول «غيّري المنشور» أو «عدّلي النص». "
            "تحتاج post_id (من fb_recent) والنص الجديد."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "string",
                    "description": "معرّف المنشور (من fb_recent)",
                },
                "message": {"type": "string", "description": "النص الجديد للمنشور"},
            },
            "required": ["post_id", "message"],
        },
        "handler": fb_edit,
    },
    {
        "name": "fb_delete",
        "description": (
            "احذفي منشوراً من الصفحة لما يطلب «احذفي المنشور». "
            "بتطلبي تأكيداً قبل الحذف — العملية لا رجعة منها. "
            "تحتاج post_id (من fb_recent)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "string",
                    "description": "معرّف المنشور المراد حذفه (من fb_recent)",
                },
            },
            "required": ["post_id"],
        },
        "handler": fb_delete,
    },
    {
        "name": "fb_mentions",
        "description": (
            "اعرضي الإشارات إلى صفحتك على Facebook لما يسأل «مين أشار للصفحة» "
            "أو «شو الإشارات؟». best-effort — ممكن يحتاج صلاحيات إضافية. "
            "limit اختياري (افتراضي 10)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "عدد الإشارات (اختياري، افتراضي 10)",
                },
            },
            "required": [],
        },
        "handler": fb_mentions,
    },
    {
        "name": "fb_stats",
        "description": (
            "اعرضي إحصائيات صفحة Facebook الأساسية (impressions + fans). "
            "استعمليها لما يسأل «شو إحصائيات الصفحة» أو «كم وصول». "
            "لو الصلاحيات ناقصة بترجّعي «غير متاح» بدون أرقام وهمية."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": fb_stats,
    },
]
