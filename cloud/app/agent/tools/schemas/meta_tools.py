"""Meta-tools — routing signals لـ Gemini.

هذه الـ tools لا تُنفَّذ عبر ToolDispatcher — بل تُعالَج
عبر routing logic (router_node / clarify_node / pending_node / execute_node).
توجد في الـ registry حتى يراها Gemini في الـ catalog ويعرف متى يستخدمها.
"""

from __future__ import annotations

from typing import Any, Dict

# dummy handler — لا يُستدعى أبداً (execute_node يحجبه عبر _META_TOOL_NAMES)
def _routing_signal(args: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    return {"handled": False, "reply": ""}


META_TOOLS = [
    {
        "name": "chat_respond",
        "description": (
            "رد على المستخدم — استخدم عند عدم وجود أمر محدد أو دردشة عامة.\n\n"
            "🚫 ممنوع تستخدميه لو الرسالة فيها أفعال action واضحة + objects:\n"
            "- 'اقفلي/افتحي/سكّري/بطّلي/علّقي/افتحي ... من جديد' + 'issue' → "
            "استدعي github_close_issue / github_reopen_issue / github_comment_issue\n"
            "- 'افتحي/اعملي/حطي/ضيفي' + 'issue' + موضوع → github_create_issue\n"
            "- 'جيبيلي/اعرضيلي' + 'issues/commits' → github_issues / github_commits\n"
            "- أوامر tasks/reminders/calendar → الـ FC المخصص لها\n\n"
            "STM ممكن يكون فيه نفس الموضوع — مش مبرر للذهاب لـ chat. الـ FC handler "
            "يتعامل مع التكرار/الـ idempotency بنفسه."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "general | proactive",
                },
            },
            "required": [],
        },
        "handler": _routing_signal,
    },
    {
        "name": "chat_emotional",
        "description": "دعم عاطفي — استخدم عند mood=stressed/frustrated/sad",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": _routing_signal,
    },
    {
        "name": "ask_clarification",
        "description": "اطلب توضيحاً — confidence<0.7 في عمليات حذف/إرسال/تعديل",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "السؤال التوضيحي للمستخدم"},
            },
            "required": ["question"],
        },
        "handler": _routing_signal,
    },
    {
        "name": "request_confirmation",
        "description": "اطلب تأكيداً قبل عملية لا رجعة فيها — الـ handler نفسه يتولى التأكيد",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "ملخص العملية المطلوب تأكيدها"},
            },
            "required": ["summary"],
        },
        "handler": _routing_signal,
    },
    {
        "name": "pending_confirm",
        "description": "تأكيد عملية pending — عند الرد بـ نعم/تمام/موافق/أكيد",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": _routing_signal,
    },
    {
        "name": "pending_reject",
        "description": "رفض عملية pending — عند الرد بـ لا/إلغاء/ما أبي",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": _routing_signal,
    },
    {
        "name": "pending_select",
        "description": "اختيار خيار من قائمة pending — عند الرد برقم (1، 2، 3...)",
        "parameters": {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "description": "الرقم أو الخيار المختار"},
            },
            "required": ["choice"],
        },
        "handler": _routing_signal,
    },
]
