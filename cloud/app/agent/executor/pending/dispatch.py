import re
from datetime import datetime
from typing import Any, Dict

from app.utils.time import USER_TZ
from app.agent.pending import (
    get_valid_pending_action,
    clear_pending_action,
    consume_pending_action,
)
from app.agent.executor.helpers import (
    _handle_modify_response,
    _is_quick_confirmation,
)
from app.agent.executor.pending.task_pending import (
    _handle_clarify_task_choice,
    _handle_clarify_task_write,
    _handle_confirm_task_due_date,
    _exec_task_complete,
    _exec_task_complete_multi,
    _exec_task_uncomplete,
    _exec_task_rename,
    _exec_task_update_due_date,
    _exec_task_update_due_time,
    _exec_task_append_note,
    _exec_task_replace_note,
    _exec_task_uncomplete_multi,
    _exec_task_delete_one,
    _exec_task_delete_multi,
    _exec_task_delete_all,
    _exec_task_bulk_update_due_date,
)
from app.agent.executor.pending.reminder_pending import (
    _handle_confirm_remind_at,
    _handle_await_remind_at,
    _exec_reminder_delete_all,
)
from app.agent.executor.pending.email_pending import (
    _handle_email_confirm_pending,
    _handle_await_email_body,
    _exec_email_send,
    _exec_email_draft,
)


def classify_response_to_pending(user_message: str, pending_type: str) -> str:
    """Fallback used only when no intent_hint is passed (old pipeline)."""
    text = (user_message or "").strip().lower()
    if re.search(r"^(اه|أه|نعم|ايوه|تمام|اكيد|ok|yes|sure)$", text):
        return "confirm"
    if re.search(r"^(لا|لأ|الغ|إلغاء|no|cancel)$", text):
        return "reject"
    return "ignore"


def execute_pending_action(
    *,
    user_message: str,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
    create_chat_completion_fn=None,
    intent_hint: str = "",
) -> Dict[str, Any]:
    pending = get_valid_pending_action(session) or {}

    if not pending:
        return {"handled": False}

    pending_type = str(pending.get("type", "")).strip().lower()
    pending_action = str(pending.get("action", "")).strip().lower()

    # await_* actions take free-form input, so skip the classify gate.
    if pending_action.startswith("await_"):
        if pending_type == "task" and pending_action == "await_name":
            task_name = (user_message or "").strip()
            if not task_name:
                return {"handled": True, "reply": "ما فهمت الاسم، حاول مرة ثانية."}
            try:
                from app.features.tasks_store import add_task as _add_task
                from app.utils.user_profiles import (
                    active_user_profile_context,
                    get_active_user_profile,
                )

                with active_user_profile_context(get_active_user_profile()):
                    _add_task(task_name, mongo_db=mongo_db, tasks_file=tasks_file)
                consume_pending_action(session)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
                return {"handled": True, "reply": f"✅ تم إضافة المهمة: {task_name}"}
            except Exception as _e:
                return {"handled": True, "reply": f"ما قدرت أضيف المهمة: {_e}"}

        if pending_type == "reminder" and pending_action == "await_remind_at":
            return _handle_await_remind_at(
                user_message,
                pending,
                create_chat_completion_fn=create_chat_completion_fn,
                session=session,
                session_file=session_file,
                mongo_db=mongo_db,
                save_session_fn=save_session_fn,
            )

        if pending_type == "email" and pending_action == "await_body":
            return _handle_await_email_body(
                user_message,
                pending,
                session=session,
                session_file=session_file,
                mongo_db=mongo_db,
                save_session_fn=save_session_fn,
            )

        return {"handled": False}

    # intent_hint comes from route_with_fc (Gemini) through the graph pipeline.
    # When it's set we already know the intent and can skip the regex classifier.
    response_intent = (
        intent_hint
        if intent_hint
        else classify_response_to_pending(user_message, pending_type)
    )

    if response_intent == "reject":
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "تمام، لغيت العملية."}

    if response_intent == "ignore":
        current_pending = get_valid_pending_action(session) or {}
        if current_pending:
            session["archived_pending"] = {
                **current_pending,
                "archived_at": datetime.now(USER_TZ).isoformat(),
            }
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": False}

    if response_intent == "modify":
        return _handle_modify_response(
            user_message=user_message,
            pending=pending,
            pending_type=pending_type,
            session=session,
            session_file=session_file,
            mongo_db=mongo_db,
            save_session_fn=save_session_fn,
        )

    if pending_type == "task":
        image_state = session.get("image_state")
        if isinstance(image_state, dict):
            image_state["pending_image_action"] = None

    # Clarification handlers. These don't need a quick confirmation.
    _clarify_common = dict(
        session=session,
        session_file=session_file,
        mongo_db=mongo_db,
        save_session_fn=save_session_fn,
    )

    if pending_type == "task" and pending_action == "clarify_task_choice":
        return _handle_clarify_task_choice(
            user_message,
            pending,
            create_chat_completion_fn=create_chat_completion_fn,
            **_clarify_common,
        )

    if pending_type == "task" and pending_action == "clarify_task_write":
        return _handle_clarify_task_write(user_message, pending)

    if pending_type == "reminder" and pending_action == "confirm_remind_at":
        return _handle_confirm_remind_at(user_message, pending, **_clarify_common)

    if pending_type == "task" and pending_action == "confirm_task_due_date":
        return _handle_confirm_task_due_date(
            user_message,
            pending,
            tasks_file=tasks_file,
            **_clarify_common,
        )

    # For email confirm/edit, catch edits before the quick-confirmation gate.
    if pending_type == "email" and pending_action == "confirm_send":
        edit_result = _handle_email_confirm_pending(
            user_message,
            pending,
            create_chat_completion_fn=create_chat_completion_fn,
            **_clarify_common,
        )
        if edit_result.get("handled"):
            return edit_result
        # otherwise fall through to the quick-confirmation gate for "ارسل"/"اه"

    # Quick-confirmation gate.
    if not _is_quick_confirmation(user_message):
        return {"handled": False}

    consume_pending_action(session)

    # Execution handlers.
    _exec_common = dict(
        pending=pending,
        session=session,
        session_file=session_file,
        mongo_db=mongo_db,
        tasks_file=tasks_file,
        save_session_fn=save_session_fn,
    )

    if pending_type == "task" and pending_action == "complete":
        return _exec_task_complete(**_exec_common)
    if pending_type == "task" and pending_action == "complete_multi":
        return _exec_task_complete_multi(**_exec_common)
    if pending_type == "task" and pending_action == "uncomplete":
        return _exec_task_uncomplete(**_exec_common)
    if pending_type == "task" and pending_action == "rename":
        return _exec_task_rename(**_exec_common)
    if pending_type == "task" and pending_action == "update_due_date":
        return _exec_task_update_due_date(**_exec_common)
    if pending_type == "task" and pending_action == "update_due_time":
        return _exec_task_update_due_time(**_exec_common)
    if pending_type == "task" and pending_action == "append_note":
        return _exec_task_append_note(**_exec_common)
    if pending_type == "task" and pending_action == "replace_note":
        return _exec_task_replace_note(**_exec_common)
    if pending_type == "task" and pending_action == "uncomplete_multi":
        return _exec_task_uncomplete_multi(**_exec_common)
    if pending_type == "task" and pending_action == "delete_one":
        return _exec_task_delete_one(**_exec_common)
    if pending_type == "task" and pending_action == "delete_multi":
        return _exec_task_delete_multi(**_exec_common)
    if pending_type == "task" and pending_action == "delete_all":
        return _exec_task_delete_all(**_exec_common)
    if pending_type == "task" and pending_action == "bulk_update_due_date":
        return _exec_task_bulk_update_due_date(**_exec_common)

    _exec_no_tasks = dict(
        pending=pending,
        session=session,
        session_file=session_file,
        mongo_db=mongo_db,
        save_session_fn=save_session_fn,
    )

    if pending_type == "reminder" and pending_action == "delete_all":
        return _exec_reminder_delete_all(**_exec_no_tasks)

    # Email execution
    if pending_type == "email" and pending_action == "confirm_send":
        return _exec_email_send(**_exec_no_tasks)
    if pending_type == "email" and pending_action == "draft":
        return _exec_email_draft(**_exec_no_tasks)

    return {"handled": False}
