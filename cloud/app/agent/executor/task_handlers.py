import re
from datetime import datetime
from typing import Any, Dict


from app.utils.nlp_normalizer import normalize_user_message
from app.utils.time import USER_TZ
from app.agent.pending import create_pending_action
from app.agent.deep_context import record_last_action
from app.agent.conflict_resolution import run_conflict_check_after_task_add

from app.features.google_calendar import (
    parse_reminder_time_ai,
)
from app.features.google_tasks import (
    add_task,
    build_task_display,
    build_completed_task_display,
    build_all_tasks_display,
    load_tasks,
    resolve_task_reference_for_write,
    resolve_completed_task_reference_for_write,
    resolve_completed_task_references_for_write,
    resolve_task_references_for_write,
    delete_completed_tasks,
    load_overdue_tasks,
    complete_all_tasks,
)
from app.agent.executor.helpers import _has_visible_task_note
from app.utils.user_profiles import active_profile_is_owner


def _task_alias_sort_key(alias: str) -> int:
    match = re.match(r"^T(\d+)$", str(alias or "").strip().upper())
    return int(match.group(1)) if match else 10**9


def _build_task_inline_markup(task_aliases: Dict[str, Any], include_add: bool = True):
    import telebot.types as tgtypes

    markup = tgtypes.InlineKeyboardMarkup()
    if include_add:
        markup.row(
            tgtypes.InlineKeyboardButton("➕ أضف مهمة", callback_data="task:add"),
        )

    for alias in sorted((task_aliases or {}).keys(), key=_task_alias_sort_key):
        task = (task_aliases or {}).get(alias, {}) or {}
        text = str(task.get("text", "") or "").strip()
        task_id = str(task.get("id", "") or "").strip()
        label = alias if not text else f"{alias}"
        reference = f"id:{task_id}" if task_id else alias
        markup.row(
            tgtypes.InlineKeyboardButton(
                f"✅ {label}", callback_data=f"task:complete:{reference}"
            ),
            tgtypes.InlineKeyboardButton(
                f"🗑️ {label}", callback_data=f"task:delete:{reference}"
            ),
        )
    return markup


def _task_callback_to_message(callback_data: str, task_aliases: Dict[str, Any]) -> str:
    parts = str(callback_data or "").split(":")
    if len(parts) < 2 or parts[0] != "task":
        return ""

    action = parts[1].strip().lower()

    if action == "add":
        return "أضف مهمة"

    if len(parts) == 4 and parts[2].strip().lower() == "id":
        task_reference = f"ID:{parts[3].strip()}"
        if action == "complete":
            return f"كملي المهمة {task_reference}"
        if action == "delete":
            return f"احذفي المهمة {task_reference}"
        return ""

    if len(parts) != 3:
        return ""

    alias = parts[2].strip().upper()

    if action == "complete":
        return f"كملي المهمة {alias}"
    if action == "delete":
        return f"احذفي المهمة {alias}"
    return ""


def _handle_list(
    task_action: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    if task_action == "list":
        reply, aliases = build_task_display(mongo_db=mongo_db, tasks_file=tasks_file)
        session["task_aliases"] = aliases
        reply_markup = _build_task_inline_markup(aliases)
    elif task_action == "list_completed":
        reply, aliases = build_completed_task_display(
            mongo_db=mongo_db, tasks_file=tasks_file
        )
        session["completed_task_aliases"] = aliases
        reply_markup = None
    else:  # list_all
        reply, active_aliases, completed_aliases = build_all_tasks_display(
            mongo_db=mongo_db, tasks_file=tasks_file
        )
        session["task_aliases"] = active_aliases
        session["completed_task_aliases"] = completed_aliases
        reply_markup = _build_task_inline_markup(active_aliases)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    result = {"handled": True, "reply": reply}
    if reply_markup is not None:
        result["reply_markup"] = reply_markup
    return result


def _handle_rename(
    task_reference: str,
    task_text: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")
    new_text = task_text.strip()

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهمة ضمن المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."
    elif status == "ambiguous":
        choices = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "rename",
                "choices": choices,
                "new_text": new_text,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )
    elif not new_text:
        reply = "شو الاسم الجديد للمهمة؟"
    elif task_obj:
        old_text = task_obj.get("text", "")
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "rename",
                "task_id": task_obj.get("id", ""),
                "old_text": old_text,
                "new_text": new_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = f"متأكد بدك تعدّل اسم المهمة؟\nمن: {old_text}\nإلى: {new_text}"
    else:
        reply = "ما قدرت أحدد المهمة."
    return {"handled": True, "reply": reply}


def _handle_update_due_date(
    task_reference: str,
    task_due_iso: str,
    task_due_text: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهمة ضمن المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."

    elif status == "ambiguous":
        due_iso_for_update = task_due_iso

        if not due_iso_for_update and task_due_text:
            parsed = parse_reminder_time_ai(
                normalize_user_message(task_due_text),
                create_chat_completion_fn=create_chat_completion_fn,
                return_json=True,
            )
            if isinstance(parsed, dict):
                if parsed.get("success"):
                    due_iso_for_update = parsed.get("remind_at_iso") or ""
                else:
                    suggested = parsed.get("suggested_iso")
                    if suggested:
                        try:
                            sdt = datetime.fromisoformat(
                                suggested.replace("Z", "+00:00")
                            )
                            if sdt.tzinfo is not None:
                                sdt = sdt.astimezone(USER_TZ)
                            confirm_text = sdt.strftime("%d/%m/%Y")
                        except Exception:
                            confirm_text = suggested
                        return {
                            "handled": True,
                            "reply": f"ما فهمت التاريخ بدقّة. تقصد اختار المهمة وعدّل التاريخ ليوم {confirm_text}?",
                        }
            else:
                due_iso_for_update = parsed or ""

        if not due_iso_for_update:
            return {
                "handled": True,
                "reply": "ما فهمت التاريخ الجديد بدقة. اكتب التاريخ بشكل أوضح.",
            }

        try:
            due_dt = datetime.fromisoformat(due_iso_for_update.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)

            if due_dt.date() < datetime.now(USER_TZ).date():
                return {
                    "handled": True,
                    "reply": "التاريخ الجديد بالماضي. أعطني تاريخ اليوم أو تاريخ لاحق.",
                }

            due_iso_for_update = due_dt.isoformat()
            new_due_text = due_dt.strftime("%d/%m/%Y")

        except Exception:
            return {
                "handled": True,
                "reply": "التاريخ الجديد غير صالح. اكتب التاريخ بشكل أوضح.",
            }

        choices = [
            {
                "id": task.get("id", ""),
                "text": task.get("text", ""),
                "due_at": task.get("due_at", ""),
            }
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]

        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "update_due_date",
                "choices": choices,
                "due_iso": due_iso_for_update,
                "new_due_text": new_due_text,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")

        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )

    elif task_obj:
        if str(task_obj.get("due_at", "")).strip():
            reply = "هاي المهمة فيها وقت/تذكير محفوظ. تعديل تاريخ المهام اللي فيها وقت مؤجل للمرحلة 6.6.3."
        else:
            due_iso_for_update = task_due_iso

            if not due_iso_for_update and task_due_text:
                parsed = parse_reminder_time_ai(
                    normalize_user_message(task_due_text),
                    create_chat_completion_fn=create_chat_completion_fn,
                    return_json=True,
                )
                if isinstance(parsed, dict):
                    if parsed.get("success"):
                        due_iso_for_update = parsed.get("remind_at_iso") or ""
                    else:
                        suggested = parsed.get("suggested_iso")
                        if suggested:
                            try:
                                sdt = datetime.fromisoformat(
                                    suggested.replace("Z", "+00:00")
                                )
                                if sdt.tzinfo is not None:
                                    sdt = sdt.astimezone(USER_TZ)
                                confirm_text = sdt.strftime("%d/%m/%Y")
                            except Exception:
                                confirm_text = suggested
                            return {
                                "handled": True,
                                "reply": f"ما فهمت التاريخ بدقّة. تقصد اختار المهمة وعدّل التاريخ ليوم {confirm_text}?",
                            }
                else:
                    due_iso_for_update = parsed or ""

            if not due_iso_for_update:
                return {
                    "handled": True,
                    "reply": "ما فهمت التاريخ الجديد بدقة. اكتب التاريخ بشكل أوضح.",
                }

            try:
                due_dt = datetime.fromisoformat(
                    due_iso_for_update.replace("Z", "+00:00")
                )
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=USER_TZ)
                else:
                    due_dt = due_dt.astimezone(USER_TZ)

                if due_dt.date() < datetime.now(USER_TZ).date():
                    return {
                        "handled": True,
                        "reply": "التاريخ الجديد بالماضي. أعطني تاريخ اليوم أو تاريخ لاحق.",
                    }

                due_iso_for_update = due_dt.isoformat()
                new_due_text = due_dt.strftime("%d/%m/%Y")

            except Exception:
                return {
                    "handled": True,
                    "reply": "التاريخ الجديد غير صالح. اكتب التاريخ بشكل أوضح.",
                }

            task_text_current = task_obj.get("text", "")

            session["pending_action"] = create_pending_action(
                {
                    "type": "task",
                    "action": "update_due_date",
                    "task_id": task_obj.get("id", ""),
                    "text": task_text_current,
                    "due_iso": due_iso_for_update,
                    "new_due_text": new_due_text,
                    "confirmation_status": "pending",
                }
            )
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

            reply = f"متأكد بدك تعدّل تاريخ المهمة؟\n- {task_text_current}\nالتاريخ الجديد: {new_due_text}"

    else:
        reply = "ما قدرت أحدد المهمة."

    return {"handled": True, "reply": reply}


def _handle_append_note(
    task_reference: str,
    task_text: str,
    task_notes: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")
    note_text = task_notes or task_text

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهمة ضمن المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."
    elif status == "ambiguous":
        choices = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "append_note",
                "choices": choices,
                "note": note_text,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )
    elif not note_text:
        reply = "شو الملاحظة اللي بدك أضيفها؟"
    elif task_obj:
        task_text_current = task_obj.get("text", "")
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "append_note",
                "task_id": task_obj.get("id", ""),
                "text": task_text_current,
                "note": note_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = f"متأكد بدك تضيف هاي الملاحظة للمهمة؟\n- {task_text_current}\nالملاحظة: {note_text}"
    else:
        reply = "ما قدرت أحدد المهمة."
    return {"handled": True, "reply": reply}


def _handle_replace_note(
    task_reference: str,
    task_text: str,
    task_notes: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")
    note_text = task_notes or task_text

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهمة ضمن المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."
    elif status == "ambiguous":
        choices = [
            {
                "id": task.get("id", ""),
                "text": task.get("text", ""),
                "notes": task.get("notes", ""),
            }
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "replace_note",
                "choices": choices,
                "note": note_text,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )
    elif not note_text:
        reply = "شو الملاحظة الجديدة؟"
    elif task_obj:
        task_text_current = task_obj.get("text", "")
        pending_note_action = "replace_note"
        reply = f"متأكد بدك تستبدل ملاحظة المهمة؟\n- {task_text_current}\nالملاحظة الجديدة: {note_text}"

        if not _has_visible_task_note(task_obj):
            pending_note_action = "append_note"
            reply = f"ما في ملاحظة قديمة أستبدلها للمهمة:\n- {task_text_current}\nبدك أضيف هاي الملاحظة؟\nالملاحظة: {note_text}"

        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": pending_note_action,
                "task_id": task_obj.get("id", ""),
                "text": task_text_current,
                "note": note_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    else:
        reply = "ما قدرت أحدد المهمة."
    return {"handled": True, "reply": reply}


def _handle_update_due_time(
    task_reference: str,
    task_due_iso: str,
    task_due_text: str,
    task_time_text: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهمة ضمن المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."

    elif status == "ambiguous":
        time_source = task_time_text or task_due_text

        if not task_due_iso and not time_source:
            return {
                "handled": True,
                "reply": "ما فهمت الوقت الجديد بدقة. اكتب الوقت بشكل أوضح.",
            }

        choices = [
            {
                "id": task.get("id", ""),
                "text": task.get("text", ""),
                "due": task.get("due", ""),
                "due_at": task.get("due_at", ""),
            }
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]

        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "update_due_time",
                "choices": choices,
                "due_iso": task_due_iso,
                "time_source": time_source,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")

        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )

    elif task_obj:
        due_iso_for_update = task_due_iso

        if not due_iso_for_update:
            base_date = ""

            for source_value in (task_obj.get("due_at", ""), task_obj.get("due", "")):
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

            time_source = task_time_text or task_due_text

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

            parsed = parse_reminder_time_ai(
                normalize_user_message(parse_source),
                create_chat_completion_fn=create_chat_completion_fn,
                return_json=True,
            )
            if isinstance(parsed, dict):
                if parsed.get("success"):
                    due_iso_for_update = parsed.get("remind_at_iso") or ""
                else:
                    suggested = parsed.get("suggested_iso")
                    if suggested:
                        try:
                            sdt = datetime.fromisoformat(
                                suggested.replace("Z", "+00:00")
                            )
                            if sdt.tzinfo is not None:
                                sdt = sdt.astimezone(USER_TZ)
                            confirm_text = sdt.strftime("%d/%m/%Y %I:%M %p")
                        except Exception:
                            confirm_text = suggested
                        return {
                            "handled": True,
                            "reply": f"ما فهمت الوقت بدقّة. تقصد تعدّل وقت المهمة ليوم {confirm_text}?",
                        }
            else:
                due_iso_for_update = parsed or ""

        if not due_iso_for_update:
            return {
                "handled": True,
                "reply": "ما فهمت الوقت الجديد بدقة. اكتب الوقت بشكل أوضح.",
            }

        try:
            due_dt = datetime.fromisoformat(due_iso_for_update.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)

            if due_dt <= datetime.now(USER_TZ):
                return {
                    "handled": True,
                    "reply": "الوقت الجديد بالماضي. أعطني وقت لاحق.",
                }

            due_iso_for_update = due_dt.isoformat()
            new_due_text = due_dt.strftime("%d/%m/%Y %I:%M %p")

        except Exception:
            return {
                "handled": True,
                "reply": "الوقت الجديد غير صالح. اكتب الوقت بشكل أوضح.",
            }

        task_text_current = task_obj.get("text", "")

        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "update_due_time",
                "task_id": task_obj.get("id", ""),
                "text": task_text_current,
                "due_iso": due_iso_for_update,
                "new_due_text": new_due_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        reply = f"متأكد بدك تعدّل وقت تذكير المهمة؟\n- {task_text_current}\nالوقت الجديد: {new_due_text}"

    else:
        reply = "ما قدرت أحدد المهمة."

    return {"handled": True, "reply": reply}


def _handle_uncomplete_multi(
    task_reference: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_completed_task_references_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("completed_task_aliases", {}),
    )
    status = result.get("status")
    tasks = result.get("tasks", [])

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهام ضمن المهام المكتملة. اعرض المهام المكتملة مرة ثانية واختر مهام موجودة."
    elif status == "ambiguous":
        lines = []
        for i, task in enumerate(result.get("matches", [])[:5], 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مكتملة مطابقة:\n"
            + "\n".join(lines)
            + "\nاكتبها بشكل أوضح."
        )
    elif status == "single":
        reply = (
            "لقيت مهمة واحدة فقط. لإلغاء اكتمال مهمة واحدة استخدم: رجّعي المهمة الأولى."
        )
    elif status in {"matched", "partial"} and tasks:
        missing_refs = result.get("missing_references", [])
        task_items = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in tasks
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "uncomplete_multi",
                "items": task_items,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = "\n".join(f"- {item['text']}" for item in task_items)
        if status == "partial":
            missing_text = "، ".join(str(ref) for ref in missing_refs)
            reply = (
                f"بعض المهام غير موجودة ضمن المكتملة: {missing_text}\n\n"
                f"لقيت هاي المهام فقط:\n{lines}\n\n"
                f"متأكد بدك أرجّعها لقائمة المهام النشطة؟"
            )
        else:
            reply = f"متأكد بدك أرجّع هاي المهام لقائمة المهام النشطة؟\n{lines}"
    else:
        reply = "ما قدرت أحدد المهام المكتملة."
    return {"handled": True, "reply": reply}


def _handle_uncomplete(
    task_reference: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_completed_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("completed_task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")

    if status in {"empty", "missing", "not_found"}:
        reply = "ما لقيت هاي المهمة ضمن المهام المكتملة. اعرض المهام المكتملة مرة ثانية واختر مهمة موجودة."
    elif status == "ambiguous":
        lines = []
        for i, task in enumerate(result.get("matches", [])[:5], 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مكتملة مطابقة:\n"
            + "\n".join(lines)
            + "\nاكتب اسم المهمة بشكل أوضح."
        )
    elif task_obj:
        task_id = task_obj.get("id", "")
        task_text = task_obj.get("text", "")
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "uncomplete",
                "task_id": task_id,
                "text": task_text,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = f"متأكد بدك أرجّع المهمة لقائمة المهام النشطة؟\n- {task_text}"
    else:
        reply = "ما قدرت أحدد المهمة المكتملة."
    return {"handled": True, "reply": reply}


def _handle_complete(
    task_reference: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_references_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    tasks = result.get("tasks", [])

    if status in {"empty", "missing", "not_found"}:
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_write",
                "target_action": "complete_multi",
                "missing": "reference",
                "reference": "",
                "value_key": "",
                "value": "",
                "repeat_reply": "أي مهمة بدك أعلّمها كمكتملة؟ احكي اسمها أو اطلب قائمة المهام.",
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = "أي مهمة بدك أعلّمها كمكتملة؟ احكي اسمها أو اطلب قائمة المهام."
    elif status == "ambiguous":
        choices = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "complete_one",
                "choices": choices,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )
    elif status == "single":
        task = tasks[0] if tasks else {}
        task_id = str(task.get("id", "")).strip()
        task_text = str(task.get("text", "")).strip()
        if not task_id:
            reply = "حددت المهمة، بس ما قدرت أجيب معرفها."
        else:
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
            reply = f"متأكد بدك أعلّم المهمة كمكتملة؟\n- {task_text}"
    else:
        reply = "ما قدرت أحدد المهمة."
    return {"handled": True, "reply": reply}


def _handle_complete_multi(
    task_reference: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_references_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    tasks = result.get("tasks", [])

    if status in {"empty", "missing", "not_found"}:
        bad_ref = str(result.get("reference", "")).strip()
        if bad_ref:
            reply = f"المهمة ({bad_ref}) غير موجودة حالياً في قائمة المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."
        else:
            reply = "أي مهمة بدك أعلّمها كمكتملة؟"
    elif status == "single":
        task = tasks[0] if tasks else {}
        task_text = str(task.get("text", "")).strip()
        if task.get("id"):
            session["pending_action"] = create_pending_action(
                {
                    "type": "task",
                    "action": "complete",
                    "task_id": task.get("id", ""),
                    "text": task_text,
                    "confirmation_status": "pending",
                }
            )
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            reply = f"لقيت مهمة واحدة فقط:\n- {task_text}\nبدك أعلّمها كمكتملة؟"
        else:
            reply = "حددت مهمة واحدة فقط، بس ما قدرت أجيب معرف المهمة."
    elif status == "ambiguous":
        choices = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "complete_one",
                "choices": choices,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )
    elif status == "partial":
        missing_refs = result.get("missing_references", [])
        missing_text = "، ".join(str(ref) for ref in missing_refs)
        pending_tasks = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in tasks
            if task.get("id")
        ]
        if pending_tasks:
            session["pending_action"] = create_pending_action(
                {
                    "type": "task",
                    "action": "complete_multi",
                    "tasks": pending_tasks,
                    "confirmation_status": "pending",
                }
            )
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            lines = "\n".join(f"- {task.get('text', '')}" for task in pending_tasks)
            reply = (
                f"المهام التالية غير موجودة: {missing_text}\n"
                f"لقيت المهام التالية فقط:\n"
                f"{lines}\n"
                f"بدك أعلّم الموجود منها كمكتمل؟"
            )
        else:
            reply = f"المهام التالية غير موجودة حالياً: {missing_text}"
    elif tasks:
        pending_tasks = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in tasks
            if task.get("id")
        ]
        lines = "\n".join(f"- {task.get('text', '')}" for task in pending_tasks)
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "complete_multi",
                "tasks": pending_tasks,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = f"متأكد بدك أعلّم المهام التالية كمكتملة؟\n{lines}"
    else:
        reply = "ما قدرت أحدد المهام."
    return {"handled": True, "reply": reply}


def _handle_delete(
    task_reference: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_reference_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    task_obj = result.get("task")

    if status in {"empty", "missing", "not_found"}:
        reply = "أي مهمة بدك أحذف بالضبط؟ اطلب قائمة المهام أو اكتب اسمها."
    elif status == "ambiguous":
        choices = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in result.get("matches", [])[:5]
            if task.get("id")
        ]
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "clarify_task_choice",
                "target_action": "delete_one",
                "choices": choices,
                "confirmation_status": "clarification",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        lines = []
        for i, task in enumerate(choices, 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاختار واحدة: الأولى، الثانية، أو رقم المهمة."
        )
    elif task_obj:
        task_id = task_obj.get("id", "")
        task_text = task_obj.get("text", "")
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
        reply = f"متأكد بدك أحذف المهمة: {task_text}؟"
    else:
        reply = "ما قدرت أحدد المهمة."
    return {"handled": True, "reply": reply}


def _handle_delete_multi(
    task_reference: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
) -> Dict[str, Any]:
    result = resolve_task_references_for_write(
        task_reference,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        aliases=session.get("task_aliases", {}),
    )
    status = result.get("status")
    tasks = result.get("tasks", [])

    if status in {"empty", "missing", "not_found"}:
        bad_ref = str(result.get("reference", "")).strip()
        if bad_ref:
            reply = f"المهمة رقم/مرجع ({bad_ref}) غير موجودة حالياً في قائمة المهام النشطة. اعرض المهام مرة ثانية واختر مهمة موجودة."
        else:
            reply = "أي مهام بدك أحذف بالضبط؟"
    elif status == "partial":
        missing_refs = result.get("missing_references", [])
        missing_text = "، ".join(str(ref) for ref in missing_refs)
        pending_tasks = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in tasks
            if task.get("id")
        ]
        if len(pending_tasks) == 1:
            task = pending_tasks[0]
            session["pending_action"] = create_pending_action(
                {
                    "type": "task",
                    "action": "delete_one",
                    "task_id": task.get("id", ""),
                    "text": task.get("text", ""),
                    "confirmation_status": "pending",
                }
            )
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            reply = (
                f"المهمة ({missing_text}) غير موجودة حالياً.\n"
                f"لقيت مهمة واحدة فقط:\n"
                f"- {task.get('text', '')}\n"
                f"بدك أحذفها؟"
            )
        elif len(pending_tasks) > 1:
            session["pending_action"] = create_pending_action(
                {
                    "type": "task",
                    "action": "delete_multi",
                    "tasks": pending_tasks,
                    "confirmation_status": "pending",
                }
            )
            save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
            lines = "\n".join(f"- {task.get('text', '')}" for task in pending_tasks)
            reply = (
                f"المهام التالية غير موجودة: {missing_text}\n"
                f"لقيت المهام التالية فقط:\n"
                f"{lines}\n"
                f"بدك أحذف الموجود منها؟"
            )
        else:
            reply = f"المهام التالية غير موجودة حالياً: {missing_text}"
    elif status == "single":
        reply = "حددت مهمة واحدة فقط. إذا بدك حذف مهمة واحدة استخدم أمر حذف عادي، أو اذكر أكثر من مهمة."
    elif status == "ambiguous":
        lines = []
        for i, task in enumerate(result.get("matches", [])[:5], 1):
            lines.append(f"المهمة {i}: {task.get('text', '')}")
        reply = (
            "لقيت أكثر من مهمة مطابقة:\n"
            + "\n".join(lines)
            + "\nاكتب الأسماء بشكل أوضح."
        )
    elif tasks:
        pending_tasks = [
            {"id": task.get("id", ""), "text": task.get("text", "")}
            for task in tasks
            if task.get("id")
        ]
        lines = "\n".join(f"- {task.get('text', '')}" for task in pending_tasks)
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "delete_multi",
                "tasks": pending_tasks,
                "confirmation_status": "pending",
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        reply = f"متأكد بدك أحذف المهام التالية؟\n{lines}"
    else:
        reply = "ما قدرت أحدد المهام."
    return {"handled": True, "reply": reply}


def _handle_delete_all(
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    session["pending_action"] = create_pending_action(
        {
            "type": "task",
            "action": "delete_all",
            "confirmation_status": "pending",
        }
    )
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {"handled": True, "reply": "متأكد بدك أحذف كل المهام؟"}


def _handle_bulk_update_due_date(
    params: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    from_due_text = str(params.get("from_due_text", "")).strip()
    to_due_text = str(params.get("to_due_text", "")).strip()

    if not from_due_text or not to_due_text:
        return {"handled": True, "reply": "مش واضح: من أي تاريخ وإلى أي تاريخ؟"}

    parsed_to = parse_reminder_time_ai(
        normalize_user_message(to_due_text),
        create_chat_completion_fn=create_chat_completion_fn,
        return_json=True,
    )
    if isinstance(parsed_to, dict):
        if parsed_to.get("success"):
            to_due_iso = parsed_to.get("remind_at_iso") or ""
        else:
            suggested = parsed_to.get("suggested_iso")
            if suggested:
                try:
                    sdt = datetime.fromisoformat(suggested.replace("Z", "+00:00"))
                    if sdt.tzinfo is not None:
                        sdt = sdt.astimezone(USER_TZ)
                    confirm_text = sdt.strftime("%d/%m/%Y")
                except Exception:
                    confirm_text = suggested
                return {
                    "handled": True,
                    "reply": f"ما فهمت التاريخ الجديد. تقصد تؤجّل المهام ليوم {confirm_text}?",
                }
            else:
                return {
                    "handled": True,
                    "reply": f"ما فهمت التاريخ الجديد: '{to_due_text}'. حدد التاريخ بوضوح.",
                }
    else:
        to_due_iso = parsed_to or ""

    if not to_due_iso:
        return {
            "handled": True,
            "reply": f"ما فهمت التاريخ الجديد: '{to_due_text}'. حدد التاريخ بوضوح.",
        }

    try:
        to_dt = datetime.fromisoformat(to_due_iso.replace("Z", "+00:00"))
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=USER_TZ)
        else:
            to_dt = to_dt.astimezone(USER_TZ)
        if to_dt.date() < datetime.now(USER_TZ).date():
            return {
                "handled": True,
                "reply": "التاريخ الجديد في الماضي. أعطني تاريخ اليوم أو لاحق.",
            }
        to_due_iso = to_dt.isoformat()
        to_due_display = to_dt.strftime("%d/%m/%Y")
    except Exception:
        return {"handled": True, "reply": "التاريخ الجديد غير صالح."}

    parsed_from = parse_reminder_time_ai(
        normalize_user_message(from_due_text),
        create_chat_completion_fn=create_chat_completion_fn,
        return_json=True,
    )
    if isinstance(parsed_from, dict):
        if parsed_from.get("success"):
            from_due_iso = parsed_from.get("remind_at_iso") or ""
        else:
            suggested = parsed_from.get("suggested_iso")
            if suggested:
                try:
                    sdt = datetime.fromisoformat(suggested.replace("Z", "+00:00"))
                    if sdt.tzinfo is not None:
                        sdt = sdt.astimezone(USER_TZ)
                    confirm_text = sdt.strftime("%d/%m/%Y")
                except Exception:
                    confirm_text = suggested
                return {
                    "handled": True,
                    "reply": f"ما فهمت تاريخ البحث. تقصد البحث عن مهام من تاريخ {confirm_text}?",
                }
            else:
                return {
                    "handled": True,
                    "reply": f"ما فهمت تاريخ البحث: '{from_due_text}'. حدد التاريخ بوضوح.",
                }
    else:
        from_due_iso = parsed_from or ""

    if not from_due_iso:
        return {
            "handled": True,
            "reply": f"ما فهمت تاريخ البحث: '{from_due_text}'. حدد التاريخ بوضوح.",
        }

    try:
        from_dt = datetime.fromisoformat(from_due_iso.replace("Z", "+00:00"))
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=USER_TZ)
        from_date_str = from_dt.date().isoformat()
    except Exception:
        return {"handled": True, "reply": "تاريخ البحث غير صالح."}

    all_tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    matching_tasks = [
        {"id": t.get("id", ""), "text": t.get("text", "")}
        for t in all_tasks
        if str(t.get("due", "") or "").strip().startswith(from_date_str) and t.get("id")
    ]

    if not matching_tasks:
        return {"handled": True, "reply": f"ما في مهام مستحقة في {from_due_text}."}

    lines = "\n".join(f"- {t['text']}" for t in matching_tasks)
    session["pending_action"] = create_pending_action(
        {
            "type": "task",
            "action": "bulk_update_due_date",
            "tasks": matching_tasks,
            "to_due_iso": to_due_iso,
            "to_due_text": to_due_display,
            "confirmation_status": "pending",
        }
    )
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {
        "handled": True,
        "reply": f"بدي أؤجل {len(matching_tasks)} مهام من {from_due_text} إلى {to_due_display}:\n{lines}\nموافق؟",
    }


def _handle_create(
    task_text: str,
    task_due_iso: str,
    task_due_text: str,
    task_notes: str,
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    if not task_text:
        return {"handled": True, "reply": "شو المهمة اللي بدك أضيفها؟"}

    if not task_due_iso and task_due_text:
        due_parse_source = normalize_user_message(task_due_text)
        parsed_due = parse_reminder_time_ai(
            due_parse_source,
            create_chat_completion_fn=create_chat_completion_fn,
            return_json=True,
        )
        if isinstance(parsed_due, dict):
            if parsed_due.get("success"):
                task_due_iso = parsed_due.get("remind_at_iso") or ""
            else:
                suggested = parsed_due.get("suggested_iso")
                if suggested:
                    try:
                        sdt = datetime.fromisoformat(suggested.replace("Z", "+00:00"))
                        if sdt.tzinfo is not None:
                            sdt = sdt.astimezone(USER_TZ)
                        confirm_text = sdt.strftime("%d/%m/%Y")
                    except Exception:
                        confirm_text = suggested
                    session["pending_action"] = create_pending_action(
                        {
                            "type": "task",
                            "action": "confirm_task_due_date",
                            "task_text": task_text,
                            "suggested_iso": suggested,
                            "confirmation_status": "pending",
                        }
                    )
                    save_session_fn(
                        session, session_file=session_file, mongo_db=mongo_db
                    )
                    return {
                        "handled": True,
                        "reply": f"ما فهمت موعد المهمة بدقّة. تقصد تضيف المهمة ليوم {confirm_text}?",
                    }
        else:
            task_due_iso = parsed_due or ""

        if not task_due_iso:
            return {
                "handled": True,
                "reply": "في موعد للمهمة لكن ما فهمته بدقة. اكتب التاريخ أو الوقت بشكل أوضح.",
            }

    if task_due_iso:
        try:
            due_dt = datetime.fromisoformat(task_due_iso.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)

            # Midnight means no time was given (date-only ISO, or AI due_iso
            # without a time), so default to 11:00 AM.
            if due_dt.hour == 0 and due_dt.minute == 0 and due_dt.second == 0:
                due_dt = due_dt.replace(hour=11, minute=0, second=0, microsecond=0)

            if due_dt <= datetime.now(USER_TZ):
                return {
                    "handled": True,
                    "reply": "موعد المهمة صار بالماضي، أعطني وقت لاحق.",
                }

            task_due_iso = due_dt.isoformat()
        except Exception:
            return {
                "handled": True,
                "reply": "وقت المهمة غير صالح. اكتب التاريخ أو الوقت بشكل أوضح.",
            }

    task_id = add_task(
        task_text,
        due_iso=task_due_iso,
        notes=task_notes,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
    )
    if task_id:
        session["_last_created_task_id"] = task_id
        session["_last_created_task_text"] = task_text
        record_last_action(
            session,
            "task_created",
            summary=task_text,
            refs={"task_id": task_id, "task_text": task_text},
        )
        if task_due_iso:
            reply = "تم التسجيل. المهمة محفوظة مع استحقاق."
            conflict_result = run_conflict_check_after_task_add(
                task_id=task_id,
                task_text=task_text,
                due_iso=task_due_iso,
                notes=task_notes,
                mongo_db=mongo_db,
                tasks_file=tasks_file,
            )
            if isinstance(conflict_result, str):
                conflict_alert = conflict_result
            else:
                conflict_alert = str(
                    (conflict_result or {}).get("alert_text", "") or ""
                )
            if conflict_alert:
                reply = f"{reply}\n\n⚠️ {conflict_alert}"
        else:
            reply = "تم التسجيل. المهمة محفوظة."
    else:
        reply = "ما قدرت أضيف المهمة."
    return {"handled": True, "reply": reply}


def _handle_list_overdue(
    *, session, session_file, mongo_db, tasks_file, save_session_fn
):
    tasks = load_overdue_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    if not tasks:
        return {"handled": True, "reply": "ما في مهام متأخرة 🎉"}
    lines = []
    for i, t in enumerate(tasks[:20], 1):
        text = str(t.get("text") or t.get("title") or "").strip()
        due = str(t.get("due_iso") or t.get("due") or "").strip()
        try:
            due_disp = (
                datetime.fromisoformat(due.replace("Z", "+00:00"))
                .astimezone(USER_TZ)
                .strftime("%d/%m")
            )
        except Exception:
            due_disp = due[:10] if due else ""
        lines.append(f"{i}. {text}" + (f" (كان موعدها {due_disp})" if due_disp else ""))
    return {
        "handled": True,
        "reply": f"⏰ المهام المتأخرة ({len(tasks)}):\n" + "\n".join(lines),
    }


def _handle_delete_completed(
    *, session, session_file, mongo_db, tasks_file, save_session_fn
):
    try:
        count = delete_completed_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        if count == 0:
            return {"handled": True, "reply": "ما في مهام مكتملة لحذفها."}
        return {"handled": True, "reply": f"✅ حذفت {count} مهمة مكتملة."}
    except Exception as e:
        return {"handled": True, "reply": f"ما قدرت أحذف المهام المكتملة: {e}"}


def _handle_complete_all(
    *, session, session_file, mongo_db, tasks_file, save_session_fn
):
    try:
        count = complete_all_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        if count == 0:
            return {"handled": True, "reply": "ما في مهام نشطة لإكمالها."}
        return {"handled": True, "reply": f"✅ كمّلت {count} مهمة."}
    except Exception as e:
        return {"handled": True, "reply": f"ما قدرت أكمّل المهام: {e}"}


def handle_task_action(
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
        return {"handled": True, "reply": "المهام خاصة بنبيل 😊"}

    task_action = str(params.get("action", "create")).strip().lower()
    if task_action not in {
        "create",
        "list",
        "list_completed",
        "list_all",
        "list_overdue",
        "complete",
        "complete_multi",
        "complete_all",
        "uncomplete",
        "uncomplete_multi",
        "rename",
        "append_note",
        "replace_note",
        "clear_note",
        "update_due_date",
        "update_due_time",
        "delete",
        "delete_multi",
        "delete_all",
        "delete_completed",
        "bulk_update_due_date",
    }:
        return {"handled": True, "reply": "نوع إجراء المهمة غير صالح."}

    task_text = str(params.get("text", "")).strip()
    task_reference = str(params.get("reference", "")).strip()

    if (
        task_action
        in {
            "complete",
            "complete_multi",
            "uncomplete",
            "uncomplete_multi",
            "delete",
            "delete_multi",
        }
        and not task_reference
    ):
        task_reference = normalized_user_message

        for prefix in (
            "كمل مهمة",
            "كمل مهمه",
            "كمل المهمة",
            "كمل المهمه",
            "كمّل مهمة",
            "كمّل مهمه",
            "كمّل المهمة",
            "كمّل المهمه",
            "اكمل مهمة",
            "اكمل مهمه",
            "اكمل المهمة",
            "اكمل المهمه",
            "أنجز مهمة",
            "أنجز مهمه",
            "أنجز المهمة",
            "أنجز المهمه",
            "انجز مهمة",
            "انجز مهمه",
            "انجز المهمة",
            "انجز المهمه",
            "خلص مهمة",
            "خلص مهمه",
            "خلص المهمة",
            "خلص المهمه",
            "خلصت مهمة",
            "خلصت مهمه",
            "خلصت المهمة",
            "خلصت المهمه",
            "خصلت مهمة",
            "خصلت مهمه",
            "خصلت المهمة",
            "خصلت المهمه",
            "أنهيت مهمة",
            "أنهيت مهمه",
            "أنهيت المهمة",
            "أنهيت المهمه",
            "انهيت مهمة",
            "انهيت مهمه",
            "انهيت المهمة",
            "انهيت المهمه",
            "رجع مهمة",
            "رجع مهمه",
            "رجع المهمة",
            "رجع المهمه",
            "رجعي مهمة",
            "رجعي مهمه",
            "رجعي المهمة",
            "رجعي المهمه",
            "رجّع مهمة",
            "رجّع مهمه",
            "رجّع المهمة",
            "رجّع المهمه",
            "رجّعي مهمة",
            "رجّعي مهمه",
            "رجّعي المهمة",
            "رجّعي المهمه",
            "ارجع مهمة",
            "ارجع مهمه",
            "ارجع المهمة",
            "ارجع المهمه",
            "ارجعي مهمة",
            "ارجعي مهمه",
            "ارجعي المهمة",
            "ارجعي المهمه",
            "الغ إكمال مهمة",
            "الغ إكمال مهمه",
            "الغ إكمال المهمة",
            "الغ إكمال المهمه",
            "الغ اكمال مهمة",
            "الغ اكمال مهمه",
            "الغ اكمال المهمة",
            "الغ اكمال المهمه",
            "الغي إكمال مهمة",
            "الغي إكمال مهمه",
            "الغي إكمال المهمة",
            "الغي إكمال المهمه",
            "الغي اكمال مهمة",
            "الغي اكمال مهمه",
            "الغي اكمال المهمة",
            "الغي اكمال المهمه",
            "احذف مهمة",
            "احذف مهمه",
            "احذف المهمة",
            "احذف المهمه",
            "احذفي مهمة",
            "احذفي مهمه",
            "احذفي المهمة",
            "احذفي المهمه",
            "امسح مهمة",
            "امسح مهمه",
            "امسح المهمة",
            "امسح المهمه",
            "امسحي مهمة",
            "امسحي مهمه",
            "امسحي المهمة",
            "امسحي المهمه",
            "شيل مهمة",
            "شيل مهمه",
            "شيل المهمة",
            "شيل المهمه",
            "شيلي مهمة",
            "شيلي مهمه",
            "شيلي المهمة",
            "شيلي المهمه",
        ):
            if task_reference.startswith(prefix):
                task_reference = task_reference[len(prefix) :].strip(" .،")
                break

    task_due_iso = str(params.get("due_iso", "")).strip()
    task_due_text = str(params.get("due_text", "")).strip()
    task_time_text = str(
        params.get("time_text", "") or params.get("due_text", "")
    ).strip()
    task_notes = str(params.get("notes", "")).strip()

    _common = dict(
        session=session,
        session_file=session_file,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        save_session_fn=save_session_fn,
    )
    _with_ai = dict(**_common, create_chat_completion_fn=create_chat_completion_fn)

    if task_action in {"list", "list_completed", "list_all", "list_overdue"}:
        if task_action == "list_overdue":
            return _handle_list_overdue(**_common)
        return _handle_list(task_action, **_common)
    elif task_action == "rename":
        return _handle_rename(task_reference, task_text, **_common)
    elif task_action == "update_due_date":
        return _handle_update_due_date(
            task_reference, task_due_iso, task_due_text, **_with_ai
        )
    elif task_action == "append_note":
        return _handle_append_note(task_reference, task_text, task_notes, **_common)
    elif task_action in {"replace_note", "clear_note"}:
        note_content = "" if task_action == "clear_note" else task_notes
        return _handle_replace_note(task_reference, task_text, note_content, **_common)
    elif task_action == "update_due_time":
        return _handle_update_due_time(
            task_reference, task_due_iso, task_due_text, task_time_text, **_with_ai
        )
    elif task_action == "uncomplete_multi":
        return _handle_uncomplete_multi(task_reference, **_common)
    elif task_action == "uncomplete":
        return _handle_uncomplete(task_reference, **_common)
    elif task_action == "complete":
        return _handle_complete(task_reference, **_common)
    elif task_action == "complete_multi":
        return _handle_complete_multi(task_reference, **_common)
    elif task_action == "complete_all":
        return _handle_complete_all(**_common)
    elif task_action == "delete":
        return _handle_delete(task_reference, **_common)
    elif task_action == "delete_multi":
        return _handle_delete_multi(task_reference, **_common)
    elif task_action == "delete_all":
        return _handle_delete_all(
            session=session,
            session_file=session_file,
            mongo_db=mongo_db,
            save_session_fn=save_session_fn,
        )
    elif task_action == "delete_completed":
        return _handle_delete_completed(**_common)
    elif task_action == "bulk_update_due_date":
        return _handle_bulk_update_due_date(params, **_with_ai)
    else:  # create
        return _handle_create(
            task_text, task_due_iso, task_due_text, task_notes, **_with_ai
        )
