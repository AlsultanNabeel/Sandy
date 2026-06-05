"""تكامل قناة Telegram — نشر/تعديل/حذف/تثبيت رسائل.

يحتاج متغيّرين بيئة:
  • `TELEGRAM_BOT_TOKEN`    — نفس توكن البوت (يجب أن يكون أدمن في القناة).
  • `SANDY_SOCIAL_TG_CHANNEL` — معرّف القناة (@username أو رقم سالب -100…).

تعطّل آمن: لو أي متغيّر ناقص → `is_configured()` بترجّع False
والأدوات بتخبر المستخدم بدل ما ترمي خطأ.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_API_TIMEOUT = 15  # ثواني


# مساعدات بيئة

def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _channel_id() -> str:
    return os.getenv("SANDY_SOCIAL_TG_CHANNEL", "").strip()


def is_configured() -> bool:
    """يرجع True لو التوكن والقناة موجودَين."""
    return bool(_bot_token() and _channel_id())


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_bot_token()}/{method}"


# العمليات الأساسية

def post_message(
    text: str,
    photo_url: Optional[str] = None,
) -> Tuple[bool, Optional[int], str]:
    """ينشر رسالة (أو صورة مع تعليق) في القناة.

    يرجع: (نجح؟, message_id أو None, رسالة وصفية)
    """
    channel = _channel_id()
    try:
        if photo_url:
            resp = requests.post(
                _api_url("sendPhoto"),
                json={
                    "chat_id": channel,
                    "photo": photo_url,
                    "caption": text,
                    "parse_mode": "HTML",
                },
                timeout=_API_TIMEOUT,
            )
        else:
            resp = requests.post(
                _api_url("sendMessage"),
                json={
                    "chat_id": channel,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=_API_TIMEOUT,
            )
        data = resp.json()
        if data.get("ok"):
            mid = data["result"]["message_id"]
            logger.info("[social_tg] نشرت رسالة %s في %s", mid, channel)
            return True, mid, "تم النشر"
        err = data.get("description", "خطأ غير معروف")
        logger.warning("[social_tg] فشل النشر: %s", err)
        return False, None, err
    except Exception as e:  # noqa: BLE001
        logger.warning("[social_tg] post_message خطأ: %s", e)
        return False, None, str(e)


def edit_message(message_id: int, text: str) -> Tuple[bool, str]:
    """يعدّل نص رسالة موجودة في القناة."""
    channel = _channel_id()
    try:
        resp = requests.post(
            _api_url("editMessageText"),
            json={
                "chat_id": channel,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=_API_TIMEOUT,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("[social_tg] عدّلت رسالة %s", message_id)
            return True, "تم التعديل"
        err = data.get("description", "خطأ غير معروف")
        logger.warning("[social_tg] فشل التعديل: %s", err)
        return False, err
    except Exception as e:  # noqa: BLE001
        logger.warning("[social_tg] edit_message خطأ: %s", e)
        return False, str(e)


def delete_message(message_id: int) -> Tuple[bool, str]:
    """يحذف رسالة من القناة."""
    channel = _channel_id()
    try:
        resp = requests.post(
            _api_url("deleteMessage"),
            json={"chat_id": channel, "message_id": message_id},
            timeout=_API_TIMEOUT,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("[social_tg] حذفت رسالة %s", message_id)
            return True, "تم الحذف"
        err = data.get("description", "خطأ غير معروف")
        logger.warning("[social_tg] فشل الحذف: %s", err)
        return False, err
    except Exception as e:  # noqa: BLE001
        logger.warning("[social_tg] delete_message خطأ: %s", e)
        return False, str(e)


def pin_message(message_id: int) -> Tuple[bool, str]:
    """يثبّت رسالة في القناة."""
    channel = _channel_id()
    try:
        resp = requests.post(
            _api_url("pinChatMessage"),
            json={
                "chat_id": channel,
                "message_id": message_id,
                "disable_notification": False,
            },
            timeout=_API_TIMEOUT,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("[social_tg] ثبّتت رسالة %s", message_id)
            return True, "تم التثبيت"
        err = data.get("description", "خطأ غير معروف")
        logger.warning("[social_tg] فشل التثبيت: %s", err)
        return False, err
    except Exception as e:  # noqa: BLE001
        logger.warning("[social_tg] pin_message خطأ: %s", e)
        return False, str(e)


def recent_posts(limit: int = 5) -> List[Dict[str, Any]]:
    """يحاول قراءة آخر منشورات القناة.

    Bot API لا يوفّر طريقة مباشرة لقراءة تاريخ القناة، لذا بترجع قائمة فاضية
    مع ملاحظة مسجّلة. ما نعمل بيانات وهمية.
    """
    logger.info(
        "[social_tg] recent_posts: Bot API لا يدعم قراءة تاريخ القناة مباشرة — "
        "يجب استخدام Telegram Client API (Telethon/Pyrogram) للقراءة."
    )
    return []


# متجر مؤقت في الذاكرة (pending posts per chat_id)

_pending: Dict[str, Dict[str, Any]] = {}


def propose_post(
    chat_id: str,
    text: str,
    photo_url: Optional[str] = None,
) -> str:
    """يحفظ منشوراً معلّقاً للمستخدم ويرجع معاينة نصية."""
    _pending[chat_id] = {"text": text, "photo_url": photo_url}
    preview = f"📋 معاينة المنشور:\n\n{text}"
    if photo_url:
        preview += f"\n\n🖼 صورة: {photo_url}"
    return preview


def confirm_post(chat_id: str) -> Tuple[bool, Optional[int], str]:
    """ينشر المنشور المعلّق للمستخدم ويمسحه من المتجر."""
    pending = _pending.pop(chat_id, None)
    if not pending:
        return False, None, "ما في منشور معلّق"
    return post_message(pending["text"], pending.get("photo_url"))


def cancel_pending(chat_id: str) -> bool:
    """يلغي المنشور المعلّق للمستخدم."""
    if chat_id in _pending:
        _pending.pop(chat_id)
        return True
    return False


def has_pending(chat_id: str) -> bool:
    """يرجع True لو في منشور معلّق للمستخدم."""
    return chat_id in _pending
