"""Calendar tools — schemas + adapters لـ ToolRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

def _NOOP_SAVE(*a, **kw): return None


def _call_calendar(params: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.executor.calendar_handlers import handle_calendar_action
    return handle_calendar_action(
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

def calendar_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    params: Dict[str, Any] = {"action": "add", **args}
    # الـ handler بدّه start_iso، فنحلل time_text لو مش ISO أصلاً
    if not params.get("start_iso"):
        time_text = str(args.get("time_text") or args.get("date") or "").strip()
        if time_text:
            from app.features.google_calendar import parse_reminder_time_ai
            parsed = parse_reminder_time_ai(
                time_text,
                create_chat_completion_fn=ctx.create_chat_completion_fn,
                return_json=True,
            )
            if isinstance(parsed, dict) and parsed.get("success"):
                params["start_iso"] = parsed.get("remind_at_iso", "")
            elif isinstance(parsed, str) and parsed:
                params["start_iso"] = parsed
    return _call_calendar(params, ctx)

def calendar_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_calendar({"action": "list", **args}, ctx)

def calendar_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    titles = args.get("titles")
    if titles and isinstance(titles, list):
        return _call_calendar({"action": "delete_multi", "titles": titles}, ctx)
    return _call_calendar({"action": "delete", **args}, ctx)

def calendar_update(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_calendar({"action": "update", **args}, ctx)

def calendar_followup(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.google_calendar import add_calendar_followup_ping
    try:
        result = add_calendar_followup_ping(
            parent_event_id=str(args.get("parent_event_id") or ""),
            parent_summary=str(args.get("parent_summary") or "موعد"),
            ping_start_iso=str(args.get("ping_start_iso") or ""),
            minutes_duration=int(args.get("minutes_duration") or 2),
        )
        if result.get("success"):
            summary = args.get("parent_summary") or "الموعد"
            return {"handled": True, "reply": f"✅ تم جدولة تذكير المتابعة بعد {summary}."}
        return {"handled": True, "reply": f"⚠️ ما قدرت أضيف تذكير المتابعة: {result.get('error', '')}"}
    except Exception as exc:
        return {"handled": True, "reply": f"⚠️ خطأ في تذكير المتابعة: {exc}"}


# Schemas

CALENDAR_TOOLS = [
    {
        "name": "calendar_add",
        "description": "أضف حدث أو موعد للتقويم",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "عنوان الحدث"},
                "time_text": {"type": "string", "description": "وقت الحدث مثل 'الساعة 3 بعد الظهر بكرا'"},
                "date": {"type": "string", "description": "التاريخ ISO اختياري"},
                "duration_min": {"type": "integer", "description": "المدة بالدقائق (افتراضي 60)"},
                "location": {"type": "string", "description": "المكان اختياري"},
                "description": {"type": "string", "description": "وصف إضافي"},
            },
            "required": ["title", "time_text"],
        },
        "handler": calendar_add,
    },
    {
        "name": "calendar_list",
        "description": "اعرض مواعيد التقويم",
        "parameters": {
            "type": "object",
            "properties": {
                "range_type": {
                    "type": "string",
                    "description": "today | tomorrow | this_week | next_week | weekend | upcoming (default)",
                },
                "date": {"type": "string", "description": "تاريخ محدد ISO اختياري"},
            },
            "required": [],
        },
        "handler": calendar_list,
    },
    {
        "name": "calendar_delete",
        "description": "احذف حدث أو موعد من التقويم",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "عنوان الحدث"},
                "titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "عدة أحداث للحذف دفعة واحدة",
                },
            },
            "required": [],
        },
        "handler": calendar_delete,
    },
    {
        "name": "calendar_followup",
        "description": "جدوِل تذكير متابعة تلقائي بعد انتهاء حدث معين",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_summary": {"type": "string", "description": "اسم الحدث الأصلي"},
                "ping_start_iso": {"type": "string", "description": "وقت إرسال التذكير بصيغة ISO"},
                "parent_event_id": {"type": "string", "description": "ID الحدث الأصلي إن توفر"},
                "minutes_duration": {"type": "integer", "description": "مدة نافذة التذكير بالدقائق (افتراضي 2)"},
            },
            "required": ["parent_summary", "ping_start_iso"],
        },
        "handler": calendar_followup,
    },
    {
        "name": "calendar_update",
        "description": "عدّل حدث موجود في التقويم",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "عنوان الحدث الحالي"},
                "new_title": {"type": "string", "description": "العنوان الجديد"},
                "time_text": {"type": "string", "description": "الوقت الجديد"},
                "location": {"type": "string", "description": "المكان الجديد"},
            },
            "required": ["title"],
        },
        "handler": calendar_update,
    },
]
