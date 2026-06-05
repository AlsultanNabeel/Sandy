from datetime import datetime
from typing import Any, Dict

import app.agent.executor.deps as deps

from app.utils.time import USER_TZ
from app.utils.nlp_normalizer import normalize_user_message
from app.agent.pending import clear_pending_action
from app.agent.executor.helpers import _is_quick_confirmation, is_cancellation


def _handle_confirm_remind_at(
    user_message: str,
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    if _is_quick_confirmation(user_message):
        reminder_text = str(pending.get("reminder_text", "")).strip()
        suggested_iso = str(pending.get("suggested_iso", "")).strip()
        if not reminder_text or not suggested_iso:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل إضافة التذكير. جرّب من جديد.",
            }
        try:
            remind_dt = datetime.fromisoformat(suggested_iso.replace("Z", "+00:00"))
            if remind_dt.tzinfo is None:
                remind_dt = remind_dt.replace(tzinfo=USER_TZ)
            else:
                remind_dt = remind_dt.astimezone(USER_TZ)
            if remind_dt <= datetime.now(USER_TZ):
                clear_pending_action(session)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                return {
                    "handled": True,
                    "reply": "وقت التذكير صار بالماضي. أعطني وقت لاحق.",
                }
            reminder_description = f"Reminder created by Sandy: {reminder_text}"
            calendar_result = deps.add_calendar_event(
                title=reminder_text,
                start_iso=remind_dt.isoformat(),
                description=reminder_description,
                reminder_minutes=0,
            )
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            if calendar_result.get("success"):
                due_text = remind_dt.strftime("%d/%m/%Y %I:%M %p")
                return {
                    "handled": True,
                    "reply": f"تم التسجيل. التذكير محفوظ؛ الوقت: {due_text}",
                }
            return {
                "handled": True,
                "reply": "صار خطأ وأنا بحفظ التذكير في Google Calendar.",
            }
        except Exception as e:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {"handled": True, "reply": f"ما قدرت أكمل: {str(e)[:50]}"}
    elif is_cancellation(user_message):
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "تمام، لغيت إضافة التذكير."}
    return {"handled": False}


def _handle_await_remind_at(
    user_message: str,
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
    create_chat_completion_fn=None,
) -> Dict[str, Any]:
    """يكمّل تذكير كان ناقصه الوقت — المستخدم رد بالوقت بعد سؤال 'متى؟'."""
    if is_cancellation(user_message):
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "تمام، لغيت التذكير."}

    reminder_text = str(pending.get("reminder_text", "")).strip()
    recurrence = str(pending.get("recurrence", "")).strip()
    if not reminder_text:
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "ما عدت أذكر شو التذكير. جرّب من جديد."}

    parsed = deps.parse_reminder_time_ai(
        normalize_user_message(user_message),
        create_chat_completion_fn=create_chat_completion_fn,
        return_json=True,
    )
    remind_at_iso = ""
    if isinstance(parsed, dict):
        if parsed.get("success"):
            remind_at_iso = parsed.get("remind_at_iso") or ""
    elif parsed:
        remind_at_iso = str(parsed)

    if not remind_at_iso:
        # ما فهم الوقت — نسأل مرة ثانية بدون ما نضيّع الـ pending
        return {
            "handled": True,
            "reply": f"ما فهمت الوقت. اكتبه أوضح (مثلاً «بكرا الساعة 5») لتذكير «{reminder_text}».",
        }

    try:
        remind_dt = datetime.fromisoformat(remind_at_iso.replace("Z", "+00:00"))
        if remind_dt.tzinfo is None:
            remind_dt = remind_dt.replace(tzinfo=USER_TZ)
        else:
            remind_dt = remind_dt.astimezone(USER_TZ)
        if (
            remind_dt.hour == 0 and remind_dt.minute == 0 and remind_dt.second == 0
        ):
            remind_dt = remind_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        if remind_dt <= datetime.now(USER_TZ):
            return {
                "handled": True,
                "reply": "الوقت صار بالماضي. أعطني وقت لاحق.",
            }
    except Exception:
        return {
            "handled": True,
            "reply": "الوقت غير واضح. اكتب التاريخ أو الساعة بشكل أوضح.",
        }

    reminder_description = f"Reminder created by Sandy: {reminder_text}"
    linked_task_id = str(pending.get("linked_task_id", "")).strip()
    if linked_task_id:
        reminder_description += f"\n[SANDY_TASK_ID:{linked_task_id}]"

    calendar_result = deps.add_calendar_event(
        title=reminder_text,
        start_iso=remind_dt.isoformat(),
        description=reminder_description,
        reminder_minutes=0,
        recurrence=recurrence or None,
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if calendar_result.get("success"):
        due_text = remind_dt.strftime("%d/%m/%Y %I:%M %p")
        return {
            "handled": True,
            "reply": f"تمام، سجلت التذكير: {reminder_text} — {due_text}",
        }
    return {
        "handled": True,
        "reply": "صار خطأ وأنا بحفظ التذكير في Google Calendar. جرّب مرة ثانية.",
    }


def _exec_reminder_delete_all(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    from app.features.google_calendar import delete_all_sandy_reminders
    deleted_count = delete_all_sandy_reminders()
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {"handled": True, "reply": f"تمام، حذفت كل التذكيرات ({deleted_count})."}
