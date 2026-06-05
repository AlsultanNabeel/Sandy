"""Helpers for persisting unexpected errors to MongoDB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import traceback
from app.utils import trace as trace_ctx
from app.utils import metrics as metrics


def log_unhandled_exception(
    mongo_db: Any,
    exc: BaseException,
    *,
    chat_id: Optional[Any] = None,
    source: str = "unknown",
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Store an exception report in MongoDB when persistence is available."""

    if mongo_db is None:
        return False

    payload: Dict[str, Any] = {
        "source": source,
        "chat_id": chat_id,
        "timestamp": datetime.now(timezone.utc),
        "exception_type": exc.__class__.__name__,
        "message": str(exc),
        "stack_trace": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    }
    if extra:
        payload["extra"] = extra

    # Include trace id if present in thread-local context to help tracing.
    try:
        _trace = trace_ctx.get_trace_id()
        if _trace:
            payload.setdefault("extra", {})["trace_id"] = _trace
    except Exception:
        pass

    try:
        mongo_db["sandy_error_logs"].insert_one(payload)
        try:
            metrics.inc_error_log_success()
        except Exception:
            pass
        return True
    except Exception as log_error:
        try:
            metrics.inc_error_log_failure()
        except Exception:
            pass
        print(f"[ErrorTracking] Failed to persist exception: {log_error}")
        return False
