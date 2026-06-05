from datetime import datetime, timezone
from typing import Any, Dict


from app.utils.nlp_normalizer import normalize_user_message
from app.utils.time import USER_TZ
from app.utils.arabic_days import resolve_day_name_to_iso
from app.agent.pending import create_pending_action

from app.features.google_calendar import (
    add_calendar_event,
    delete_sandy_reminder_by_task_id,
    load_reminders,
    parse_reminder_time_ai,
    update_calendar_event,
)
from app.utils.user_profiles import active_profile_is_owner


def handle_reminder_action(
    params: Dict[str, Any],
    *,
    user_message: str,
    normalized_user_message: str,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    if not active_profile_is_owner():
        return {"handled": True, "reply": "التذكيرات خاصة بنبيل 😊"}

    reminder_action = str(params.get("action", "create")).strip().lower()
    if reminder_action not in {
        "create",
        "list",
        "delete",
        "delete_all",
        "update",
        "snooze",
        "rename",
    }:
        return {"handled": True, "reply": "نوع إجراء التذكير غير صالح."}

    reminder_text = str(params.get("text", "")).strip()
    time_text = str(params.get("time_text", "")).strip()
    remind_at_iso = str(params.get("remind_at_iso", "")).strip()
    recurrence = str(params.get("recurrence", "")).strip()
    end_iso = str(params.get("end_iso", "")).strip()

    # If the user named a weekday ("الجمعة" etc.) with no clock time, work out the
    # date ourselves. The AI planner gets the wrong weekday too often.
    if not time_text:
        _det = resolve_day_name_to_iso(
            normalized_user_message or user_message, default_hour=9
        )
        if _det:
            print(f"[Reminder] day-name override: {_det}", flush=True)
            remind_at_iso = _det

    recurrence = recurrence.strip().upper()

    if recurrence and recurrence.startswith("FREQ="):
        recurrence = f"RRULE:{recurrence}"

    if recurrence and not recurrence.startswith("RRULE:FREQ="):
        print(f"[Reminder] ignoring invalid recurrence: {recurrence}")
        recurrence = ""

    if (
        recurrence
        and end_iso
        and "UNTIL=" not in recurrence
        and "COUNT=" not in recurrence
    ):
        try:
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            cairo_tz = USER_TZ
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=cairo_tz)
            else:
                end_dt = end_dt.astimezone(cairo_tz)
            until_utc = end_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            until_utc = ""
        if until_utc:
            recurrence = recurrence.rstrip(";") + f";UNTIL={until_utc}"

    if reminder_action == "list":
        reminders = load_reminders()
        if not reminders:
            reply = "لا توجد تذكيرات."
        else:
            lines = []
            for idx, reminder in enumerate(reminders[:10], 1):
                text = reminder.get("text", "")
                remind_at = reminder.get("remind_at", "")
                is_recurring = bool(reminder.get("is_recurring", False))

                try:
                    if remind_at:
                        dt_obj = datetime.fromisoformat(
                            remind_at.replace("Z", "+00:00")
                        )
                        if dt_obj.tzinfo is not None:
                            dt_obj = dt_obj.astimezone(USER_TZ)
                        due_text = dt_obj.strftime("%d/%m %I:%M %p")
                    else:
                        due_text = ""
                except Exception:
                    due_text = remind_at

                prefix = "متكرر — " if is_recurring else ""
                lines.append(
                    f"{idx}. {prefix}{text} @ {due_text}"
                    if due_text
                    else f"{idx}. {prefix}{text}"
                )
            reply = "\n".join(lines)

    elif reminder_action == "delete":
        target_text = reminder_text or str(params.get("text", "")).strip()
        if not target_text:
            reply = "شو اسم التذكير أو المهمة اللي بدك تحذف تذكيرها؟"
        else:
            reminders = load_reminders()
            target_lower = target_text.lower()
            matched = [
                r
                for r in reminders
                if target_lower in (r.get("text", "") or "").lower()
            ]
            if not matched:
                reply = f"ما لقيت تذكير مرتبط بـ '{target_text}'."
            else:
                deleted = 0
                for r in matched:
                    task_id = r.get("task_id", "")
                    event_id = r.get("id", "")
                    if task_id:
                        deleted += delete_sandy_reminder_by_task_id(task_id)
                    elif event_id:
                        try:
                            from app.features.google_calendar import (
                                _execute_calendar_request_with_retry,
                                _get_calendar_service,
                                calendar_id,
                            )

                            svc = _get_calendar_service()
                            _execute_calendar_request_with_retry(
                                svc.events().delete(
                                    calendarId=calendar_id(), eventId=event_id
                                ),
                                "executor_delete_reminder_event",
                            )
                            deleted += 1
                        except Exception:
                            pass
                reply = (
                    f"تمام، حذفت {deleted} تذكير مرتبط بـ '{target_text}'."
                    if deleted
                    else "ما قدرت أحذف التذكير."
                )

    elif reminder_action == "delete_all":
        session["pending_action"] = create_pending_action(
            {
                "type": "reminder",
                "action": "delete_all",
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = "متأكد بدك أحذف كل التذكيرات؟"

    elif reminder_action in {"update", "snooze", "rename"}:
        target_text = reminder_text or str(params.get("reference", "")).strip()
        if not target_text:
            return {"handled": True, "reply": "شو اسم التذكير اللي بدك تعدّله؟"}

        reminders = load_reminders()
        target_lower = target_text.lower()
        matched = [
            r for r in reminders if target_lower in (r.get("text", "") or "").lower()
        ]
        if not matched:
            return {
                "handled": True,
                "reply": f"ما لقيت تذكير مرتبط بـ '{target_text}'.",
            }
        reminder = matched[0]
        event_id = reminder.get("id", "")
        current_text = reminder.get("text", target_text)

        new_title = ""
        new_time_iso = ""
        new_recurrence = recurrence

        if reminder_action == "rename":
            new_title = (
                str(params.get("new_text", "")).strip()
                or str(params.get("text", "")).strip()
            )
            if not new_title:
                return {"handled": True, "reply": "شو الاسم الجديد للتذكير؟"}

        elif reminder_action == "snooze":
            snooze_minutes = int(params.get("snooze_minutes", 0) or 0)
            if not snooze_minutes and time_text:
                # parse "ساعة" → 60, "١٠ دقائق" → 10 etc.
                txt = time_text.lower()
                import re as _re

                m = _re.search(r"(\d+)\s*(دقيقة|دقائق|minute)", txt)
                if m:
                    snooze_minutes = int(m.group(1))
                else:
                    m = _re.search(r"(\d+)\s*(ساعة|ساعات|hour)", txt)
                    snooze_minutes = int(m.group(1)) * 60 if m else 30
                    if not m and ("ساعة" in txt or "hour" in txt):
                        snooze_minutes = 60
            if not snooze_minutes:
                snooze_minutes = 30
            current_remind = reminder.get("remind_at", "")
            try:
                base_dt = (
                    datetime.fromisoformat(
                        current_remind.replace("Z", "+00:00")
                    ).astimezone(USER_TZ)
                    if current_remind
                    else datetime.now(USER_TZ)
                )
            except Exception:
                base_dt = datetime.now(USER_TZ)
            from datetime import timedelta

            new_dt = base_dt + timedelta(minutes=snooze_minutes)
            new_time_iso = new_dt.isoformat()

        else:  # update: time and/or recurrence
            if not remind_at_iso and time_text:
                parse_source = normalize_user_message(time_text) or (
                    normalized_user_message or user_message
                )
                parsed = parse_reminder_time_ai(
                    parse_source,
                    create_chat_completion_fn=create_chat_completion_fn,
                    return_json=True,
                )
                if isinstance(parsed, dict) and parsed.get("success"):
                    remind_at_iso = parsed.get("remind_at_iso") or ""
            if not remind_at_iso and not new_recurrence:
                return {
                    "handled": True,
                    "reply": "أعطني الوقت الجديد أو التكرار الجديد للتذكير.",
                }
            if remind_at_iso:
                try:
                    new_dt = datetime.fromisoformat(
                        remind_at_iso.replace("Z", "+00:00")
                    )
                    new_dt = (
                        new_dt.replace(tzinfo=USER_TZ)
                        if new_dt.tzinfo is None
                        else new_dt.astimezone(USER_TZ)
                    )
                    if new_dt <= datetime.now(USER_TZ):
                        return {
                            "handled": True,
                            "reply": "وقت التذكير صار بالماضي، أعطني وقت لاحق.",
                        }
                    new_time_iso = new_dt.isoformat()
                except Exception:
                    return {
                        "handled": True,
                        "reply": "وقت غير صالح. أعطني الوقت بشكل أوضح.",
                    }

        result = update_calendar_event(
            event_id,
            title=new_title,
            start_iso=new_time_iso,
            description="",
        )

        if result.get("success"):
            if reminder_action == "rename":
                reply = f"✅ غيّرت اسم التذكير من '{current_text}' لـ '{new_title}'."
            elif reminder_action == "snooze":
                from datetime import timedelta

                try:
                    disp_dt = datetime.fromisoformat(new_time_iso).astimezone(USER_TZ)
                    disp = disp_dt.strftime("%I:%M %p")
                except Exception:
                    disp = new_time_iso
                reply = f"✅ أجّلت تذكير '{current_text}' — الوقت الجديد: {disp}."
            else:
                parts = []
                if new_time_iso:
                    try:
                        disp_dt = datetime.fromisoformat(new_time_iso).astimezone(
                            USER_TZ
                        )
                        parts.append(f"الوقت: {disp_dt.strftime('%I:%M %p')}")
                    except Exception:
                        pass
                if new_recurrence:
                    parts.append("التكرار محدّث")
                reply = f"✅ عدّلت تذكير '{current_text}'" + (
                    f" — {', '.join(parts)}." if parts else "."
                )
        else:
            err = result.get("error", "")
            if err == "past_datetime":
                reply = "وقت التذكير صار بالماضي، أعطني وقت لاحق."
            else:
                reply = "ما قدرت أعدّل التذكير. جرّب مرة ثانية."

    else:
        if not reminder_text:
            reply = "شو التذكير اللي بدك أسجله؟"
        else:
            if not remind_at_iso:
                parse_source = (
                    normalize_user_message(time_text)
                    if time_text
                    else (normalized_user_message or user_message)
                )
                parsed = parse_reminder_time_ai(
                    parse_source,
                    create_chat_completion_fn=create_chat_completion_fn,
                    return_json=True,
                )

                if isinstance(parsed, dict):
                    if parsed.get("success"):
                        remind_at_iso = parsed.get("remind_at_iso") or ""
                    else:
                        suggested = parsed.get("suggested_iso")
                        if suggested:
                            # Ask user to confirm suggested date
                            try:
                                sdt = datetime.fromisoformat(
                                    suggested.replace("Z", "+00:00")
                                )
                                if sdt.tzinfo is not None:
                                    sdt = sdt.astimezone(USER_TZ)
                                confirm_text = sdt.strftime("%d/%m/%Y %I:%M %p")
                            except Exception:
                                confirm_text = suggested

                            session["pending_action"] = create_pending_action(
                                {
                                    "type": "reminder",
                                    "action": "confirm_remind_at",
                                    "reminder_text": reminder_text,
                                    "suggested_iso": suggested,
                                    "confirmation_status": "pending",
                                }
                            )
                            save_session_fn(
                                session, session_file=session_file, mongo_db=mongo_db
                            )
                            return {
                                "handled": True,
                                "reply": f"ما فهمت التاريخ بدقّة. تقصد تضيف التذكير يوم {confirm_text}?",
                            }
                        # else fall through to ask for clarification below
                else:
                    # fallback: older behavior
                    remind_at_iso = parsed or ""

            if remind_at_iso:
                try:
                    remind_dt = datetime.fromisoformat(
                        remind_at_iso.replace("Z", "+00:00")
                    )
                    if remind_dt.tzinfo is None:
                        remind_dt = remind_dt.replace(tzinfo=USER_TZ)
                    else:
                        remind_dt = remind_dt.astimezone(USER_TZ)

                    if (
                        not time_text
                        and remind_dt.hour == 0
                        and remind_dt.minute == 0
                        and remind_dt.second == 0
                    ):
                        remind_dt = remind_dt.replace(
                            hour=9, minute=0, second=0, microsecond=0
                        )

                    if remind_dt <= datetime.now(USER_TZ):
                        reply = "وقت التذكير صار بالماضي، أعطني وقت لاحق."
                        return {"handled": True, "reply": reply}

                    remind_at_iso = remind_dt.isoformat()
                except Exception:
                    reply = "وقت التذكير غير صالح. اكتب التاريخ أو الوقت بشكل أوضح."
                    return {"handled": True, "reply": reply}

            if not remind_at_iso:
                # خزّن pending عشان لما المستخدم يرد بالوقت نكمّل التذكير
                # (قبل هيك كان بس يسأل "متى؟" بدون ما يحفظ → loop وضياع النص)
                session["pending_action"] = create_pending_action(
                    {
                        "type": "reminder",
                        "action": "await_remind_at",
                        "reminder_text": reminder_text,
                        "recurrence": recurrence,
                        "linked_task_id": str(params.get("linked_task_id", "")).strip(),
                    }
                )
                save_session_fn(
                    session, session_file=session_file, mongo_db=mongo_db
                )
                reply = f"متى بدك أذكرك بـ «{reminder_text}»؟"
            else:
                linked_task_id = str(params.get("linked_task_id", "")).strip()
                last_created_task_id = str(
                    session.pop("_last_created_task_id", "")
                ).strip()
                last_created_task_text = str(
                    session.pop("_last_created_task_text", "")
                ).strip()

                if (
                    not linked_task_id
                    and last_created_task_id
                    and last_created_task_text == reminder_text
                ):
                    linked_task_id = last_created_task_id

                reminder_description = f"Reminder created by Sandy: {reminder_text}"
                if linked_task_id:
                    reminder_description += f"\n[SANDY_TASK_ID:{linked_task_id}]"

                calendar_result = add_calendar_event(
                    title=reminder_text,
                    start_iso=remind_at_iso,
                    description=reminder_description,
                    reminder_minutes=0,
                    recurrence=recurrence,
                )
                reply = (
                    (
                        f"تمام، سجلت التذكير المتكرر: {reminder_text}"
                        if recurrence
                        else f"تمام، سجلت التذكير: {reminder_text}"
                    )
                    if calendar_result.get("success")
                    else "صار خطأ وأنا بضيف التذكير على Google Calendar. جرّب مرة ثانية."
                )
    return {"handled": True, "reply": reply}
