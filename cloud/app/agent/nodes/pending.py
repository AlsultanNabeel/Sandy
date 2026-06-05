"""pending_node: ينفّذ الـ pending action الحالي.

Wrapper يحوّل SandyState للـ session القديمة ويستدعي execute_pending_action().

الحالات: confirmation (نعم/لا)، clarification (إجابة سؤال)،
selection (اختيار رقم)، destructive (تأكيد قبل حذف)،
conflict_resolution (اختيار slot تقويم).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.agent.graph.state import SandyState, merge_state
from app.agent.executor.pending_execution import execute_pending_action
from app.utils.session import build_session_from_state as _build_session_from_state

logger = logging.getLogger(__name__)



def _extract_results_from_session(
    session: Dict[str, Any],
    original_pending: Dict[str, Any],
) -> Dict[str, Any]:
    """يستخرج pending_state المحدث وarchived من الـ session بعد التنفيذ."""
    new_pending = session.get("pending_action")
    # consumed pending → treat as cleared so it doesn't leak into next request
    if isinstance(new_pending, dict) and new_pending.get("consumed_at"):
        new_pending = None
    archived = session.get("archived_pending") or []

    # إذا الـ pending اتغير → ابنِ archived list محدث
    if isinstance(archived, dict):
        archived = [archived]

    return {
        "pending_state": new_pending,
        "pending_archived": archived,
    }


def _noop_save(*args, **kwargs) -> None:
    """save_session_fn وهمية — LangGraph يتولى الـ persistence."""
    pass


_INTENT_TO_PENDING_RESPONSE = {
    "pending.confirm": "confirm",
    "pending.reject": "reject",
    "pending.select_option": "confirm",
}


def pending_node(state: SandyState) -> SandyState:
    """LangGraph node: ينفذ الـ pending action الحالي.

    يستدعي execute_pending_action() الموجودة عبر session مؤقت.
    """

    original_pending = state.get("pending_state") or {}

    # clarification await: المستخدم أجاب على سؤال توضيحي
    if (
        original_pending.get("type") == "clarification"
        and original_pending.get("action") == "await_clarification"
    ):
        original_message = str(original_pending.get("original_message") or "").strip()
        user_answer = str(state.get("message") or "").strip()
        combined = (
            f"{original_message} — {user_answer}" if original_message else user_answer
        )

        # أعد الـ state بدون pending وبالرسالة المدمجة
        new_state = merge_state(
            state,
            {
                "message": combined,
                "pending_state": None,
                "requires_clarification": False,
                "routing_hint": "execute_direct",
            },
        )

        try:
            from app.agent.nodes.execute import execute_node

            return execute_node(new_state)
        except Exception as exc:
            logger.error(f"[pending_node] clarification re-execute failed: {exc}")
            return merge_state(
                state,
                {
                    "pending_state": None,
                    "final_response": "حصل خطأ، حاول مرة ثانية.",
                    "execution_result": {
                        "handled": False,
                        "reply": "حصل خطأ، حاول مرة ثانية.",
                        "source": "pending_node_clarify",
                    },
                },
            )

    session = _build_session_from_state(state)

    # Map Gemini intent → response_intent to skip regex classifier
    intent_hint = _INTENT_TO_PENDING_RESPONSE.get(state.get("intent") or "", "")

    try:
        mongo_db = None
        create_chat_completion_fn = None
        try:
            import app.agent.executor.deps as deps

            mongo_db = getattr(deps, "mongo_db", None)
        except Exception:
            pass
        try:
            from app.agent.nodes.execute import _get_chat_completion_fn
            create_chat_completion_fn = _get_chat_completion_fn()
        except Exception:
            pass

        result = execute_pending_action(
            user_message=state["message"],
            session=session,
            session_file=None,
            mongo_db=mongo_db,
            tasks_file=None,
            save_session_fn=_noop_save,
            create_chat_completion_fn=create_chat_completion_fn,
            intent_hint=intent_hint,
        )

    except Exception as exc:
        logger.error(f"[pending_node] execute_pending_action failed: {exc}")
        try:
            from app.integrations.sentry_config import capture_exception

            capture_exception(exc, context={"node": "pending_node"})
        except Exception:
            pass
        result = {"handled": False, "reply": "حصل خطأ، حاول مرة ثانية."}

    handled = result.get("handled", False)
    reply = result.get("reply") or ""
    reply_markup = result.get("reply_markup")

    pending_updates = _extract_results_from_session(session, original_pending)

    # Add confirm/reject buttons when clarification resolves to a confirmation step
    new_pending = pending_updates.get("pending_state")
    if (
        new_pending
        and isinstance(new_pending, dict)
        and new_pending.get("confirmation_status") == "pending"
        and reply_markup is None
    ):
        try:
            import telebot.types as tgtypes

            markup = tgtypes.InlineKeyboardMarkup()
            markup.row(
                tgtypes.InlineKeyboardButton("✅ نعم", callback_data="confirm_yes"),
                tgtypes.InlineKeyboardButton("❌ لا", callback_data="confirm_no"),
            )
            reply_markup = markup
        except Exception:
            pass

    updates: Dict[str, Any] = {
        **pending_updates,
        "execution_result": {
            "handled": handled,
            "reply": reply,
            "reply_markup": reply_markup,
            "source": "pending_node",
        },
    }

    if reply:
        updates["final_response"] = reply

    return merge_state(state, updates)
