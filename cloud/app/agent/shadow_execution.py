"""Stash a draft in the session and ask the user to confirm before running it."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict
from uuid import uuid4

from app.utils.time import USER_TZ

SESSION_KEY = "shadow_draft"
_TTL_MINUTES = 30


def _now() -> datetime:
    return datetime.now(USER_TZ)


def _expires_at() -> str:
    return (_now() + timedelta(minutes=_TTL_MINUTES)).isoformat()


def handle_shadow_draft_action(
    params: Dict[str, Any],
    session: Dict[str, Any],
) -> Dict[str, Any]:
    draft_type = str(params.get("draft_type", "general") or "general").strip()
    preview = str(params.get("preview", "") or "").strip()
    confirm_action = params.get("confirm_action") or {}

    if not preview and not confirm_action:
        return {"handled": False, "reply": ""}

    session[SESSION_KEY] = {
        "id": uuid4().hex[:8],
        "draft_type": draft_type,
        "preview": preview,
        "confirm_action": (
            dict(confirm_action) if isinstance(confirm_action, dict) else {}
        ),
        "created_at": _now().isoformat(),
        "expires_at": _expires_at(),
    }

    return {"handled": True, "reply": _format_confirmation_prompt(draft_type, preview)}


# Formatting

_DRAFT_TYPE_LABELS = {
    "email_draft": "مسودة إيميل",
    "reply_draft": "مسودة رد إيميل",
    "task_plan": "خطة مهام",
    "general": "مسودة",
}


def _format_confirmation_prompt(draft_type: str, preview: str) -> str:
    label = _DRAFT_TYPE_LABELS.get(draft_type, "مسودة")
    parts = [f"جهّزت {label} — تبيني أنفّذها؟"]
    if preview:
        parts.append(f"\n📝 *المعاينة:*\n{preview}")
    parts.append("\n_(اكتب نعم للتنفيذ، لا للإلغاء)_")
    return "\n".join(parts)
