"""session.py — بناء session مؤقت من SandyState للـ handlers."""
from typing import Any, Dict

from app.agent.graph.state import SandyState


def build_session_from_state(state: SandyState) -> Dict[str, Any]:
    """يبني session مؤقت من SandyState للـ handlers والـ pending execution."""
    pending = state.get("pending_state") or {}
    session: Dict[str, Any] = {
        "pending_action": pending if pending else None,
        "archived_pending": state.get("pending_archived") or [],
        "user_id": state.get("user_id"),
        "chat_id": state.get("chat_id"),
        "messages": state.get("conversation_history") or [],
    }
    image_state = state.get("image_state")
    if image_state:
        session["image_state"] = image_state
        session["last_image_bytes"] = image_state.get("active_image_bytes")
    gmail_list = state.get("gmail_list_state")
    if gmail_list:
        session["gmail_last_list"] = gmail_list
    return session
