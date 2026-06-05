"""Cooperative shutdown flag for the Self-Coding Worker.

Heroku sends SIGTERM during a redeploy and gives the dyno ~30s before SIGKILL.
The Worker's polling loops (CI wait, owner-resume wait) can block far longer
than that, so they consult this flag via `interruptible_sleep` and break out
early when set. The orchestrator then re-enqueues the in-flight task with
enough saved state for the next Worker boot to resume in-place.
"""

from __future__ import annotations

import time

_shutdown_requested = False


def request_shutdown() -> None:
    global _shutdown_requested
    _shutdown_requested = True


def is_shutdown_requested() -> bool:
    return _shutdown_requested


def reset() -> None:
    """Test-only: clear the flag between unit tests."""
    global _shutdown_requested
    _shutdown_requested = False


def interruptible_sleep(seconds: float, *, tick: float = 1.0) -> bool:
    """Sleep up to `seconds`, returning early if shutdown is requested.

    Returns True if the full sleep completed, False if interrupted.
    """
    if seconds <= 0:
        return not _shutdown_requested
    deadline = time.monotonic() + seconds
    while True:
        if _shutdown_requested:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(tick, remaining))
