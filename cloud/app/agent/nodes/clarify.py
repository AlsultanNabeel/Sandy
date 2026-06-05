"""clarify_node: يرسل سؤال توضيحي لما requires_clarification=True.

يحفظ السياق كـ pending_state لحد ما المستخدم يجاوب.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict
import uuid

from app.agent.graph.state import SandyState, merge_state

logger = logging.getLogger(__name__)

_CLARIFY_TTL_MINUTES = 10


def _build_clarify_pending(state: SandyState) -> Dict[str, Any]:
    """يبني pending_state لانتظار إجابة التوضيح."""
    now = datetime.now(timezone.utc)
    return {
        "type": "clarification",
        "action": "await_clarification",
        "pending_id": str(uuid.uuid4()),
        "nonce": str(uuid.uuid4()),
        "expires_at": (now + timedelta(minutes=_CLARIFY_TTL_MINUTES)).isoformat(),
        "consumed_at": "",
        "created_at": now.isoformat(),
        "original_message": state.get("message", ""),
        "intent": state.get("intent", ""),
    }


def clarify_node(state: SandyState) -> SandyState:
    """LangGraph node: يُرجع سؤال توضيحي ويحفظ السياق كـ pending.

    يُستدعى فقط عندما requires_clarification=True.
    """
    question = state.get("clarification_question") or ""

    if not question:
        logger.warning(
            "[clarify_node] no clarification_question in state — using fallback"
        )
        question = "ممكن توضّح أكثر؟"

    pending = _build_clarify_pending(state)

    return merge_state(
        state,
        {
            "pending_state": pending,
            "final_response": question,
            "execution_result": {
                "handled": True,
                "reply": question,
                "reply_markup": None,
                "source": "clarify_node",
            },
        },
    )
