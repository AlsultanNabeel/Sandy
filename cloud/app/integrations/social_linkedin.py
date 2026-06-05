"""تكامل LinkedIn — نشر منشورات + حذفها + قراءة تعليقات + الرد عليها.

يحتاج متغيّرين بيئة:
  • `LINKEDIN_ACCESS_TOKEN` — توكن OAuth بصلاحية w_member_social.
  • `LINKEDIN_AUTHOR_URN`   — URN صاحب الحساب/الصفحة
                              (مثال: "urn:li:person:XXXX" أو "urn:li:organization:XXXX").

تعطّل آمن: لو أي منهم غائب → `is_configured()` بترجّع False.

ملاحظات API:
  • LinkedIn v2 لا يدعم تعديل منشور بعد نشره — لا توجد نقطة edit endpoint.
  • قراءة التعليقات والإشارات عبر /v2/socialActions تحتاج موافقة خاصة من LinkedIn؛
    الدوال تعمل best-effort وترجّع "غير متاح بدون صلاحيات إضافية" عند 401/403.

ملاحظة نشر الصور:
  نشر صورة على LinkedIn يتطلّب خطوتين إضافيتين:
    1) طلب register-upload للحصول على uploadUrl + asset URN.
    2) رفع الصورة للـuploadUrl ثم ربط الـasset URN بالمنشور.
  publish_post() تدعم النص الآن فقط؛ دعم الصور محجوز كـ TODO أدناه.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_API = "https://api.linkedin.com/v2"
_TIMEOUT = 15


# إعداد

def _token() -> str:
    return os.getenv("LINKEDIN_ACCESS_TOKEN", "").strip()


def _author_urn() -> str:
    return os.getenv("LINKEDIN_AUTHOR_URN", "").strip()


def is_configured() -> bool:
    """يرجّع True فقط لو التوكن وURN المؤلف موجودَين."""
    return bool(_token() and _author_urn())


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


# قاموس العمليات المعلّقة (confirm-before-execute)
# مفتاح: chat_id (str) → dict يصف العملية المعلّقة
# أشكال القيمة:
#   نشر:  {"op": "post",   "text": str}
#   حذف:  {"op": "delete", "post_urn": str}
_pending: Dict[str, Dict[str, str]] = {}


# عمليات النشر المعلّق

def propose_post(chat_id: str, text: str) -> None:
    """يسجّل مسودّة منشور للـchat_id بانتظار التأكيد."""
    _pending[chat_id] = {"op": "post", "text": text}


def propose_delete(chat_id: str, post_urn: str) -> None:
    """يسجّل طلب حذف منشور للـchat_id بانتظار التأكيد."""
    _pending[chat_id] = {"op": "delete", "post_urn": post_urn}


def confirm_pending(chat_id: str) -> Tuple[bool, str]:
    """ينفّذ العملية المعلّقة (نشر أو حذف) ويمسحها. يرجّع (ok, رسالة)."""
    entry = _pending.pop(chat_id, None)
    if entry is None:
        return False, "ما في عملية معلّقة للتأكيد."

    if entry["op"] == "post":
        ok, _, msg = publish_post(entry["text"])
        return ok, msg

    if entry["op"] == "delete":
        ok, msg = delete_post(entry["post_urn"])
        return ok, msg

    return False, "نوع عملية غير معروف."


# دالة قديمة محتفظ بها للتوافق مع أي كود خارجي يستدعيها مباشرة
def confirm_post(chat_id: str) -> Tuple[bool, str]:
    """ينشر المنشور المعلّق ويمسحه. يرجّع (ok, رسالة).

    ملاحظة: confirm_pending() أشمل وتتعامل مع النشر والحذف.
    """
    entry = _pending.get(chat_id)
    if entry is None or entry.get("op") != "post":
        _pending.pop(chat_id, None)
        return False, "ما في منشور معلّق للتأكيد."
    _pending.pop(chat_id)
    ok, _, msg = publish_post(entry["text"])
    return ok, msg


def cancel_pending(chat_id: str) -> bool:
    """يلغي العملية المعلّقة لو وُجدت. يرجّع True لو كانت موجودة."""
    return _pending.pop(chat_id, None) is not None


def has_pending(chat_id: str) -> bool:
    """يرجّع True لو في عملية معلّقة لهذه المحادثة."""
    return chat_id in _pending


def pending_preview(chat_id: str) -> Optional[str]:
    """يرجّع نص المسودّة المعلّقة (للنشر) أو None."""
    entry = _pending.get(chat_id)
    if entry and entry.get("op") == "post":
        return entry.get("text")
    return None


def pending_op(chat_id: str) -> Optional[str]:
    """يرجّع نوع العملية المعلّقة ("post" أو "delete") أو None."""
    entry = _pending.get(chat_id)
    return entry.get("op") if entry else None


# عمليات API

def publish_post(text: str, image_url: Optional[str] = None) -> Tuple[bool, Optional[str], str]:
    """ينشر منشوراً نصياً على LinkedIn.

    يرجّع (ok: bool, post_urn: str | None, msg: str).

    image_url: معلّق للمستقبل — TODO: نشر الصور يتطلّب register-upload + asset URN.
      لو مرّرت image_url الآن سيُتجاهل مع تحذير في اللوغ.

    ملاحظة: LinkedIn v2 لا يدعم تعديل منشور بعد نشره.
    """
    if not is_configured():
        return False, None, "LinkedIn غير مضبوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN."

    if image_url:
        # TODO (مستقبل): نشر الصور يحتاج:
        #   POST /v2/assets?action=registerUpload  ← تطلب uploadUrl + asset URN
        #   PUT  <uploadUrl>  ← ترفع الصورة
        #   ثم تضع asset URN في shareMediaCategory="IMAGE" + media[]
        logger.warning("[linkedin] نشر الصور غير مدعوم بعد — سيُنشر النص فقط.")

    try:
        import requests

        payload: Dict[str, Any] = {
            "author": _author_urn(),
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }
        resp = requests.post(
            f"{_API}/ugcPosts",
            headers=_headers(),
            json=payload,
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.warning("[linkedin] publish failed %s: %s", resp.status_code, resp.text[:300])
            return False, None, f"فشل النشر ({resp.status_code}): {resp.text[:200]}"

        post_urn: Optional[str] = resp.headers.get("x-restli-id") or resp.json().get("id")
        return True, post_urn, "نُشر المنشور بنجاح ✅"
    except Exception as e:  # noqa: BLE001
        logger.warning("[linkedin] publish error: %s", e)
        return False, None, f"خطأ أثناء النشر: {e}"


def delete_post(post_urn: str) -> Tuple[bool, str]:
    """يحذف منشوراً من LinkedIn عبر DELETE /v2/ugcPosts/{encoded_urn}.

    يرجّع (ok: bool, msg: str).

    ملاحظة: LinkedIn v2 لا يدعم تعديل المنشور — الحذف فقط هو الخيار الممكن.
    """
    if not is_configured():
        return False, "LinkedIn غير مضبوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN."

    if not post_urn:
        return False, "post_urn مطلوب."

    try:
        import requests
        from urllib.parse import quote

        encoded_urn = quote(post_urn, safe="")
        resp = requests.delete(
            f"{_API}/ugcPosts/{encoded_urn}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            logger.info("[linkedin] delete_post: صلاحيات غير كافية %s", resp.status_code)
            return False, "غير متاح بدون صلاحيات إضافية"
        if resp.status_code >= 300:
            logger.warning("[linkedin] delete_post failed %s: %s", resp.status_code, resp.text[:200])
            return False, f"فشل الحذف ({resp.status_code}): {resp.text[:200]}"

        return True, "حُذف المنشور بنجاح ✅"
    except Exception as e:  # noqa: BLE001
        logger.warning("[linkedin] delete_post error: %s", e)
        return False, f"خطأ أثناء الحذف: {e}"


def recent_posts(count: int = 10) -> Tuple[bool, Optional[list], str]:
    """يجلب آخر منشورات المؤلف.

    تنبيه: قراءة منشورات الأعضاء عبر /v2/ugcPosts مقيّدة وتحتاج موافقة خاصة من LinkedIn.
    لو API رفض (403/401) يرجّع رسالة تفيد بعدم التوفر.
    يرجّع (ok, قائمة_أو_None, رسالة).
    """
    if not is_configured():
        return False, None, "LinkedIn غير مضبوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN."

    try:
        import requests

        params = {
            "q": "authors",
            "authors": f'List({_author_urn()})',
            "count": count,
        }
        resp = requests.get(
            f"{_API}/ugcPosts",
            headers=_headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            logger.info("[linkedin] recent_posts: صلاحيات غير كافية %s", resp.status_code)
            return False, None, "غير متاح بدون صلاحيات إضافية"
        if resp.status_code >= 300:
            logger.warning("[linkedin] recent_posts failed %s: %s", resp.status_code, resp.text[:200])
            return False, None, f"فشل الجلب ({resp.status_code})"

        elements = resp.json().get("elements", [])
        return True, elements, f"جُلب {len(elements)} منشور."
    except Exception as e:  # noqa: BLE001
        logger.warning("[linkedin] recent_posts error: %s", e)
        return False, None, f"خطأ: {e}"


def post_comments(post_urn: str) -> Tuple[bool, Optional[list], str]:
    """يجلب التعليقات على منشور معيّن.

    تنبيه: /v2/socialActions مقيّد — يحتاج موافقة LinkedIn الخاصة.
    best-effort: يرجّع "غير متاح بدون صلاحيات إضافية" عند 401/403 دون رفع استثناء.
    يرجّع (ok, قائمة_أو_None, رسالة).
    """
    if not is_configured():
        return False, None, "LinkedIn غير مضبوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN."

    if not post_urn:
        return False, None, "post_urn مطلوب."

    try:
        import requests
        from urllib.parse import quote

        encoded_urn = quote(post_urn, safe="")
        resp = requests.get(
            f"{_API}/socialActions/{encoded_urn}/comments",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            logger.info("[linkedin] post_comments: صلاحيات غير كافية %s", resp.status_code)
            return False, None, "غير متاح بدون صلاحيات إضافية"
        if resp.status_code >= 300:
            logger.warning("[linkedin] post_comments failed %s: %s", resp.status_code, resp.text[:200])
            return False, None, f"فشل الجلب ({resp.status_code})"

        elements = resp.json().get("elements", [])
        return True, elements, f"جُلب {len(elements)} تعليق."
    except Exception as e:  # noqa: BLE001
        logger.warning("[linkedin] post_comments error: %s", e)
        return False, None, f"خطأ: {e}"


def reply_comment(post_urn: str, message: str) -> Tuple[bool, Optional[str], str]:
    """يضيف تعليقاً (رداً) على منشور.

    تنبيه: /v2/socialActions مقيّد — يحتاج موافقة LinkedIn الخاصة.
    يرجّع (ok, comment_urn_أو_None, رسالة).
    """
    if not is_configured():
        return False, None, "LinkedIn غير مضبوط — أضف LINKEDIN_ACCESS_TOKEN وLINKEDIN_AUTHOR_URN."

    if not post_urn or not message:
        return False, None, "post_urn والرسالة مطلوبَين."

    try:
        import requests
        from urllib.parse import quote

        encoded_urn = quote(post_urn, safe="")
        payload: Dict[str, Any] = {
            "actor": _author_urn(),
            "message": {"text": message},
        }
        resp = requests.post(
            f"{_API}/socialActions/{encoded_urn}/comments",
            headers=_headers(),
            json=payload,
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            logger.info("[linkedin] reply_comment: صلاحيات غير كافية %s", resp.status_code)
            return False, None, "غير متاح بدون صلاحيات إضافية"
        if resp.status_code >= 300:
            logger.warning("[linkedin] reply_comment failed %s: %s", resp.status_code, resp.text[:200])
            return False, None, f"فشل الرد ({resp.status_code}): {resp.text[:200]}"

        comment_urn: Optional[str] = resp.headers.get("x-restli-id") or resp.json().get("id")
        return True, comment_urn, "نُشر الرد بنجاح ✅"
    except Exception as e:  # noqa: BLE001
        logger.warning("[linkedin] reply_comment error: %s", e)
        return False, None, f"خطأ أثناء الرد: {e}"
