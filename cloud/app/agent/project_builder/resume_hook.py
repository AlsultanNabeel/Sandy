"""Resume hook — pre-graph middleware for Project Builder `waiting_user` tasks.

When a Project Builder task is paused (mark_waiting_user), the Worker dyno blocks
until a Redis flag is set. This module is the WEB-side counterpart: before any
LangGraph processing, we check whether the current chat has a paused task
awaiting reply.

M4 expanded the response space from 3 → 4 nuances:
  • agree   → signal_resume (existing)
  • cancel  → mark FAILED   (existing)
  • question → open a PENDING_FIX_DISCUSSION; worker stays blocked while
              Sandy and the owner discuss via pending_node
  • propose → same as question, but the owner's text seeds the alternative
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.agent.project_builder import task_state

logger = logging.getLogger(__name__)


# Common Arabic + English agreement tokens — short, exact-match style
_AGREE_TOKENS = {
    "اه",
    "أيوا",
    "ايوا",
    "ايوة",
    "اوكي",
    "أوكي",
    "ok",
    "okay",
    "نعم",
    "موافق",
    "اتفقنا",
    "تمام",
    "yes",
    "y",
    "go",
    "اعمل",
    "اعملي",
    "كمل",
    "كملي",
}

_CANCEL_TOKENS = {
    "لا",
    "no",
    "n",
    "وقف",
    "وقفي",
    "الغي",
    "ألغي",
    "cancel",
    "stop",
    "خلاص",
}

# كلمات تخلي الرسالة سؤال — تفتح نقاش بدل ما تتطبّق كـ patch
_QUESTION_MARKERS = {
    "ليش",
    "لماذا",
    "كيف",
    "ايش",
    "إيش",
    "شو",
    "متى",
    "وين",
    "هل",
    "why",
    "how",
    "what",
    "when",
    "where",
}


def _classify_intent(message: str) -> str:
    """Return 'agree' | 'cancel' | 'question' | 'propose'.

    • agree    — short approval token (or short message containing one)
    • cancel   — short rejection
    • question — contains a question marker (؟ / ? / ليش / كيف / شو ...)
                 The owner wants to discuss before deciding.
    • propose  — everything else longer than 3 words. The owner is suggesting
                 an alternative plan.
    """
    text = (message or "").strip().lower()
    if not text:
        return "propose"  # empty → treat as something needs discussion
    if text in _AGREE_TOKENS:
        return "agree"
    if text in _CANCEL_TOKENS:
        return "cancel"
    if len(text.split()) <= 3:
        words = set(text.split())
        if words & _AGREE_TOKENS:
            return "agree"
        if words & _CANCEL_TOKENS:
            return "cancel"

    # Question? ends with ? or ؟, or contains a question marker word
    if "؟" in text or "?" in text:
        return "question"
    first_word = text.split()[0] if text.split() else ""
    if first_word in _QUESTION_MARKERS or any(
        f" {m} " in f" {text} " for m in _QUESTION_MARKERS
    ):
        return "question"

    return "propose"


def try_handle_resume(
    message: str,
    chat_id: Any,
    *,
    session: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """If chat has a `waiting_user` Project Builder task, intercept the message.

    Returns a response dict ready to send back, or None to let the normal
    graph handle the message.

    Response dict shape (matches telegram_handlers._graph_respond):
        {"text": str, "reply_markup": None, "image_bytes": None, "caption": ""}

    Behavior depends on classified intent:
        agree    → signal_resume; worker continues with existing plan
        cancel   → set FAILED
        question → open PENDING_FIX_DISCUSSION; worker stays blocked while
                   the owner and Sandy discuss
        propose  → same as question, but seed the alternative plan with the
                   owner's text
    """
    if not chat_id:
        return None

    task_id = task_state.find_waiting_task_for_chat(chat_id)
    if not task_id:
        return None

    task = task_state.get_task(task_id)
    if not task:
        return None

    if task.get("status") != task_state.STATUS_WAITING_USER:
        # Stale index — clean up
        from app.agent.project_builder import _redis as sa_redis
        client = sa_redis.get_client()
        if client is not None:
            try:
                client.delete(sa_redis.k_waiting_user(chat_id))
            except Exception:
                pass
        return None

    intent = _classify_intent(message)
    where = task.get("where_we_stopped") or "—"

    if intent == "cancel":
        task_state.set_status(
            task_id,
            task_state.STATUS_FAILED,
            where_we_stopped=f"ألغاه المستخدم: {message[:200]}",
        )
        from app.agent.project_builder import _redis as sa_redis
        client = sa_redis.get_client()
        if client is not None:
            try:
                client.delete(sa_redis.k_waiting_user(chat_id))
            except Exception:
                pass
        return _reply(
            f"تمام، ألغيت task `{task_id}`. وقفنا عند: {where[:200]}"
        )

    if intent == "agree":
        signaled = task_state.signal_resume(task_id, agreed_solution="")
        if not signaled:
            return _reply(
                "ما قدرت أوصل الإشارة للـ Worker الآن (Redis مش متاح). جرّب بعد دقيقة."
            )
        logger.info(
            "[resume_hook] resumed task=%s intent=agree chat=%s",
            task_id,
            chat_id,
        )
        return _reply(
            f"تمام، كمّلت على task `{task_id}`. حأخبرك أول ما ينتهي CI."
        )

    # For a project-builder task waiting on PLAN review, a "propose" message
    # (free-text revision like "بدي vanilla بدل React") must be routed to
    # the worker as a re-plan request — NOT dropped into the chat graph
    # where the maestro will treat it as a fresh conversation and the
    # pending task gets orphaned. The worker reads `plan_revision_text`
    # right after `wait_for_resume` and regenerates the plan with those
    # constraints baked in.
    if (
        intent == "propose"
        and (task.get("type") or "") == task_state.TYPE_PROJECT_BUILDER
        and (task.get("stage") or "") == task_state.STAGE_PROJECT_PLAN_REVIEW
    ):
        task_state.save_plan_revision_request(task_id, message)
        signaled = task_state.signal_resume(task_id, agreed_solution="")
        if not signaled:
            return _reply(
                "ما قدرت أوصل تعديلك للـ Worker الآن (Redis مش متاح). جرّب بعد دقيقة."
            )
        logger.info(
            "[resume_hook] revised task=%s chat=%s len=%d",
            task_id, chat_id, len(message or ""),
        )
        return _reply(
            f"تمام، أرسلت تعديلاتك للـ planner. حأبعتلك الخطة المحدّثة "
            f"بعد ثواني (task `{task_id}`)."
        )

    # Other intents — questions, or proposals on non-plan-review tasks —
    # still fall through to the chat graph for manual discussion.
    if intent in {"question", "propose"}:
        return None

    return None


def _reply(text: str) -> Dict[str, Any]:
    return {
        "text": text,
        "reply_markup": None,
        "image_bytes": None,
        "caption": "",
    }
