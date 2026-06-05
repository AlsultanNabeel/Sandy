"""C2 — مشاركة المحتوى الذكي.

Sandy تشارك محتوى مرتبط باهتمامات المستخدم — تستدعي research_web تحت الغطاء.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)


def share_interesting_content(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يشارك محتوى عن اهتمام المستخدم — explicit topic أو يختار top interest."""
    topic = str(args.get("topic") or "").strip()

    if not topic and ctx.mongo_db is not None:
        try:
            from app.agent.interests_tracker import get_top_interests
            chat_id = str((ctx.state or {}).get("chat_id", "default"))
            user_id = str((ctx.state or {}).get("user_id", "default"))
            tops = get_top_interests(chat_id, user_id, ctx.mongo_db, limit=1)
            if tops:
                topic = tops[0]
        except Exception:
            pass

    if not topic:
        return {
            "handled": True,
            "reply": "ما عندي فكرة عن اهتماماتك بعد، احكيلي عن شي بحبه وبجيبلك معلومات حلوة عنه 💛",
        }

    # نستخدم research_web الموجود بدل تكرار المنطق
    try:
        from app.agent.tools.registry import get_registry
        registry = get_registry()
        research_tool = registry.get("research_web")
        if research_tool:
            query = f"معلومات مثيرة عن {topic}"
            return research_tool.handler({"query": query}, ctx)
    except Exception as exc:
        logger.debug(f"[content_share] research failed: {exc}")

    return {
        "handled": True,
        "reply": f"حابب أشاركك محتوى عن {topic}، بس البحث مش شغّال هلق — جرّبني بعد شوي.",
    }


CONTENT_SHARE_TOOLS = [
    {
        "name": "share_interesting_content",
        "description": "شاركي محتوى ذكي مرتبط باهتمامات المستخدم — يُستدعى عند طلبه شي يفيده أو يلهمه",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "موضوع محدد (اختياري) — إذا فاضي تختار من اهتمامات المستخدم"},
            },
            "required": [],
        },
        "handler": share_interesting_content,
    },
]
