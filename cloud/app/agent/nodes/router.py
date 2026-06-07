"""router_node: يحدّد الـ complexity ويختار الـ node التالي.

route_after_router هو اللي يقرر الوجهة (pending / clarify / execute).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent.graph.state import SandyState, merge_state

logger = logging.getLogger(__name__)


def _is_valid_pending(pending: Any) -> bool:
    """pending صالح فقط لو عنده type أو action أو confirmation_status."""
    if not pending or not isinstance(pending, dict):
        return False
    return bool(
        pending.get("type")
        or pending.get("action")
        or pending.get("confirmation_status")
    )


def route_after_router(state: SandyState) -> str:
    """Conditional edge — يحدد الـ node التالي.

    ترتيب الأولوية:
    1. pending نشط + routing_hint != execute_direct → pending_node
    2. requires_clarification → clarify_node
    3. الباقي → execute_node
    """
    pending = state.get("pending_state")
    routing_hint = state.get("routing_hint") or ""
    intent = state.get("intent") or ""

    # اختيار من اقتراحات تقويم → execute دائماً (يتجاوز pending)
    if intent == "calendar.pick_suggestion":
        return "execute_node"

    # pending نشط: إذا كان نوعه "انتظار إدخال" → pending دائماً بغض النظر عن routing_hint
    if pending and isinstance(pending, dict):
        _paction = str(pending.get("action") or "")
        if (
            _paction.startswith("await_")
            or pending.get("confirmation_status") == "clarification"
        ):
            return "pending_node"

        # confirmation pending + رد قصير اه/لا → pending_node حتى لو ماستيرو حوّل لـ execute_direct.
        # Why: maestro أحياناً يفسّر "اه" كأمر جديد مطابق للأصلي → يعيد إنشاء نفس الـ pending → loop.
        if pending.get("confirmation_status") == "pending":
            from app.agent.executor.helpers import _is_quick_confirmation, is_cancellation

            msg = state.get("message") or ""
            if _is_quick_confirmation(msg) or is_cancellation(msg):
                return "pending_node"

    # pending نشط وما في أمر مباشر صريح
    if pending and routing_hint != "execute_direct":
        return "pending_node"

    if state.get("requires_clarification"):
        return "clarify_node"

    return "execute_node"


def router_node(state: SandyState) -> SandyState:
    """LangGraph node: يضبط routing_hint وينظّف pending_state الفاسد.

    الـ routing نفسه يتم عبر route_after_router() كـ conditional edge.
    """
    try:
        updates: dict = {
            "routing_hint": state.get("routing_hint") or "execute_direct",
        }
        # امسح pending_state الناقص/الفاسد — يمنع crash في pending_node
        pending = state.get("pending_state")
        if pending is not None and not _is_valid_pending(pending):
            logger.warning(
                f"[router_node] stale/invalid pending_state cleared: {pending}"
            )
            updates["pending_state"] = None
        return merge_state(state, updates)
    except Exception as exc:
        logger.error(f"[router_node] failed: {exc}")
        try:
            from app.integrations.sentry_config import capture_exception

            capture_exception(exc, context={"node": "router_node"})
        except Exception:
            pass
        return state
