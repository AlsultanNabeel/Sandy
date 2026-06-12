"""Builds the context block for Sandy.

Combines STM, semantic LTM, persona directives and session state. Both the
LangGraph pipeline (soul_node) and the Gemini Live voice session import from
here, so they build context the same way.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_memory_context(
    chat_id: str,
    user_id: str,
    message: str,
    mongo_db,
    stm_history: Optional[List[Dict]] = None,
    include_semantic: bool = True,
) -> Dict[str, Any]:
    """
    Assemble all memory layers into a unified context dict.

    Returns:
        {
            "stm_turns":          list[dict],   # Redis STM messages
            "persona_directives": str | None,   # style/prefs/relationships/lessons
            "semantic_summaries": list[str],    # relevant summaries (vector search)
            "semantic_facts":     list[str],    # relevant facts (vector search)
            "session_state":      dict | None,  # cross-platform user state
        }
    """
    ctx: Dict[str, Any] = {
        "stm_turns": stm_history or [],
        "persona_directives": None,
        "semantic_summaries": [],
        "semantic_facts": [],
        "session_state": None,
    }

    if mongo_db and chat_id:
        ctx["persona_directives"] = get_persona_directives(chat_id, user_id, mongo_db)
        try:
            from app.agent.session_state import get_session_state
            ctx["session_state"] = get_session_state(chat_id, mongo_db)
        except Exception:
            pass

    if include_semantic and message and chat_id:
        try:
            from app.agent.semantic_memory import search_relevant_summaries, search_relevant_facts
            ctx["semantic_summaries"] = search_relevant_summaries(message, chat_id, n_results=3)
            ctx["semantic_facts"] = search_relevant_facts(message, n_results=5)
        except Exception as exc:
            logger.debug("[context_builder] semantic search skipped: %s", exc)

    return ctx


def format_for_voice(ctx: Dict[str, Any]) -> str:
    """Format context package as injected text for Gemini Live system prompt."""
    parts: List[str] = []

    ss = ctx.get("session_state") or {}
    state_parts: List[str] = []
    if ss.get("last_mood") and ss["last_mood"] not in ("neutral",):
        state_parts.append(f"مزاجه الأخير: {ss['last_mood']}")
    if ss.get("last_platform"):
        state_parts.append(f"آخر منصة: {ss['last_platform']}")
    if ss.get("recent_topics"):
        state_parts.append("مواضيع أخيرة: " + "، ".join(ss["recent_topics"][-3:]))
    if state_parts:
        parts.append("[حالة المستخدم: " + " | ".join(state_parts) + "]")

    if ctx.get("semantic_summaries"):
        parts.append("[ملخصات ذات صلة: " + " | ".join(ctx["semantic_summaries"][:2]) + "]")
    if ctx.get("semantic_facts"):
        parts.append("[معلومات ذات صلة: " + " | ".join(ctx["semantic_facts"][:3]) + "]")
    if ctx.get("persona_directives"):
        parts.append(ctx["persona_directives"])

    turns = ctx.get("stm_turns") or []
    if turns:
        formatted: List[str] = []
        for m in turns[-10:]:
            role = "نبيل" if m.get("role") == "user" else "Sandy"
            content = m.get("content", "")
            if content:
                formatted.append(f"{role}: {content}")
        if formatted:
            parts.append("\nآخر المحادثات عبر المنصات:\n" + "\n".join(formatted))

    return "\n".join(parts)


def get_persona_directives(chat_id: str, user_id: str, mongo_db) -> Optional[str]:
    """
    Fetch style + preferences + relationships + lessons + summaries from MongoDB.

    Labels:
      style_memory / preferences / user_fact → preference | content
      relationship                            → relation + name
      lesson_learned                          → lesson
      conversation_summary                    → summary
    """
    if mongo_db is None or not chat_id:
        return None
    try:
        docs = list(mongo_db["sandy_memories"].find(
            {
                "chat_id": str(chat_id),
                "label": {"$in": [
                    "style_memory", "preferences", "user_fact",
                    "relationship", "lesson_learned", "conversation_summary",
                ]},
            },
            {"_id": 0},
            sort=[("created_at", -1)],
            limit=25,
        ))
    except Exception:
        return None

    if not docs:
        return None

    from app.agent.ltm_crypto import decrypt_field
    prefs: List[str] = []
    rels: List[str] = []
    lessons: List[str] = []
    summaries: List[str] = []

    for d in docs:
        label = d.get("label")
        if label in ("style_memory", "preferences", "user_fact"):
            text = d.get("preference") or d.get("content")
            if text:
                prefs.append(decrypt_field(str(text)))
        elif label == "relationship":
            if d.get("relation") and d.get("name"):
                rels.append(f"{d['relation']} {d['name']}")
        elif label == "lesson_learned":
            if d.get("lesson"):
                lessons.append(decrypt_field(str(d["lesson"])))
        elif label == "conversation_summary":
            if d.get("summary"):
                summaries.append(str(d["summary"]))

    blocks: List[str] = []
    if summaries:
        blocks.append("[ملخصات محادثات سابقة: " + " | ".join(summaries[:3]) + "]")
    if prefs:
        blocks.append("[تفضيلات: " + " | ".join(prefs[:5]) + "]")
    if rels:
        blocks.append("[علاقات: " + " · ".join(rels[:8]) + "]")
    if lessons:
        blocks.append("[دروس سابقة: " + " | ".join(lessons[:3]) + "]")
    return "\n".join(blocks) if blocks else None
