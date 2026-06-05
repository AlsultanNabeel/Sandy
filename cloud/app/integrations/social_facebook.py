"""تكامل Facebook Page — نشر منشورات، تعديل، حذف، قراءة التعليقات والإشارات، الرد.

يحتاج متغيّرين بيئة:
  • `FB_PAGE_ACCESS_TOKEN` — توكن وصول الصفحة (صلاحيات: pages_manage_posts,
                              pages_read_engagement).
  • `FB_PAGE_ID`           — معرّف صفحة Facebook.

تعطّل آمن: لو أي منهما مش مضبوط → `is_configured()` بترجّع False وكل
الدوال بترجّع رسالة واضحة بدون رمي استثناء.

قاموس _pending_posts بيخزّن العمليات المعلّقة تأكيداً (مفتاحها chat_id).
كل إدخال له "kind": "post" | "delete" إضافةً للبيانات الخاصة بالعملية.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_API = "https://graph.facebook.com/v21.0"
_TIMEOUT = 15

# عمليات معلّقة التأكيد — {chat_id: {"kind": str, ...}}
_pending_posts: Dict[str, Dict[str, Any]] = {}


# إعداد

def _token() -> str:
    return os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()


def _page_id() -> str:
    return os.getenv("FB_PAGE_ID", "").strip()


def is_configured() -> bool:
    """يرجّع True لما يكون FB_PAGE_ACCESS_TOKEN وFB_PAGE_ID مضبوطَين."""
    return bool(_token() and _page_id())


# مساعدات HTTP

def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, Any, str]:
    """GET على Graph API. يرجّع (ok, data, msg)."""
    try:
        import requests  # lazy import — requests مثبّت في المشروع

        p = {"access_token": _token(), **(params or {})}
        resp = requests.get(f"{_API}/{path}", params=p, timeout=_TIMEOUT)
        if resp.status_code >= 300:
            msg = f"[facebook] GET /{path} → {resp.status_code}: {resp.text[:200]}"
            logger.warning(msg)
            return False, None, msg
        return True, resp.json(), ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("[facebook] GET /%s error: %s", path, exc)
        return False, None, str(exc)


def _post(path: str, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Any, str]:
    """POST على Graph API. يرجّع (ok, data, msg)."""
    try:
        import requests

        payload = {"access_token": _token(), **(data or {})}
        resp = requests.post(f"{_API}/{path}", data=payload, timeout=_TIMEOUT)
        if resp.status_code >= 300:
            msg = f"[facebook] POST /{path} → {resp.status_code}: {resp.text[:200]}"
            logger.warning(msg)
            return False, None, msg
        return True, resp.json(), ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("[facebook] POST /%s error: %s", path, exc)
        return False, None, str(exc)


def _delete(path: str) -> Tuple[bool, Any, str]:
    """DELETE على Graph API. يرجّع (ok, data, msg)."""
    try:
        import requests

        params = {"access_token": _token()}
        resp = requests.delete(f"{_API}/{path}", params=params, timeout=_TIMEOUT)
        if resp.status_code >= 300:
            msg = f"[facebook] DELETE /{path} → {resp.status_code}: {resp.text[:200]}"
            logger.warning(msg)
            return False, None, msg
        return True, resp.json(), ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("[facebook] DELETE /%s error: %s", path, exc)
        return False, None, str(exc)


# عمليات معلّقة (confirm-before-post / confirm-before-delete)

def propose_post(
    chat_id: str,
    message: str,
    photo_url: Optional[str] = None,
) -> Dict[str, Any]:
    """يخزّن المنشور المقترح في الذاكرة بانتظار التأكيد.

    يرجّع dict بـ ok=True + نص المعاينة.
    """
    _pending_posts[chat_id] = {
        "kind": "post",
        "message": message,
        "photo_url": photo_url,
    }
    logger.info("[facebook] propose_post chat_id=%s photo=%s", chat_id, bool(photo_url))
    return {"ok": True, "message": message, "photo_url": photo_url}


def propose_delete(chat_id: str, post_id: str) -> None:
    """يخزّن طلب الحذف في الذاكرة بانتظار التأكيد."""
    _pending_posts[chat_id] = {"kind": "delete", "post_id": post_id}
    logger.info("[facebook] propose_delete chat_id=%s post_id=%s", chat_id, post_id)


def confirm_post(chat_id: str) -> Tuple[bool, str]:
    """ينفّذ العملية المعلّقة (نشر أو حذف) ويمسحها من الذاكرة.

    يرجّع (ok, msg).
    """
    pending = _pending_posts.pop(chat_id, None)
    if not pending:
        return False, "ما في عملية معلّقة."
    kind = pending.get("kind", "post")
    if kind == "delete":
        return _execute_delete(pending["post_id"])
    return publish_post(pending["message"], pending.get("photo_url"))


def _execute_delete(post_id: str) -> Tuple[bool, str]:
    """ينفّذ الحذف الفعلي على Graph API."""
    ok, _data, msg = _delete(post_id)
    if not ok:
        return False, msg
    logger.info("[facebook] deleted post_id=%s", post_id)
    return True, post_id


def cancel_pending(chat_id: str) -> bool:
    """يلغي العملية المعلّقة. يرجّع True لو كان في شي."""
    existed = chat_id in _pending_posts
    _pending_posts.pop(chat_id, None)
    return existed


def has_pending(chat_id: str) -> bool:
    """يرجّع True لو في عملية معلّقة لهذا chat_id."""
    return chat_id in _pending_posts


def pending_kind(chat_id: str) -> Optional[str]:
    """يرجّع نوع العملية المعلّقة ('post' | 'delete' | None)."""
    return (_pending_posts.get(chat_id) or {}).get("kind")


# دوال Graph API

def publish_post(
    message: str,
    photo_url: Optional[str] = None,
) -> Tuple[bool, str]:
    """ينشر منشوراً نصياً أو بصورة على الصفحة مباشرةً.

    يرجّع (ok, post_id_or_error_msg).
    """
    if not is_configured():
        return False, "Facebook مش مضبوط."
    pid = _page_id()
    if photo_url:
        ok, data, msg = _post(
            f"{pid}/photos",
            {"url": photo_url, "caption": message},
        )
    else:
        ok, data, msg = _post(f"{pid}/feed", {"message": message})
    if not ok:
        return False, msg
    post_id = data.get("id") or data.get("post_id") or ""
    logger.info("[facebook] published post_id=%s photo=%s", post_id, bool(photo_url))
    return True, post_id


def edit_post(post_id: str, message: str) -> Tuple[bool, str]:
    """يعدّل نص منشور موجود.

    يرجّع (ok, post_id_or_error_msg).
    """
    if not is_configured():
        return False, "Facebook مش مضبوط."
    ok, data, msg = _post(post_id, {"message": message})
    if not ok:
        return False, msg
    result_id = data.get("id") or post_id
    logger.info("[facebook] edited post_id=%s", result_id)
    return True, result_id


def delete_post(post_id: str) -> Tuple[bool, str]:
    """يحذف منشوراً من الصفحة مباشرةً (بدون تأكيد — استعمل propose_delete للتأكيد).

    يرجّع (ok, post_id_or_error_msg).
    """
    if not is_configured():
        return False, "Facebook مش مضبوط."
    return _execute_delete(post_id)


def recent_posts(limit: int = 5) -> Tuple[bool, Any, str]:
    """يجيب آخر N منشور من الصفحة.

    يرجّع (ok, list_of_posts, msg).
    """
    if not is_configured():
        return False, [], "Facebook مش مضبوط."
    ok, data, msg = _get(
        f"{_page_id()}/posts",
        {"fields": "id,message,created_time", "limit": max(1, min(limit, 25))},
    )
    if not ok:
        return False, [], msg
    return True, data.get("data", []), ""


def post_comments(post_id: str) -> Tuple[bool, Any, str]:
    """يجيب التعليقات على منشور معيّن.

    يرجّع (ok, list_of_comments, msg).
    """
    if not is_configured():
        return False, [], "Facebook مش مضبوط."
    ok, data, msg = _get(
        f"{post_id}/comments",
        {"fields": "id,message,from"},
    )
    if not ok:
        return False, [], msg
    return True, data.get("data", []), ""


def reply_comment(comment_id: str, message: str) -> Tuple[bool, str]:
    """يرد على تعليق.

    يرجّع (ok, comment_id_or_error).
    """
    if not is_configured():
        return False, "Facebook مش مضبوط."
    ok, data, msg = _post(f"{comment_id}/comments", {"message": message})
    if not ok:
        return False, msg
    return True, data.get("id") or ""


def mentions(limit: int = 10) -> Tuple[bool, Any, str]:
    """يجيب الإشارات إلى الصفحة (/{page_id}/tagged).

    يرجّع (ok, list_of_mentions, msg).
    لو الصلاحيات ناقصة بترجّع ok=False مع رسالة واضحة.
    """
    if not is_configured():
        return False, [], "Facebook مش مضبوط."
    ok, data, msg = _get(
        f"{_page_id()}/tagged",
        {
            "fields": "id,message,from,permalink_url",
            "limit": max(1, min(limit, 50)),
        },
    )
    if not ok:
        return False, [], msg
    return True, data.get("data", []), ""


def insights() -> Tuple[bool, Any, str]:
    """يجيب إحصائيات أساسية (page_impressions, page_fans).

    يرجّع (ok, data, msg). لو الصلاحيات ناقصة بترجّع ok=False مع رسالة
    واضحة بدون أرقام مزيّفة.
    غير مكشوف كأداة — للاستعمال البرمجي المباشر فقط.
    """
    if not is_configured():
        return False, {}, "Facebook مش مضبوط."
    ok, data, msg = _get(
        f"{_page_id()}/insights",
        {"metric": "page_impressions,page_fans", "period": "day"},
    )
    if not ok:
        return False, {}, msg
    return True, data.get("data", []), ""
