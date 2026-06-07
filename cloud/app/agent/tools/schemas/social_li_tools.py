"""أدوات LinkedIn — نشر منشورات، حذفها، قراءة آخر المنشورات، الرد على تعليق.

سلوك النشر والحذف: ساندي تعرض معاينة/تأكيداً وتطلب موافقة قبل التنفيذ الفعلي.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)


def _chat_id(ctx: "DispatchContext") -> str:
    return str((ctx.state or {}).get("chat_id", "") or "")


# أدوات LinkedIn

def linkedin_post(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يقترح منشور LinkedIn ويطلب تأكيداً قبل النشر."""
    from app.integrations.social_linkedin import is_configured, propose_post

    if not is_configured():
        return {
            "handled": True,
            "reply": (
                "LinkedIn مش مربوط لسا.\n"
                "أضف هالمتغيّرات على Heroku:\n"
                "• `LINKEDIN_ACCESS_TOKEN` — توكن OAuth (صلاحية w_member_social)\n"
                "• `LINKEDIN_AUTHOR_URN` — URN حسابك (مثال: urn:li:person:XXXX)"
            ),
        }

    chat_id = _chat_id(ctx)
    if not chat_id:
        return {"handled": True, "reply": "ما قدرت أحدد المحادثة."}

    text = str(args.get("text") or "").strip()
    if not text:
        return {"handled": True, "reply": "شو نص المنشور؟"}

    propose_post(chat_id, text)

    preview_lines = text.splitlines()
    preview = "\n".join(preview_lines[:6])
    if len(preview_lines) > 6:
        preview += f"\n... (+{len(preview_lines) - 6} سطر)"

    return {
        "handled": True,
        "reply": (
            f"هون معاينة المنشور:\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{preview}\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            "قوليلي «أكّدي» لو بدك ينشر، أو «ألغي» لو بدك تراجع."
        ),
    }


def linkedin_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يطلب تأكيداً قبل حذف منشور LinkedIn."""
    from app.integrations.social_linkedin import is_configured, propose_delete

    if not is_configured():
        return {
            "handled": True,
            "reply": (
                "LinkedIn مش مربوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN على Heroku."
            ),
        }

    chat_id = _chat_id(ctx)
    if not chat_id:
        return {"handled": True, "reply": "ما قدرت أحدد المحادثة."}

    post_urn = str(args.get("post_urn") or "").strip()
    if not post_urn:
        return {"handled": True, "reply": "محتاجة URN المنشور اللي بدك تحذفه."}

    # ملاحظة: LinkedIn v2 لا يدعم التعديل — الحذف نهائي ولا يمكن التراجع عنه.
    propose_delete(chat_id, post_urn)

    return {
        "handled": True,
        "reply": (
            f"⚠️ بدك تحذف المنشور:\n`{post_urn}`\n\n"
            "الحذف نهائي — LinkedIn لا يدعم استعادة المنشورات.\n"
            "قوليلي «أكّدي» لتأكيد الحذف، أو «ألغي» للتراجع."
        ),
    }


def linkedin_confirm(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """ينفّذ العملية المعلّقة (نشر أو حذف) بعد تأكيد المستخدم."""
    from app.integrations.social_linkedin import (
        has_pending,
        is_configured,
        confirm_pending,
    )

    if not is_configured():
        return {
            "handled": True,
            "reply": (
                "LinkedIn مش مربوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN على Heroku."
            ),
        }

    chat_id = _chat_id(ctx)
    if not has_pending(chat_id):
        return {
            "handled": True,
            "reply": "ما في عملية معلّقة أأكّدها — استعملي linkedin_post أو linkedin_delete أول.",
        }

    _ok, msg = confirm_pending(chat_id)
    return {"handled": True, "reply": msg}


def linkedin_recent(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يجلب آخر منشورات LinkedIn."""
    from app.integrations.social_linkedin import is_configured, recent_posts

    if not is_configured():
        return {
            "handled": True,
            "reply": (
                "LinkedIn مش مربوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN على Heroku."
            ),
        }

    ok, posts, msg = recent_posts(count=10)
    if not ok:
        # msg قد تكون "غير متاح بدون صلاحيات إضافية" أو رسالة خطأ أخرى
        return {"handled": True, "reply": msg}

    if not posts:
        return {"handled": True, "reply": "ما لقيت منشورات حديثة."}

    lines = []
    for i, p in enumerate(posts, 1):
        # حاول تجيب النص من الحقول المعتادة
        content = (
            (p.get("specificContent") or {})
            .get("com.linkedin.ugc.ShareContent", {})
            .get("shareCommentary", {})
            .get("text", "")
        ) or p.get("id", "(بدون نص)")
        snippet = content[:80].replace("\n", " ")
        if len(content) > 80:
            snippet += "…"
        lines.append(f"{i}. {snippet}")

    return {"handled": True, "reply": "📋 آخر منشوراتك على LinkedIn:\n\n" + "\n".join(lines)}


def linkedin_reply(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يضيف رداً (تعليق) على منشور أو تعليق."""
    from app.integrations.social_linkedin import is_configured, reply_comment

    if not is_configured():
        return {
            "handled": True,
            "reply": (
                "LinkedIn مش مربوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN على Heroku."
            ),
        }

    post_urn = str(args.get("post_or_comment_urn") or "").strip()
    message = str(args.get("message") or "").strip()

    if not post_urn:
        return {"handled": True, "reply": "محتاجة URN المنشور أو التعليق اللي بدك ترد عليه."}
    if not message:
        return {"handled": True, "reply": "شو الرد اللي بدك أكتبه؟"}

    ok, _, msg = reply_comment(post_urn, message)
    return {"handled": True, "reply": msg}


# القائمة المُصدَّرة

LINKEDIN_TOOLS = [
    {
        "name": "linkedin_post",
        "description": (
            "اقترحي منشوراً على LinkedIn وعرضيه للمستخدم قبل النشر. "
            "استعمليها لما يقول «انشري على LinkedIn» أو «حضّري منشور LinkedIn» أو "
            "«كتبيلي بوست على LinkedIn». ما تنشري فوراً — عرضي المعاينة واطلبي تأكيداً."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "نص المنشور الكامل المراد نشره على LinkedIn",
                },
            },
            "required": ["text"],
        },
        "handler": linkedin_post,
    },
    {
        "name": "linkedin_delete",
        "description": (
            "احذفي منشوراً من LinkedIn بعد تأكيد المستخدم. "
            "استعمليها لما يقول «احذفي هالمنشور» أو «امسحي البوست». "
            "ستعرض تأكيداً قبل الحذف — الحذف نهائي ولا يمكن التراجع عنه. "
            "ملاحظة: LinkedIn v2 لا يدعم تعديل المنشور — الحذف هو الخيار الوحيد."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_urn": {
                    "type": "string",
                    "description": "URN المنشور المراد حذفه (مثال: urn:li:ugcPost:XXXX)",
                },
            },
            "required": ["post_urn"],
        },
        "handler": linkedin_delete,
    },
    {
        "name": "linkedin_confirm",
        "description": (
            "نفّذي العملية المعلّقة (نشر أو حذف) بعد ما يؤكّد المستخدم. "
            "استعمليها لما يقول «أكّدي» أو «آه» أو «موافق» بعد عرض المعاينة أو طلب الحذف. "
            "لا تستعمليها لو ما اقترحتِ عملية قبل شوي."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": linkedin_confirm,
    },
    {
        "name": "linkedin_recent",
        "description": (
            "اعرضي آخر منشورات المستخدم على LinkedIn. "
            "استعمليها لما يسأل «شو آخر منشوراتي على LinkedIn؟» أو «ورجيني مشاركاتي الأخيرة». "
            "تنبيه: LinkedIn قد يحتاج صلاحيات إضافية — سيظهر إشعار لو مش متاح."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": linkedin_recent,
    },
    {
        "name": "linkedin_reply",
        "description": (
            "ردّي على تعليق أو منشور على LinkedIn. "
            "استعمليها لما يقول «ردّي على هالتعليق» أو «اكتبي رد على هالمنشور». "
            "محتاجة URN المنشور/التعليق ونص الرد. "
            "تنبيه: LinkedIn قد يحتاج صلاحيات إضافية."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_or_comment_urn": {
                    "type": "string",
                    "description": "URN المنشور أو التعليق المراد الرد عليه (مثال: urn:li:ugcPost:XXXX)",
                },
                "message": {
                    "type": "string",
                    "description": "نص الرد",
                },
            },
            "required": ["post_or_comment_urn", "message"],
        },
        "handler": linkedin_reply,
    },
]
