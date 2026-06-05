"""SA5: github_ci_status — workflow run status filtered by commit_sha + polling.

The plan specifies:
- Filter runs by HEAD SHA so we never read a stale run from a different commit.
- Wait 60s before first poll (let GitHub register the run).
- Poll every 30s.
- Total budget: 15 minutes.

When called from the Worker (in `wait_for_ci`), each poll is logged so the
owner sees progress via Telegram.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from app.agent.self_coding import shutdown as sa_shutdown
from app.integrations import github_api

logger = logging.getLogger(__name__)

_INITIAL_DELAY_SECS = 60
_POLL_INTERVAL_SECS = 30
_DEFAULT_TIMEOUT_SECS = 15 * 60

_TERMINAL_CONCLUSIONS = {
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "neutral",
    "skipped",
    "stale",
}

_FAILURE_CONCLUSIONS = {"failure", "timed_out", "action_required"}


def github_ci_status(
    commit_sha: str,
    *,
    branch: Optional[str] = None,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a summary of workflow runs for `commit_sha`.

    Returns:
        {
          ok: bool,
          state: 'pending' | 'in_progress' | 'success' | 'failure' | 'no_runs',
          runs: [...],
          worst_conclusion: str | None,
          error?: str,
        }
    """
    if not commit_sha:
        return {
            "ok": False,
            "state": "no_runs",
            "runs": [],
            "error": "commit_sha فاضي",
        }

    api = github_api.list_workflow_runs_for_commit(
        commit_sha,
        branch=branch,
        repo=repo,
    )
    if not api.get("ok"):
        return {
            "ok": False,
            "state": "no_runs",
            "runs": [],
            "error": api.get("error") or "ci api failed",
        }

    runs = api.get("runs") or []
    if not runs:
        return {"ok": True, "state": "no_runs", "runs": [], "worst_conclusion": None}

    # Aggregate state
    not_done = [r for r in runs if r.get("status") != "completed"]
    if not_done:
        # Mix of completed + running = in_progress
        any_running = any(r.get("status") in {"in_progress", "queued"} for r in not_done)
        state = "in_progress" if any_running else "pending"
        return {
            "ok": True,
            "state": state,
            "runs": runs,
            "worst_conclusion": None,
        }

    # All completed — derive worst conclusion
    conclusions = [r.get("conclusion") for r in runs if r.get("conclusion")]
    worst = _pick_worst_conclusion(conclusions)
    state = "failure" if worst in _FAILURE_CONCLUSIONS else "success"
    return {
        "ok": True,
        "state": state,
        "runs": runs,
        "worst_conclusion": worst,
    }


def _pick_worst_conclusion(conclusions: list[Optional[str]]) -> Optional[str]:
    """Pick the most serious conclusion from a list.

    Priority: failure > timed_out > action_required > cancelled > stale > skipped > neutral > success
    """
    if not conclusions:
        return None
    priority = [
        "failure",
        "timed_out",
        "action_required",
        "cancelled",
        "stale",
        "skipped",
        "neutral",
        "success",
    ]
    seen = set(c for c in conclusions if c)
    for p in priority:
        if p in seen:
            return p
    return next(iter(seen), None)


def wait_for_ci(
    commit_sha: str,
    *,
    branch: Optional[str] = None,
    repo: Optional[str] = None,
    timeout_secs: int = _DEFAULT_TIMEOUT_SECS,
    initial_delay: int = _INITIAL_DELAY_SECS,
    poll_interval: int = _POLL_INTERVAL_SECS,
    on_poll=None,
) -> Dict[str, Any]:
    """Poll CI until terminal or timeout.

    `on_poll(state_dict)` is called once after the initial delay and then after
    every poll — useful for status logging.

    Returns the final status dict with an extra `timed_out: bool` field.
    """
    if not commit_sha:
        return {
            "ok": False,
            "state": "no_runs",
            "timed_out": False,
            "error": "commit_sha فاضي",
        }

    deadline = time.monotonic() + max(60, timeout_secs)

    # Initial delay — let GitHub register the run
    initial = min(initial_delay, max(0, int(deadline - time.monotonic())))
    if not sa_shutdown.interruptible_sleep(initial):
        return {"ok": True, "state": "shutdown", "runs": [], "timed_out": False}

    last: Dict[str, Any] = {}
    no_runs_count = 0
    while time.monotonic() < deadline:
        if sa_shutdown.is_shutdown_requested():
            return {**(last or {"ok": True, "runs": []}), "state": "shutdown", "timed_out": False}

        status = github_ci_status(commit_sha, branch=branch, repo=repo)
        last = status

        if callable(on_poll):
            try:
                on_poll(status)
            except Exception as exc:
                logger.debug("[SA5] on_poll callback failed: %s", exc)

        state = status.get("state")
        if state in {"success", "failure"}:
            try:
                from app.utils import metrics
                metrics.inc_self_coding_ci_outcome(state)
            except Exception:
                pass
            return {**status, "timed_out": False}

        # No-runs: tolerate up to 4 polls (≈2 minutes after initial delay)
        if state == "no_runs":
            no_runs_count += 1
            if no_runs_count >= 4:
                try:
                    from app.utils import metrics
                    metrics.inc_self_coding_ci_outcome("no_runs")
                except Exception:
                    pass
                return {**status, "timed_out": False, "state": "no_runs"}

        # In_progress / pending → keep waiting
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        nap = min(poll_interval, max(1, int(remaining)))
        if not sa_shutdown.interruptible_sleep(nap):
            return {**(last or {"ok": True, "runs": []}), "state": "shutdown", "timed_out": False}

    return {**last, "timed_out": True}
