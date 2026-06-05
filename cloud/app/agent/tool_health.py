"""In-memory health tracker for the FC tools (M6a).

Keeps a per-tool ring buffer of the last `_WINDOW` calls (success and
latency) so the rest of the agent can tell a registered-but-flaky tool
apart from one that doesn't exist, and warn the owner when a tool's
recent failure rate crosses `_DEGRADED_FAILURE_RATE`.

In-memory on purpose. It resets on dyno restart. Redis would give us
cross-restart continuity but one restart flap would skew the numbers,
and what we want is whether the tool is flaky right now.

Every public function takes `_lock` because dispatcher callbacks can
fire from worker threads (sandy_executor), not just the main thread.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

_WINDOW = 20  # recent calls kept per tool
_DEGRADED_MIN_CALLS = 3   # fewer calls than this and we don't call it degraded, too noisy
_DEGRADED_FAILURE_RATE = 0.4  # 40% or more failures in the window means degraded

_lock = threading.Lock()
# tool_name -> deque[{"ok": bool, "ms": float, "ts": float, "error": str|None}]
_history: Dict[str, Deque[Dict[str, Any]]] = {}


def record_call(
    tool_name: str,
    *,
    ok: bool,
    latency_ms: float,
    error: Optional[str] = None,
) -> None:
    """Append one outcome to the tool's ring buffer. Best-effort; the
    dispatcher already wraps this in a try/except so errors here stay put."""
    if not tool_name:
        return
    entry: Dict[str, Any] = {
        "ok": bool(ok),
        "ms": float(latency_ms),
        "ts": time.time(),
    }
    if error and not ok:
        entry["error"] = str(error)[:200]
    with _lock:
        buf = _history.get(tool_name)
        if buf is None:
            buf = deque(maxlen=_WINDOW)
            _history[tool_name] = buf
        buf.append(entry)


def get_health(tool_name: str) -> Dict[str, Any]:
    """Return a snapshot of a single tool's recent behavior.

    Shape:
        {
            tool: "name",
            n_calls: int,
            success_rate: float (0..1),
            avg_latency_ms: float,
            last_seen_ts: float | None,
            last_error: str | None,
            status: "ok" | "degraded" | "unknown",
        }
    """
    with _lock:
        buf = _history.get(tool_name)
        entries = list(buf) if buf else []

    if not entries:
        return {
            "tool": tool_name,
            "n_calls": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 0.0,
            "last_seen_ts": None,
            "last_error": None,
            "status": "unknown",
        }
    n = len(entries)
    n_ok = sum(1 for e in entries if e.get("ok"))
    success_rate = n_ok / n if n else 1.0
    avg_latency = sum(e.get("ms", 0.0) for e in entries) / n if n else 0.0
    last = entries[-1]
    last_error = next(
        (
            e.get("error")
            for e in reversed(entries)
            if not e.get("ok") and e.get("error")
        ),
        None,
    )
    degraded = (
        n >= _DEGRADED_MIN_CALLS
        and (1.0 - success_rate) >= _DEGRADED_FAILURE_RATE
    )
    return {
        "tool": tool_name,
        "n_calls": n,
        "success_rate": round(success_rate, 3),
        "avg_latency_ms": round(avg_latency, 1),
        "last_seen_ts": last.get("ts"),
        "last_error": last_error,
        "status": "degraded" if degraded else "ok",
    }


def reset() -> None:
    """Test-only: drop all tracked history."""
    with _lock:
        _history.clear()
