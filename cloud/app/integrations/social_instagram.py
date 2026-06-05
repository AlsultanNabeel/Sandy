"""تكامل Instagram Graph API — نشر صور، قراءة ميديا، تعليقات، رد، حذف تعليق، ذكر.

يحتاج متغيّرين بيئة:
  • `IG_ACCESS_TOKEN`         — توكن Facebook/Page طويل الأمد مع صلاحيات
                                instagram_content_publish + instagram_manage_comments.
  • `IG_BUSINESS_ACCOUNT_ID` — معرّف حساب Instagram Business/Creator.

تعطّل آمن: لو أي متغيّر ناقص → `is_configured()` بترجّع False وكل الدوال بترجّع
خطأ واضح بدون ما تطير exception.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_BASE = "https://graph.facebook.com/v21.0"
_TIMEOUT = 20

# قاموس النشر المعلّق: chat_id → {"image_url": ..., "caption": ...}
_pending: Dict[str, Dict[str, str]] = {}


# إعداد

def _token() -> str:
    return os.getenv("IG_ACCESS_TOKEN", "").strip()


def _account_id() -> str:
    return os.getenv("IG_BUSINESS_ACCOUNT_ID", "").strip()


def is_configured() -> bool:
    """يرجّع True لو المتغيّرين البيئيين موجودَين."""
    return bool(_token() and _account_id())


def _params(**extra: Any) -> Dict[str, Any]:
    """يبني dict المعاملات مع access_token."""
    return {"access_token": _token(), **extra}


# نشر صورة (خطوتان)

def publish_photo(image_url: str, caption: str) -> Tuple[bool, str]:
    """ينشر صورة على Instagram.

    الخطوة 1: POST /{ig_user_id}/media → creation_id
    الخطوة 2: POST /{ig_user_id}/media_publish → post_id

    يرجّع (True, post_id) لو نجح، أو (False, رسالة_خطأ).
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        acct = _account_id()

        # الخطوة 1: إنشاء container
        r1 = requests.post(
            f"{_BASE}/{acct}/media",
            params=_params(image_url=image_url, caption=caption),
            timeout=_TIMEOUT,
        )
        if r1.status_code >= 300:
            logger.warning("[ig] create container failed %s: %s", r1.status_code, r1.text[:300])
            return False, f"فشل إنشاء الميديا: {r1.status_code}"
        creation_id = r1.json().get("id")
        if not creation_id:
            return False, "ما رجع creation_id من API."

        # الخطوة 2: نشر
        r2 = requests.post(
            f"{_BASE}/{acct}/media_publish",
            params=_params(creation_id=creation_id),
            timeout=_TIMEOUT,
        )
        if r2.status_code >= 300:
            logger.warning("[ig] publish failed %s: %s", r2.status_code, r2.text[:300])
            return False, f"فشل النشر: {r2.status_code}"
        post_id = r2.json().get("id", "")
        return True, post_id
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] publish_photo error: %s", e)
        return False, str(e)


# قراءة آخر منشورات

def recent_media(limit: int = 5) -> Tuple[bool, Any]:
    """يجلب آخر منشورات الحساب.

    يرجّع (True, list[dict]) أو (False, رسالة_خطأ).
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        r = requests.get(
            f"{_BASE}/{_account_id()}/media",
            params=_params(fields="id,caption,permalink,timestamp", limit=limit),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("[ig] recent_media failed %s: %s", r.status_code, r.text[:300])
            return False, f"فشل جلب المنشورات: {r.status_code}"
        return True, r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] recent_media error: %s", e)
        return False, str(e)


# تعليقات

def media_comments(media_id: str) -> Tuple[bool, Any]:
    """يجلب التعليقات على منشور معين.

    يرجّع (True, list[dict]) أو (False, رسالة_خطأ).
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        r = requests.get(
            f"{_BASE}/{media_id}/comments",
            params=_params(fields="id,text,username"),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("[ig] media_comments failed %s: %s", r.status_code, r.text[:300])
            return False, f"فشل جلب التعليقات: {r.status_code}"
        return True, r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] media_comments error: %s", e)
        return False, str(e)


def reply_comment(comment_id: str, message: str) -> Tuple[bool, str]:
    """يرد على تعليق.

    يرجّع (True, reply_id) أو (False, رسالة_خطأ).
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        r = requests.post(
            f"{_BASE}/{comment_id}/replies",
            params=_params(message=message),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("[ig] reply_comment failed %s: %s", r.status_code, r.text[:300])
            return False, f"فشل الرد: {r.status_code}"
        return True, r.json().get("id", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] reply_comment error: %s", e)
        return False, str(e)


def delete_comment(comment_id: str) -> Tuple[bool, str]:
    """يحذف تعليقًا بمعرّفه.

    يرجّع (True, "deleted") أو (False, رسالة_خطأ).
    ملاحظة: Instagram Graph API لا يدعم حذف المنشورات الأصلية — فقط التعليقات.
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        r = requests.delete(
            f"{_BASE}/{comment_id}",
            params=_params(),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("[ig] delete_comment failed %s: %s", r.status_code, r.text[:300])
            return False, f"فشل الحذف: {r.status_code}"
        return True, "deleted"
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] delete_comment error: %s", e)
        return False, str(e)


# الذكر (tags)

def tagged_media(limit: int = 10) -> Tuple[bool, Any]:
    """يجلب المنشورات التي تمّ وسم الحساب فيها (best-effort).

    يرجّع (True, list[dict]) أو (False, رسالة_خطأ).
    إذا رفض API بسبب صلاحيات ناقصة، يرجّع رسالة عربية واضحة.
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        r = requests.get(
            f"{_BASE}/{_account_id()}/tags",
            params=_params(fields="id,caption,permalink,username", limit=limit),
            timeout=_TIMEOUT,
        )
        if r.status_code == 403 or r.status_code == 400:
            logger.warning("[ig] tagged_media permission denied %s: %s", r.status_code, r.text[:200])
            return False, "غير متاح بدون صلاحيات إضافية"
        if r.status_code >= 300:
            logger.warning("[ig] tagged_media failed %s: %s", r.status_code, r.text[:300])
            return False, "غير متاح بدون صلاحيات إضافية"
        return True, r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] tagged_media error: %s", e)
        return False, str(e)


# إحصائيات (غير مكشوفة كأداة — للاستخدام الداخلي فقط)

def insights() -> Tuple[bool, Any]:
    """يجلب إحصائيات أساسية (reach + follower_count).

    يرجّع (True, dict) أو (False, رسالة_خطأ).
    """
    if not is_configured():
        return False, "Instagram غير مضبوط."
    try:
        import requests

        r = requests.get(
            f"{_BASE}/{_account_id()}/insights",
            params=_params(metric="reach,follower_count", period="day"),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("[ig] insights failed %s: %s", r.status_code, r.text[:300])
            return False, f"الإحصائيات غير متاحة ({r.status_code})"
        return True, r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ig] insights error: %s", e)
        return False, str(e)


# إدارة النشر المعلّق (confirm-before-publish)

def propose_publish(chat_id: str, image_url: str, caption: str) -> None:
    """يحفظ اقتراح نشر معلّق لهذا المحادثة."""
    _pending[chat_id] = {"image_url": image_url, "caption": caption}


def confirm_publish(chat_id: str) -> Tuple[bool, str]:
    """ينفّذ النشر المعلّق لهذه المحادثة.

    يرجّع (True, post_id) أو (False, رسالة).
    """
    proposal = _pending.pop(chat_id, None)
    if not proposal:
        return False, "ما في اقتراح نشر معلّق."
    return publish_photo(proposal["image_url"], proposal["caption"])


def cancel_pending(chat_id: str) -> bool:
    """يلغي اقتراح النشر المعلّق. يرجّع True لو كان في شي."""
    return _pending.pop(chat_id, None) is not None


def get_pending(chat_id: str) -> Optional[Dict[str, str]]:
    """يرجّع الاقتراح المعلّق أو None."""
    return _pending.get(chat_id)
