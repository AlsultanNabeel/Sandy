from datetime import datetime
from typing import Any, Dict

import app.agent.executor.deps as deps

from app.utils.nlp_normalizer import normalize_user_message
from app.utils.time import USER_TZ
from app.agent.pending import create_pending_action, clear_pending_action
from app.agent.executor.helpers import (
    _has_visible_task_note,
    _is_quick_confirmation,
    _task_choice_index,
    _task_choice_pair_indexes,
    is_cancellation,
)


def _handle_clarify_task_choice(
    user_message: str,
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
    create_chat_completion_fn,
) -> Dict[str, Any]:
    if _is_quick_confirmation(user_message):
        return {
            "handled": True,
            "reply": "اختار رقم المهمة من القائمة، مثل: الأولى أو 1.",
        }

    choices = pending.get("choices", [])
    target_action = str(pending.get("target_action", "")).strip().lower()
    pair_indexes = _task_choice_pair_indexes(user_message, len(choices))

    if pair_indexes and target_action in {"complete_one", "delete_one"}:
        selected_tasks = [
            {"id": choices[i].get("id", ""), "text": choices[i].get("text", "")}
            for i in pair_indexes
            if choices[i].get("id")
        ]
        if len(selected_tasks) == 2:
            if target_action == "complete_one":
                session["pending_action"] = create_pending_action(
                    {
                        "type": "task",
                        "action": "complete_multi",
                        "tasks": selected_tasks,
                        "confirmation_status": "pending",
                    }
                )
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                lines = "\n".join(
                    f"- {task.get('text', '')}" for task in selected_tasks
                )
                return {
                    "handled": True,
                    "reply": f"متأكد بدك أعلّم المهمتين كمكتملتين؟\n{lines}",
                }
            if target_action == "delete_one":
                session["pending_action"] = create_pending_action(
                    {
                        "type": "task",
                        "action": "delete_multi",
                        "tasks": selected_tasks,
                        "confirmation_status": "pending",
                    }
                )
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                lines = "\n".join(
                    f"- {task.get('text', '')}" for task in selected_tasks
                )
                return {"handled": True, "reply": f"متأكد بدك تحذف المهمتين؟\n{lines}"}

    index = _task_choice_index(user_message)

    if index is None or index < 0 or index >= len(choices):
        lines = "\n".join(
            f"المهمة {i}: {task.get('text', '')}" for i, task in enumerate(choices, 1)
        )
        return {
            "handled": True,
            "reply": "ما فهمت اختيارك. اختار واحدة من هاي المهام:\n" + lines,
        }

    selected = choices[index]

    if target_action == "rename":
        task_id = str(selected.get("id", "")).strip()
        old_text = str(selected.get("text", "")).strip()
        new_text = str(pending.get("new_text", "")).strip()
        if not task_id or not new_text:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل تعديل الاسم. جرّب الأمر من جديد.",
            }
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "rename",
                "task_id": task_id,
                "old_text": old_text,
                "new_text": new_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {
            "handled": True,
            "reply": f"متأكد بدك تعدّل اسم المهمة؟\nمن: {old_text}\nإلى: {new_text}",
        }

    if target_action == "delete_one":
        task_id = str(selected.get("id", "")).strip()
        task_text = str(selected.get("text", "")).strip()
        if not task_id:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {"handled": True, "reply": "ما قدرت أكمل الحذف. جرّب الأمر من جديد."}
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "delete_one",
                "task_id": task_id,
                "text": task_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": f"متأكد بدك أحذف المهمة: {task_text}؟"}

    if target_action == "complete_one":
        task_id = str(selected.get("id", "")).strip()
        task_text = str(selected.get("text", "")).strip()
        if not task_id:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل المهمة. جرّب الأمر من جديد.",
            }
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "complete",
                "task_id": task_id,
                "text": task_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {
            "handled": True,
            "reply": f"متأكد بدك أعلّم المهمة كمكتملة؟\n- {task_text}",
        }

    if target_action == "append_note":
        task_id = str(selected.get("id", "")).strip()
        task_text = str(selected.get("text", "")).strip()
        note_text = str(pending.get("note", "")).strip()
        if not task_id or not note_text:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل إضافة الملاحظة. جرّب الأمر من جديد.",
            }
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "append_note",
                "task_id": task_id,
                "text": task_text,
                "note": note_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {
            "handled": True,
            "reply": f"متأكد بدك تضيف هاي الملاحظة للمهمة؟\n- {task_text}\nالملاحظة: {note_text}",
        }

    if target_action == "replace_note":
        task_id = str(selected.get("id", "")).strip()
        task_text = str(selected.get("text", "")).strip()
        note_text = str(pending.get("note", "")).strip()
        if not task_id or not note_text:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل استبدال الملاحظة. جرّب الأمر من جديد.",
            }
        pending_note_action = "replace_note"
        reply = f"متأكد بدك تستبدل ملاحظة المهمة؟\n- {task_text}\nالملاحظة الجديدة: {note_text}"
        if not _has_visible_task_note(selected):
            pending_note_action = "append_note"
            reply = f"ما في ملاحظة قديمة أستبدلها للمهمة:\n- {task_text}\nبدك أضيف هاي الملاحظة؟\nالملاحظة: {note_text}"
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": pending_note_action,
                "task_id": task_id,
                "text": task_text,
                "note": note_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": reply}

    if target_action == "update_due_date":
        task_id = str(selected.get("id", "")).strip()
        task_text = str(selected.get("text", "")).strip()
        due_iso = str(pending.get("due_iso", "")).strip()
        new_due_text = str(pending.get("new_due_text", "")).strip()
        if str(selected.get("due_at", "")).strip():
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "هاي المهمة فيها وقت/تذكير محفوظ. تعديل تاريخ هالمهام لسا مش جاهز.",
            }
        if not task_id or not due_iso:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل تعديل التاريخ. جرّب الأمر من جديد.",
            }
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "update_due_date",
                "task_id": task_id,
                "text": task_text,
                "due_iso": due_iso,
                "new_due_text": new_due_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {
            "handled": True,
            "reply": f"متأكد بدك تعدّل تاريخ المهمة؟\n- {task_text}\nالتاريخ الجديد: {new_due_text}",
        }

    if target_action == "update_due_time":
        task_id = str(selected.get("id", "")).strip()
        task_text = str(selected.get("text", "")).strip()
        due_iso_for_update = str(pending.get("due_iso", "")).strip()
        time_source = str(pending.get("time_source", "")).strip()

        if not due_iso_for_update:
            base_date = ""
            for source_value in (selected.get("due_at", ""), selected.get("due", "")):
                source_value = str(source_value or "").strip()
                if not source_value:
                    continue
                try:
                    base_dt = datetime.fromisoformat(
                        source_value.replace("Z", "+00:00")
                    )
                    if base_dt.tzinfo is None:
                        base_dt = base_dt.replace(tzinfo=USER_TZ)
                    else:
                        base_dt = base_dt.astimezone(USER_TZ)
                    base_date = base_dt.date().isoformat()
                    break
                except Exception:
                    continue

            has_date_hint = any(
                hint in time_source.lower()
                for hint in (
                    "اليوم",
                    "بكرة",
                    "بكره",
                    "غدا",
                    "غداً",
                    "بعد",
                    "الأحد",
                    "الاحد",
                    "الاثنين",
                    "الثلاثاء",
                    "الأربعاء",
                    "الاربعاء",
                    "الخميس",
                    "الجمعة",
                    "الجمعه",
                    "السبت",
                    "today",
                    "tomorrow",
                    "next",
                    "/",
                    "-",
                )
            )

            if base_date and not has_date_hint:
                parse_source = f"تاريخ المهمة الحالي هو {base_date}. الوقت الجديد هو {time_source}."
            else:
                parse_source = time_source

            if not parse_source or create_chat_completion_fn is None:
                clear_pending_action(session)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                return {
                    "handled": True,
                    "reply": "ما قدرت أفهم الوقت الجديد. جرّب الأمر من جديد.",
                }

            due_iso_for_update = (
                deps.parse_reminder_time_ai(
                    normalize_user_message(parse_source),
                    create_chat_completion_fn=create_chat_completion_fn,
                )
                or ""
            )

        if not task_id or not due_iso_for_update:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل تعديل الوقت. جرّب الأمر من جديد.",
            }

        try:
            due_dt = datetime.fromisoformat(due_iso_for_update.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)

            if due_dt <= datetime.now(USER_TZ):
                clear_pending_action(session)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                return {
                    "handled": True,
                    "reply": "الوقت الجديد بالماضي. أعطني وقت لاحق.",
                }

            due_iso_for_update = due_dt.isoformat()
            new_due_text = due_dt.strftime("%d/%m/%Y %I:%M %p")

        except Exception:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "الوقت الجديد غير صالح. اكتب الوقت بشكل أوضح.",
            }

        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "update_due_time",
                "task_id": task_id,
                "text": task_text,
                "due_iso": due_iso_for_update,
                "new_due_text": new_due_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {
            "handled": True,
            "reply": f"متأكد بدك تعدّل وقت تذكير المهمة؟\n- {task_text}\nالوقت الجديد: {new_due_text}",
        }

    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {"handled": True, "reply": "نوع الاختيار غير مدعوم حالياً."}


def _handle_clarify_task_write(
    user_message: str,
    pending: Dict[str, Any],
) -> Dict[str, Any]:
    if _is_quick_confirmation(user_message):
        return {
            "handled": True,
            "reply": str(pending.get("repeat_reply", "اكتب التوضيح المطلوب.")).strip()
            or "اكتب التوضيح المطلوب.",
        }
    return {"handled": False}


def _handle_confirm_task_due_date(
    user_message: str,
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    if _is_quick_confirmation(user_message):
        task_text = str(pending.get("task_text", "")).strip()
        suggested_iso = str(pending.get("suggested_iso", "")).strip()
        if not task_text or not suggested_iso:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {
                "handled": True,
                "reply": "ما قدرت أكمل إضافة المهمة. جرّب من جديد.",
            }
        try:
            due_dt = datetime.fromisoformat(suggested_iso.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)
            if due_dt.hour == 0 and due_dt.minute == 0 and due_dt.second == 0:
                due_dt = due_dt.replace(hour=11, minute=0, second=0, microsecond=0)
            if due_dt <= datetime.now(USER_TZ):
                clear_pending_action(session)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                return {
                    "handled": True,
                    "reply": "موعد المهمة صار بالماضي. أعطني وقت لاحق.",
                }
            task_id = deps.add_task(
                task_text,
                due_iso=due_dt.isoformat(),
                mongo_db=mongo_db,
                tasks_file=tasks_file,
            )
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            if task_id:
                due_text = due_dt.strftime("%d/%m/%Y")
                return {
                    "handled": True,
                    "reply": f"تم التسجيل. المهمة محفوظة؛ الاستحقاق: {due_text}",
                }
            return {"handled": True, "reply": "صار خطأ وأنا بحفظ المهمة."}
        except Exception as e:
            clear_pending_action(session)
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            return {"handled": True, "reply": f"ما قدرت أكمل: {str(e)[:50]}"}
    elif is_cancellation(user_message):
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "تمام، لغيت إضافة المهمة."}
    return {"handled": False}


def _exec_task_complete(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    ok = deps.complete_task(task_id, mongo_db=mongo_db, tasks_file=tasks_file)
    if ok:
        deps.delete_sandy_reminder_by_task_id(task_id)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if ok:
        return {"handled": True, "reply": f"تمام، علّمت المهمة كمكتملة:\n- {task_text}"}
    return {"handled": True, "reply": "ما قدرت أكمل المهمة."}


def _exec_task_complete_multi(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    pending_tasks = pending.get("tasks", [])
    completed_names = []
    failed_names = []
    for task in pending_tasks:
        task_id = str(task.get("id", "")).strip()
        task_text = str(task.get("text", "")).strip()
        ok = deps.complete_task(task_id, mongo_db=mongo_db, tasks_file=tasks_file)
        if ok:
            deps.delete_sandy_reminder_by_task_id(task_id)
            completed_names.append(task_text)
        else:
            failed_names.append(task_text)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if completed_names and not failed_names:
        lines = "\n".join(f"- {name}" for name in completed_names)
        return {
            "handled": True,
            "reply": f"تمام، علّمت {len(completed_names)} مهام كمكتملة:\n{lines}",
        }
    if completed_names:
        ok_lines = "\n".join(f"- {name}" for name in completed_names)
        fail_lines = "\n".join(f"- {name}" for name in failed_names)
        return {
            "handled": True,
            "reply": f"علّمت كمكتملة:\n{ok_lines}\nوما قدرت أكمل:\n{fail_lines}",
        }
    return {"handled": True, "reply": "ما قدرت أكمل المهام المحددة."}


def _exec_task_uncomplete(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    ok = deps.uncomplete_task(task_id, mongo_db=mongo_db, tasks_file=tasks_file)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if ok:
        return {
            "handled": True,
            "reply": f"تمام، رجّعت المهمة لقائمة المهام النشطة: {task_text}",
        }
    return {"handled": True, "reply": "ما قدرت أرجّع المهمة."}


def _exec_task_rename(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    old_text = str(pending.get("old_text", "")).strip()
    new_text = str(pending.get("new_text", "")).strip()
    ok = deps.rename_task(task_id, new_text, mongo_db=mongo_db, tasks_file=tasks_file)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if ok:
        return {
            "handled": True,
            "reply": f"تمام، عدّلت اسم المهمة:\nمن: {old_text}\nإلى: {new_text}",
        }
    return {"handled": True, "reply": "ما قدرت أعدل اسم المهمة."}


def _exec_task_update_due_date(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    due_iso = str(pending.get("due_iso", "")).strip()
    new_due_text = str(pending.get("new_due_text", "")).strip()
    result = deps.update_task_due_date(
        task_id, due_iso, mongo_db=mongo_db, tasks_file=tasks_file
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if result.get("ok"):
        return {
            "handled": True,
            "reply": f"تمام، عدّلت تاريخ المهمة:\n- {task_text}\nالتاريخ الجديد: {new_due_text}",
        }
    reason = result.get("reason")
    if reason == "has_time":
        return {
            "handled": True,
            "reply": "هاي المهمة فيها وقت/تذكير محفوظ. تعديل تاريخ المهام اللي فيها وقت مؤجل للمرحلة 6.6.3.",
        }
    if reason == "past":
        return {
            "handled": True,
            "reply": "التاريخ الجديد بالماضي. أعطني تاريخ اليوم أو تاريخ لاحق.",
        }
    return {"handled": True, "reply": "ما قدرت أعدل تاريخ المهمة."}


def _exec_task_update_due_time(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    due_iso = str(pending.get("due_iso", "")).strip()
    new_due_text = str(pending.get("new_due_text", "")).strip()
    result = deps.update_task_due_time(
        task_id, due_iso, mongo_db=mongo_db, tasks_file=tasks_file
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if not result.get("ok"):
        if result.get("reason") == "past":
            return {"handled": True, "reply": "الوقت الجديد بالماضي. أعطني وقت لاحق."}
        return {"handled": True, "reply": "ما قدرت أعدل وقت المهمة."}
    deps.delete_sandy_reminder_by_task_id(task_id)
    reminder_description = (
        f"Reminder created by Sandy: {task_text}\n[SANDY_TASK_ID:{task_id}]"
    )
    calendar_result = deps.add_calendar_event(
        title=task_text,
        start_iso=due_iso,
        description=reminder_description,
        reminder_minutes=0,
    )
    if calendar_result.get("success"):
        return {
            "handled": True,
            "reply": f"تمام، عدّلت وقت تذكير المهمة:\n- {task_text}\nالوقت الجديد: {new_due_text}",
        }
    return {
        "handled": True,
        "reply": f"عدّلت وقت المهمة، بس صار خطأ وأنا بحدّث تذكير Google Calendar:\n- {task_text}",
    }


def _exec_task_append_note(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    note_text = str(pending.get("note", "")).strip()
    ok = deps.append_task_note(
        task_id, note_text, mongo_db=mongo_db, tasks_file=tasks_file
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if ok:
        return {
            "handled": True,
            "reply": f"تمام، أضفت الملاحظة على المهمة:\n- {task_text}",
        }
    return {"handled": True, "reply": "ما قدرت أضيف الملاحظة للمهمة."}


def _exec_task_replace_note(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    note_text = str(pending.get("note", "")).strip()
    ok = deps.replace_task_note(
        task_id, note_text, mongo_db=mongo_db, tasks_file=tasks_file
    )
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if ok:
        return {
            "handled": True,
            "reply": f"تمام، استبدلت ملاحظة المهمة:\n- {task_text}",
        }
    return {"handled": True, "reply": "ما قدرت أستبدل ملاحظة المهمة."}


def _exec_task_uncomplete_multi(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_items = pending.get("items", [])
    restored = []
    failed = []
    for item in task_items:
        task_id = str(item.get("id", "")).strip()
        task_text = str(item.get("text", "")).strip()
        if deps.uncomplete_task(task_id, mongo_db=mongo_db, tasks_file=tasks_file):
            restored.append(task_text)
        else:
            failed.append(task_text)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if restored and not failed:
        lines = "\n".join(f"- {text}" for text in restored)
        return {
            "handled": True,
            "reply": f"تمام، رجّعت هاي المهام لقائمة المهام النشطة:\n{lines}",
        }
    if restored and failed:
        ok_lines = "\n".join(f"- {text}" for text in restored)
        fail_lines = "\n".join(f"- {text}" for text in failed)
        return {
            "handled": True,
            "reply": f"رجّعت بعض المهام:\n{ok_lines}\n\nوما قدرت أرجّع:\n{fail_lines}",
        }
    return {"handled": True, "reply": "ما قدرت أرجّع المهام."}


def _exec_task_delete_one(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    task_id = str(pending.get("task_id", "")).strip()
    task_text = str(pending.get("text", "")).strip()
    ok = deps.delete_task(task_id, mongo_db=mongo_db, tasks_file=tasks_file)
    if ok:
        deps.delete_sandy_reminder_by_task_id(task_id)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if ok:
        return {"handled": True, "reply": f"تمام، حذفت المهمة: {task_text}"}
    return {"handled": True, "reply": "ما قدرت أحذف المهمة."}


def _exec_task_delete_multi(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    pending_tasks = pending.get("tasks", [])
    deleted_names = []
    failed_names = []
    for task in pending_tasks:
        task_id = str(task.get("id", "")).strip()
        task_text = str(task.get("text", "")).strip()
        ok = deps.delete_task(task_id, mongo_db=mongo_db, tasks_file=tasks_file)
        if ok:
            deps.delete_sandy_reminder_by_task_id(task_id)
            deleted_names.append(task_text)
        else:
            failed_names.append(task_text)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if deleted_names and not failed_names:
        lines = "\n".join(f"- {name}" for name in deleted_names)
        return {
            "handled": True,
            "reply": f"تمام، حذفت {len(deleted_names)} مهام:\n{lines}",
        }
    if deleted_names:
        ok_lines = "\n".join(f"- {name}" for name in deleted_names)
        fail_lines = "\n".join(f"- {name}" for name in failed_names)
        return {
            "handled": True,
            "reply": f"حذفت:\n{ok_lines}\nوما قدرت أحذف:\n{fail_lines}",
        }
    return {"handled": True, "reply": "ما قدرت أحذف المهام المحددة."}


def _exec_task_delete_all(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    deleted_count = deps.delete_active_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if deleted_count == 0:
        return {
            "handled": True,
            "reply": "ما في مهام نشطة للحذف. المهام المكتملة بقيت كما هي.",
        }
    return {
        "handled": True,
        "reply": f"تمام، حذفت كل المهام النشطة ({deleted_count}) وتركت المهام المكتملة.",
    }


def _exec_task_bulk_update_due_date(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    tasks_to_update = pending.get("tasks", [])
    to_due_iso = pending.get("to_due_iso", "")
    to_due_text = pending.get("to_due_text", "")
    updated = []
    failed = []
    for task_item in tasks_to_update:
        task_id = str(task_item.get("id", "")).strip()
        task_text_item = str(task_item.get("text", "")).strip()
        if not task_id or not to_due_iso:
            failed.append(task_text_item)
            continue
        result = deps.update_task_due_date(
            task_id, to_due_iso, mongo_db=mongo_db, tasks_file=tasks_file
        )
        if result.get("ok"):
            updated.append(task_text_item)
        else:
            failed.append(task_text_item)
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if updated and not failed:
        lines = "\n".join(f"- {t}" for t in updated)
        return {
            "handled": True,
            "reply": f"✅ أجّلت {len(updated)} مهام إلى {to_due_text}:\n{lines}",
        }
    if updated and failed:
        ok_lines = "\n".join(f"- {t}" for t in updated)
        fail_lines = "\n".join(f"- {t}" for t in failed)
        return {
            "handled": True,
            "reply": f"أجّلت:\n{ok_lines}\n\nوما قدرت أؤجل:\n{fail_lines}",
        }
    return {"handled": True, "reply": "ما قدرت أؤجل المهام."}
