"""response_node: يبني الرد النهائي.

آخر node بالـ graph. يجمع execution_result.reply مع persona_snippet
و response_template، ويطلّع final_response جاهز للإرسال عبر Telegram.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.agent.graph.state import SandyState, merge_state
from app.agent.soul_vault import get_apology
from app.agent import tool_health

logger = logging.getLogger(__name__)

_FALLBACK_REPLY = "آسفة، حصل خطأ غير متوقع. جرّب مرة ثانية."

_CHAT_TOOLS = frozenset({
    "chat_respond", "chat_emotional", "ask_clarification",
    "request_confirmation", "pending_confirm", "pending_reject", "pending_select",
})


def _degradation_disclosure(tool_name: str) -> str:
    """نص تنبيه قصير يتقدّم الرد لو الـ tool اللي نفّذناه متعثّر حالياً.
    يرجع "" لما ما في داعي للتنبيه."""
    if not tool_name or tool_name in _CHAT_TOOLS:
        return ""
    snap = tool_health.get_health(tool_name)
    if snap.get("status") != "degraded":
        return ""
    n = snap.get("n_calls", 0)
    failures = max(0, n - round(snap.get("success_rate", 1.0) * n))
    return (
        f"⚠️ تنبيه: `{tool_name}` عندي مشاكل مؤقتة "
        f"({failures} فشل من آخر {n} محاولة). "
        "جرّبت أعمل اللي طلبته، بس لو بان ناقص خبّرني وأعيد.\n"
    )


def _build_final_text(
    reply: str,
    persona_snippet: Optional[str],
    response_template: Optional[str],
) -> str:
    """يدمج الـ reply مع persona و template."""
    if not reply:
        return persona_snippet or _FALLBACK_REPLY

    parts = []
    if response_template and len(reply) < 30 and response_template not in reply:
        parts.append(response_template)
    parts.append(reply)
    return "\n".join(parts).strip()


def response_node(state: SandyState) -> SandyState:
    """LangGraph node: يبني الرد النهائي من نتائج الـ nodes السابقة.

    الأولوية:
    1. final_response موجود مسبقاً → استخدمه مباشرة
    2. execution_result.reply → ادمجه مع persona/template
    3. persona_snippet → ردّ مؤدب بدون تنفيذ
    4. fallback → رسالة خطأ آمنة
    """
    final = state.get("final_response") or ""
    execution = state.get("execution_result") or {}
    reply = str(execution.get("reply") or "").strip()
    reply_markup = execution.get("reply_markup")
    persona_snippet = str(state.get("persona_snippet") or "").strip()
    response_template = str(state.get("response_template") or "").strip()

    function_call_name = (state.get("function_call") or {}).get("name", "")

    if final:
        text = final
    elif reply:
        text = _build_final_text(
            reply, persona_snippet or None, response_template or None
        )
        if (
            execution.get("handled")
            and function_call_name
            and function_call_name not in _CHAT_TOOLS
            and "✅" not in text
        ):
            text += " ✅"
        # تنبيه تعثّر الـ tool. ما نضيفه لو الرد أصلاً فيه تحذير صريح.
        disclosure = _degradation_disclosure(function_call_name)
        if disclosure and "⚠️" not in text:
            text = disclosure + text
    else:
        # B3: اعتذار مناسب للمود بدل رسالة خطأ جامدة
        mood = state.get("mood") or "neutral"
        text = get_apology(mood)
        logger.warning(f"[response_node] no reply — apology sent (mood={mood})")

    return merge_state(
        state,
        {
            "final_response": text,
            "execution_result": {
                **execution,
                "reply": text,
                "reply_markup": reply_markup,
                "source": execution.get("source", "response_node"),
                "final": True,
            },
        },
    )
