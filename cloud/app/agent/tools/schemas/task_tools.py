"""Task tools — schemas + adapters لـ ToolRegistry.

كل tool = schema (لـ Gemini) + adapter (يستدعي handle_task_action).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

def _NOOP_SAVE(*a, **kw): return None


def _persona_intensity(ctx: "DispatchContext") -> str:
    """نبرة Sandy الحالية من الـ state (يحطها soul_node قبل execute)."""
    try:
        if ctx.state:
            return ctx.state.get("persona_intensity") or "standard"
    except Exception:
        pass
    return "standard"


def _task_create_reply(titles: List[str], due: str = "", intensity: str = "standard") -> str:
    """يبني تأكيد إنشاء المهام محلياً من response_templates، بدون استدعاء LLM.

    يدعم عنوان واحد أو عدة عناوين. النبرة تتبع مزاج Sandy الحالي، ولو ما في
    قالب للنبرة المطلوبة بيرجع للـ standard.
    """
    from app.agent.graph.response_templates import get_response_template

    intro = get_response_template("task.create", intensity) or "سجّلتها"
    due_part = f" بموعد {due}" if due else ""
    if len(titles) == 1:
        return f"{intro} '{titles[0]}'{due_part}."
    listed = "، ".join(f"'{t}'" for t in titles)
    return f"{intro} {len(titles)} مهام: {listed}{due_part}."


def _call_task(params: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.executor.task_handlers import handle_task_action
    return handle_task_action(
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

def task_create(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    titles = args.get("titles")
    due = args.get("due", "")
    intensity = _persona_intensity(ctx)
    if titles and isinstance(titles, list):
        created = []
        for t in titles:
            r = _call_task({"action": "create", "text": str(t), "due_text": due, "notes": args.get("notes", "")}, ctx)
            if r.get("handled"):
                created.append(str(t))
        return {"handled": True, "reply": _task_create_reply(created, due, intensity) if created else "تم."}
    result = _call_task({
        "action": "create",
        "text": args.get("title", ""),
        "due_text": due,
        "notes": args.get("notes", ""),
    }, ctx)
    if result.get("handled"):
        result["reply"] = _task_create_reply([args.get("title", "")], due, intensity)
    return result

def task_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    filter_type = str(args.get("filter") or "active").lower()
    action_map = {
        "completed": "list_completed",
        "all": "list_all",
        "overdue": "list_overdue",
    }
    return _call_task({"action": action_map.get(filter_type, "list")}, ctx)

def task_complete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    if args.get("all"):
        return _call_task({"action": "complete_all"}, ctx)
    refs = args.get("references")
    if refs and isinstance(refs, list):
        return _call_task({"action": "complete_multi", "reference": " ".join(str(r) for r in refs)}, ctx)
    return _call_task({"action": "complete", "reference": args.get("reference", "")}, ctx)

def task_uncomplete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_task({"action": "uncomplete", "reference": args.get("reference", "")}, ctx)

def task_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    if args.get("scope") == "completed":
        return _call_task({"action": "delete_completed"}, ctx)
    if args.get("all"):
        return _call_task({"action": "delete_all"}, ctx)
    refs = args.get("references")
    if refs and isinstance(refs, list):
        return _call_task({"action": "delete_multi", "reference": " ".join(str(r) for r in refs)}, ctx)
    return _call_task({"action": "delete", "reference": args.get("reference", "")}, ctx)

def task_update(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    ref = args.get("reference", "")
    if args.get("title"):
        return _call_task({"action": "rename", "reference": ref, "text": args["title"]}, ctx)
    if args.get("notes"):
        return _call_task({"action": "append_note", "reference": ref, "notes": args["notes"]}, ctx)
    if args.get("due"):
        return _call_task({"action": "update_due_date", "reference": ref, "due_text": args["due"]}, ctx)
    return {"handled": False, "reply": "حدّد ما تريد تعديله (عنوان/ملاحظة/موعد)."}


# Schemas

TASK_TOOLS = [
    {
        "name": "task_create",
        "description": "أضف مهمة أو عدة مهام — استخدم titles=[...] لإضافة أكثر من مهمة دفعة واحدة",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "عنوان المهمة (لمهمة واحدة)"},
                "titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "قائمة عناوين لإضافة عدة مهام دفعة واحدة",
                },
                "notes": {"type": "string", "description": "ملاحظة اختيارية"},
                "due": {"type": "string", "description": "تاريخ الاستحقاق (ISO أو وصف مثل 'بكرا')"},
            },
            "required": [],
        },
        "handler": task_create,
    },
    {
        "name": "task_list",
        "description": "اعرض قائمة المهام",
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "active (default) | completed | all | overdue",
                },
            },
            "required": [],
        },
        "handler": task_list,
    },
    {
        "name": "task_complete",
        "description": "اعلم مهمة أو أكثر كمكتملة — استخدم all=true لإكمال جميع المهام (يطلب تأكيداً تلقائياً، لا تستخدم request_confirmation)",
        "parameters": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "رقم أو عنوان المهمة"},
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "قائمة مهام لإكمالها دفعة واحدة",
                },
                "all": {"type": "boolean", "description": "true لإكمال جميع المهام"},
            },
            "required": [],
        },
        "handler": task_complete,
    },
    {
        "name": "task_uncomplete",
        "description": "أعد مهمة للقائمة النشطة",
        "parameters": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "رقم أو عنوان المهمة"},
            },
            "required": ["reference"],
        },
        "handler": task_uncomplete,
    },
    {
        "name": "task_delete",
        "description": "احذف مهمة أو أكثر أو جميع المهام — استخدم all=true لحذف الكل، scope='completed' لحذف المكتملة فقط (يطلب تأكيداً تلقائياً، لا تستخدم request_confirmation)",
        "parameters": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "رقم أو عنوان المهمة"},
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "قائمة مهام للحذف دفعة واحدة",
                },
                "all": {"type": "boolean", "description": "true لحذف جميع المهام النشطة"},
                "scope": {"type": "string", "description": "completed لحذف المهام المكتملة فقط"},
            },
            "required": [],
        },
        "handler": task_delete,
    },
    {
        "name": "task_update",
        "description": "عدّل عنوان أو ملاحظة أو موعد استحقاق مهمة",
        "parameters": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "رقم أو عنوان المهمة"},
                "title": {"type": "string", "description": "العنوان الجديد"},
                "notes": {"type": "string", "description": "ملاحظة تُضاف"},
                "due": {"type": "string", "description": "موعد الاستحقاق الجديد"},
            },
            "required": ["reference"],
        },
        "handler": task_update,
    },
]
