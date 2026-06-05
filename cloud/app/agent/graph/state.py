"""SandyState — قلب الـ LangGraph.

TypedDict موحد يمر عبر كل nodes في الـ graph.
كل node يقرأ منه ويضيف إليه — لا global variables.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional, TypedDict


class SandyState(TypedDict):
    # أساسي
    message: str
    user_id: str
    chat_id: str
    session_id: str

    # الذاكرة
    conversation_history: List[dict]  # STM: آخر 10 رسائل (Redis)
    stm_redis_key: Optional[str]

    # قرار الـ router
    intent: Optional[str]
    confidence: Optional[float]
    complexity: Optional[str]  # simple / complex
    mood: Optional[str]
    urgency: Optional[str]  # low / medium / high
    requires_clarification: bool
    routing_hint: Optional[str]
    clarification_question: Optional[str]

    # الـ persona
    persona_intensity: Optional[str]  # minimal/standard/empathetic/playful/formal
    persona_snippet: Optional[str]

    # pending
    pending_state: Optional[dict]
    pending_archived: Optional[List[dict]]

    # function call
    function_call: Optional[dict]        # {"name": str, "args": dict} من maestro FC
    function_calls: Optional[List[dict]] # [{"name": str, "args": dict}, ...] multi-tool

    # التنفيذ
    execution_result: Optional[Any]
    response_template: Optional[str]
    final_response: Optional[str]

    # تمرير من الـ session
    image_state: Optional[dict]  # image bytes + history من agent.session
    gmail_list_state: Optional[dict]  # gmail_last_list للردود الموضعية

    # تحسين النداء الواحد
    chat_reply: Optional[str]      # رد دردشة inline من maestro (يوفر نداء LLM ثاني)

    # soul prefetch
    soul_prefetch: Optional[Any]  # dict of Future objects started before routing

    # meta
    error: Optional[str]
    source: Optional[str]  # user / proactive / hardware
    created_at: Optional[str]


def create_initial_state(
    message: str,
    user_id: str,
    chat_id: str,
    source: str = "user",
    pending_state: Optional[dict] = None,
    image_state: Optional[dict] = None,
    gmail_list_state: Optional[dict] = None,
) -> SandyState:
    """أنشئ SandyState فارغة جاهزة لـ graph.ainvoke().

    Args:
        message: نص رسالة المستخدم
        user_id: معرف المستخدم في Telegram
        chat_id: معرف المحادثة في Telegram
        source: مصدر الرسالة (user / proactive / hardware)
        pending_state: حالة pending موجودة مسبقاً (اختياري)
    """
    return SandyState(
        message=message,
        user_id=str(user_id),
        chat_id=str(chat_id),
        session_id=str(uuid.uuid4()),
        conversation_history=[],
        stm_redis_key=f"stm:{chat_id}:{user_id}",
        intent=None,
        confidence=None,
        complexity=None,
        mood=None,
        urgency=None,
        requires_clarification=False,
        routing_hint=None,
        clarification_question=None,
        persona_intensity=None,
        persona_snippet=None,
        pending_state=pending_state,
        pending_archived=[],
        function_call=None,
        function_calls=None,
        execution_result=None,
        response_template=None,
        final_response=None,
        chat_reply=None,
        error=None,
        source=source,
        created_at=datetime.utcnow().isoformat(),
        image_state=image_state,
        gmail_list_state=gmail_list_state,
        soul_prefetch=None,
    )


def merge_state(base: SandyState, updates: dict) -> SandyState:
    """ادمج updates في base state — كل node يستدعيه.

    يستخدم TypedDict merge آمن بدل dict.update() المباشر.
    """
    merged = dict(base)
    merged.update(updates)
    return SandyState(**merged)
