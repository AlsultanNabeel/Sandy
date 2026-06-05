"""Email tools — schemas + adapters لـ ToolRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext


def _call_email(params: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.executor.dispatch import execute_operational_action
    return execute_operational_action(
        action_type="email",
        params=params,
        user_message=ctx.user_message,
        normalized_user_message=ctx.normalized_message,
        session=ctx.session,
        session_file=None,
        mongo_db=ctx.mongo_db,
        tasks_file=None,
        create_chat_completion_fn=ctx.create_chat_completion_fn,
        save_session_fn=lambda *a, **kw: None,
    )


# Adapters

def email_read(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_email({"action": "read", **args}, ctx)

def email_unread_count(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_email({"action": "unread_count"}, ctx)

def email_send(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_email({"action": "send", **args}, ctx)

def email_reply(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_email({"action": "reply", **args}, ctx)

def email_draft(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    to = str(args.get("to") or "").strip()
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "").strip()
    if not body:
        return {"handled": True, "reply": "حدّد محتوى المسودة."}
    try:
        from app.features.gmail import create_gmail_draft
        draft_id = create_gmail_draft(to=to, subject=subject, body=body)
        return {"handled": True, "reply": f"✉️ حفظت المسودة في Gmail. (ID: `{draft_id}`)"}
    except Exception as e:
        return {"handled": True, "reply": f"ما قدرت أحفظ المسودة: {e}"}


# Schemas

EMAIL_TOOLS = [
    {
        "name": "email_read",
        "description": "اقرأ آخر رسائل البريد الوارد غير المقروءة",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "عدد الرسائل (افتراضي 10)"},
            },
            "required": [],
        },
        "handler": email_read,
    },
    {
        "name": "email_unread_count",
        "description": "كم رسالة غير مقروءة في البريد",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": email_unread_count,
    },
    {
        "name": "email_send",
        "description": "أرسل رسالة بريد إلكتروني",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "عنوان المستلم"},
                "subject": {"type": "string", "description": "الموضوع"},
                "body": {"type": "string", "description": "نص الرسالة"},
            },
            "required": ["to"],
        },
        "handler": email_send,
    },
    {
        "name": "email_reply",
        "description": "رد على رسالة بريد",
        "parameters": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "ID الرسالة للرد عليها"},
                "body": {"type": "string", "description": "نص الرد"},
            },
            "required": ["body"],
        },
        "handler": email_reply,
    },
    {
        "name": "email_draft",
        "description": "احفظ مسودة إيميل في Gmail بدون إرسال",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "عنوان المستلم (اختياري)"},
                "subject": {"type": "string", "description": "موضوع الإيميل"},
                "body": {"type": "string", "description": "محتوى الإيميل"},
            },
            "required": ["body"],
        },
        "handler": email_draft,
    },
]
