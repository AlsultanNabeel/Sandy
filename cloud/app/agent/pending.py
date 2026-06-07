from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4
from app.utils.time import USER_TZ

PENDING_ACTION_TTL_MINUTES = 10


def _now_iso() -> str:
    return datetime.now(USER_TZ).isoformat()


def create_pending_action(
    payload: Dict[str, Any], *, ttl_minutes: int = PENDING_ACTION_TTL_MINUTES
) -> Dict[str, Any]:
    pending = dict(payload or {})
    now = datetime.now(USER_TZ)
    pending["created_at"] = pending.get("created_at") or now.isoformat()
    pending["expires_at"] = (
        pending.get("expires_at") or (now + timedelta(minutes=ttl_minutes)).isoformat()
    )
    pending["nonce"] = pending.get("nonce") or uuid4().hex
    pending["consumed_at"] = pending.get("consumed_at") or ""
    return pending


def get_valid_pending_action(
    session: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the live pending action, or None.

    Note: this is not a pure read. When the stored action is missing, expired,
    or already consumed it clears `session["pending_action"]` as a side effect,
    so don't call it if you only want to peek.
    """
    if not isinstance(session, dict):
        return None

    pending = session.get("pending_action")
    if not isinstance(pending, dict):
        session["pending_action"] = None
        return None

    if pending.get("consumed_at"):
        session["pending_action"] = None
        return None

    expires_at = str(pending.get("expires_at", "") or "").strip()
    if not expires_at:
        session["pending_action"] = None
        return None

    try:
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=USER_TZ)
        else:
            expires_dt = expires_dt.astimezone(USER_TZ)
    except Exception:
        session["pending_action"] = None
        return None

    if expires_dt <= datetime.now(USER_TZ):
        session["pending_action"] = None
        return None

    return pending


def clear_pending_action(session: Optional[Dict[str, Any]]) -> None:
    if isinstance(session, dict):
        session["pending_action"] = None


def consume_pending_action(session: Optional[Dict[str, Any]]) -> None:
    if isinstance(session, dict) and isinstance(session.get("pending_action"), dict):
        session["pending_action"]["consumed_at"] = _now_iso()
