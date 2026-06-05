from datetime import datetime
from typing import Any, Dict

import app.agent.executor.deps as deps

from app.utils.time import USER_TZ
from app.agent.pending import clear_pending_action
from app.agent.executor.helpers import _is_quick_confirmation, is_cancellation


def _handle_confirm_update_with_time(
    user_message: str,
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    if _is_quick_confirmation(user_message):
        event_id = str(pending.get("event_id", "")).strip()
        suggested_iso = str(pending.get("suggested_start_iso", "")).strip()
        title_display = str(pending.get("title_display", "الموعد")).strip()
        if not event_id or not suggested_iso:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل تعديل الموعد. جرّب من جديد.",
            }
        try:
            start_dt = datetime.fromisoformat(suggested_iso.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=USER_TZ)
            else:
                start_dt = start_dt.astimezone(USER_TZ)
            if start_dt <= datetime.now(USER_TZ):
                clear_pending_action(session)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                return {
                    "handled": True,
                    "reply": "الوقت الجديد بالماضي. أعطني وقت لاحق.",
                }
            result = deps.update_calendar_event(
                event_id, start_iso=start_dt.isoformat()
            )
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            if result.get("success"):
                new_time = start_dt.strftime("%d/%m/%Y %I:%M %p")
                return {
                    "handled": True,
                    "reply": f"تمام، عدّلت الموعد:\n- {title_display}\n- الوقت الجديد: {new_time}",
                }
            return {"handled": True, "reply": "صار خطأ وأنا بتحديث الموعد."}
        except Exception as e:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {"handled": True, "reply": f"ما قدرت أكمل: {str(e)[:50]}"}
    elif is_cancellation(user_message):
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "تمام، لغيت تعديل الموعد."}
    return {"handled": False}


def _exec_calendar_delete(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    title = pending.get("title", "")
    result = deps.delete_calendar_event_by_title(title)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if result.get("success"):
        return {
            "handled": True,
            "reply": f"✅ تم حذف '{result.get('title', title)}' بنجاح.",
        }
    if result.get("ambiguous"):
        matches = "\n".join(f"• {m}" for m in result.get("matches", [])[:5])
        return {
            "handled": True,
            "reply": f"لقيت أكثر من موعد مطابق لـ '{title}':\n{matches}\nاكتب الاسم بشكل أوضح.",
        }
    return {"handled": True, "reply": f"❌ ما قدرت أحذف: {result.get('error')}"}


def _exec_calendar_confirm_delete_multi(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    result = deps.delete_calendar_events_by_titles(pending.get("titles", []))
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {
        "handled": True,
        "reply": f"✅ تم حذف {result.get('deleted_count', 0)} موعد بنجاح.",
    }


def _exec_calendar_confirm_delete_range(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    result = deps.delete_calendar_events_in_range(
        pending.get("title_hint", ""),
        pending.get("start_date_iso", ""),
        pending.get("end_date_iso", ""),
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {
        "handled": True,
        "reply": f"✅ تم حذف {result.get('deleted_count', 0)} موعد في الفترة المحددة.",
    }


def _exec_calendar_confirm_update(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    event_id = pending.get("event_id", "")
    if not event_id:
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "❌ بيانات التعديل ناقصة."}
    result = deps.update_calendar_event(
        event_id=event_id,
        title=pending.get("title", ""),
        start_iso=pending.get("start_iso", ""),
        end_iso=pending.get("end_iso", ""),
        location=pending.get("location", ""),
        description=pending.get("description", ""),
        reminder_minutes=pending.get("reminder_minutes"),
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    reply = (
        "✅ تم تعديل الموعد."
        if result.get("success")
        else f"❌ ما قدرت أعدل: {result.get('error', '')}"
    )
    return {"handled": True, "reply": reply}
