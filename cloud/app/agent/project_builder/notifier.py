"""Telegram notifier for Project Builder Agent.

Sends messages from the Worker dyno directly to the owner. Worker doesn't
have access to the main telebot instance, so we use a fresh client per call
(stateless — cheap, no connection pool needed for ~10 msgs/day).

All notifications are prefixed with 🔔 so the owner can distinguish them
from regular chat replies.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_NOTIF_PREFIX = "🔔 "


def _get_bot():
    """Lazy import telebot + build client. None if not configured."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    try:
        import telebot  # type: ignore
        return telebot.TeleBot(token, parse_mode=None)
    except Exception as exc:
        logger.warning("[notifier] telebot init failed: %s", exc)
        return None


def get_owner_chat_id() -> Optional[str]:
    val = (os.getenv("OWNER_CHAT_ID") or os.getenv("SANDY_USER_CHAT_ID") or "").strip()
    return val or None


def notify_owner(message: str, *, chat_id: Optional[str] = None) -> bool:
    """Send a prefixed notification to the owner (or provided chat_id)."""
    cid = chat_id or get_owner_chat_id()
    if not cid:
        logger.warning("[notifier] OWNER_CHAT_ID غير مضبوط — skip")
        return False
    bot = _get_bot()
    if bot is None:
        return False
    try:
        text = message if message.startswith(_NOTIF_PREFIX) else _NOTIF_PREFIX + message
        bot.send_message(int(cid) if str(cid).lstrip("-").isdigit() else cid, text)
        return True
    except Exception as exc:
        logger.warning("[notifier] send_message failed: %s", exc)
        return False


def notify_build_failed(*, task_id: str, branch: str, summary: str) -> bool:
    msg = (
        f"فشل الـ build على branch `{branch}`.\n"
        f"task: `{task_id}`\n"
        f"الخلاصة: {summary[:600]}\n\n"
        "بفحص السبب وأرجع أحكيلك..."
    )
    return notify_owner(msg)


def notify_plan(*, task_id: str, file_hint: str, root_cause: str, plan: str) -> bool:
    msg = (
        f"تشخيص للمشكلة (task `{task_id}`):\n\n"
        f"📍 الموقع: {file_hint or 'تحت الفحص'}\n"
        f"🔎 السبب: {root_cause[:400]}\n\n"
        f"💡 خطتي: {plan[:500]}\n\n"
        "اتفقنا؟ ردّ 'اه' للموافقة، أو اكتب البديل."
    )
    return notify_owner(msg)


def notify_project_plan(*, task_id: str, plan_md: str) -> bool:
    """SA8 Phase A: send the generated PLAN to the owner for approval.

    Telegram message limit is 4096 chars — `plan_md` should already be
    truncated by the caller. Adds a `task_id` header so the owner can match
    the plan to the task in the queue.
    """
    msg = f"🏗️ خطة جاهزة (task `{task_id}`)\n\n{plan_md[:3800]}"
    return notify_owner(msg)


def notify_project_group_done(
    *,
    task_id: str,
    group_name: str,
    files: list,
    n_done: int,
    n_total: int,
) -> bool:
    """SA8 Phase B: between-groups gate notification."""
    files_str = "، ".join(f"`{f}`" for f in files[:5]) or "—"
    if len(files) > 5:
        files_str += f" ... و{len(files) - 5} غيرها"
    msg = (
        f"📦 تم Group {n_done}/{n_total}: **{group_name}** (task `{task_id}`)\n"
        f"الملفات: {files_str}\n\n"
        f"نكمل للـ group اللي بعده؟ ردّ 'اه'، أو اكتب تعديل/توقف."
    )
    return notify_owner(msg)


def notify_project_done(*, task_id: str, pr_url: Optional[str], file_count: int, loc: int) -> bool:
    """SA8 Phase C: final completion notification."""
    parts = [
        f"🎉 خلصت المشروع (task `{task_id}`)",
        f"📁 {file_count} ملف، ~{loc} سطر",
    ]
    if pr_url:
        parts.append(f"🔗 {pr_url}")
    return notify_owner("\n".join(parts))


def notify_patched(*, task_id: str, files: list, commit_sha: str) -> bool:
    files_str = "، ".join(files[:5]) or "ملف"
    msg = (
        f"تمّ الـ patch (task `{task_id}`):\n"
        f"الملفات: {files_str}\n"
        f"commit: `{commit_sha[:7]}`\n"
        "هلق انتظر CI..."
    )
    return notify_owner(msg)


def notify_ci_result(
    *,
    task_id: str,
    state: str,
    pr_url: Optional[str] = None,
    partial_files: Optional[list] = None,
) -> bool:
    if state == "success":
        msg = (
            f"✅ نجح CI (task `{task_id}`)\n"
            f"PR جاهز للمراجعة: {pr_url or '—'}"
        )
    elif state == "failure":
        msg = (
            f"❌ فشل CI (task `{task_id}`)\n"
            "حسحاول مرة ثانية بتحليل جديد."
        )
    elif state == "no_runs":
        msg = (
            f"⚠️ ما لقيت CI runs (task `{task_id}`)\n"
            f"الـ branch محتاج مراجعة يدوية: {pr_url or '—'}"
        )
    else:
        msg = f"⌛ CI status={state} (task `{task_id}`)"

    if partial_files:
        files_str = "، ".join(partial_files[:5])
        msg += f"\n\n⚠️ تعديل جزئي — الملفات المعدّلة: {files_str}"
    return notify_owner(msg)


def notify_needs_human(*, task_id: str, reason: str, branch: str, partial_files: Optional[list] = None) -> bool:
    msg = (
        f"🛑 وقفت (task `{task_id}`)\n"
        f"السبب: {reason[:500]}\n"
        f"branch: `{branch}`\n"
    )
    if partial_files:
        msg += f"\nملفات معدّلة جزئياً: {'، '.join(partial_files[:5])}"
    msg += "\n\nمحتاج نظرة بشرية."
    return notify_owner(msg)


def notify_expired(*, task_id: str) -> bool:
    return notify_owner(
        f"⏰ task `{task_id}` انتهت مدته (24 ساعة بدون رد). أعد تفعيله لما تفضى."
    )
