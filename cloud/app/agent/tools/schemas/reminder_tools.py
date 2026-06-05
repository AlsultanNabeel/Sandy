"""Reminder tools — schemas + adapters لـ ToolRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

def _NOOP_SAVE(*a, **kw): return None


def _call_reminder(params: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.executor.reminder_handlers import handle_reminder_action
    return handle_reminder_action(
        params,
        user_message=ctx.user_message,
        normalized_user_message=ctx.normalized_message,
        session=ctx.session,
        session_file=None,
        mongo_db=ctx.mongo_db,
        tasks_file=None,
        create_chat_completion_fn=ctx.create_chat_completion_fn,
        save_session_fn=_NOOP_SAVE,
    )


# Adapters

def reminder_create(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_reminder({"action": "create", **args}, ctx)

def reminder_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_reminder({"action": "list"}, ctx)

def reminder_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    if args.get("all"):
        return _call_reminder({"action": "delete_all"}, ctx)
    return _call_reminder({"action": "delete", **args}, ctx)

def reminder_update(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_reminder({"action": "update", **args}, ctx)


# Schemas

REMINDER_TOOLS = [
    {
        "name": "reminder_create",
        "description": "أنشئ تذكيراً في وقت محدد",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "نص التذكير"},
                "time_text": {"type": "string", "description": "وقت التذكير بصيغة طبيعية مثل 'بعد 5 دقائق' أو 'الساعة 3'"},
                "remind_at_iso": {"type": "string", "description": "وقت ISO اختياري لو معروف بدقة"},
                "recurrence": {"type": "string", "description": "RRULE للتكرار مثل FREQ=DAILY"},
            },
            "required": ["text"],
        },
        "handler": reminder_create,
    },
    {
        "name": "reminder_list",
        "description": "اعرض التذكيرات القادمة",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": reminder_list,
    },
    {
        "name": "reminder_delete",
        "description": "احذف تذكير أو جميع التذكيرات — استخدم all=true لحذف الكل (يطلب تأكيداً تلقائياً)",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "نص أو وصف التذكير للحذف"},
                "reminder_id": {"type": "string", "description": "ID التذكير لو معروف"},
                "all": {"type": "boolean", "description": "true لحذف جميع التذكيرات"},
            },
            "required": [],
        },
        "handler": reminder_delete,
    },
    {
        "name": "reminder_update",
        "description": "عدّل وقت أو نص تذكير موجود",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "النص الحالي للتذكير"},
                "new_text": {"type": "string", "description": "النص الجديد"},
                "time_text": {"type": "string", "description": "الوقت الجديد"},
            },
            "required": ["text"],
        },
        "handler": reminder_update,
    },
]
