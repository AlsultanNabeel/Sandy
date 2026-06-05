"""SA4: github_create_branch — idempotent branch creation per task.

Each Self-Coding task runs on an isolated branch `sandy-task-<id>`.
If the branch already exists (e.g. crash recovery), we just return its SHA.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app.integrations import github_api

logger = logging.getLogger(__name__)

_BRANCH_PREFIX = "sandy-task-"
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _safe_task_id(task_id: str) -> str:
    """Sanitize task_id for branch name."""
    return _SAFE_ID_RE.sub("-", task_id.strip())[:64] or "noid"


def task_branch_name(task_id: str) -> str:
    """Build the canonical branch name for a task."""
    return f"{_BRANCH_PREFIX}{_safe_task_id(task_id)}"


def github_create_branch(
    task_id: str,
    *,
    base_ref: Optional[str] = None,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """SA4: Create `sandy-task-<id>` branch from base_ref (default: repo default).

    Idempotent: if the branch already exists, returns ok=True with existed=True.

    Returns:
        {ok: bool, branch: str, sha: str, existed: bool, error?: str}
    """
    branch = task_branch_name(task_id)
    result = github_api.create_branch(branch, base_ref=base_ref, repo=repo)
    if not result.get("ok"):
        logger.warning(
            "[SA4] فشل إنشاء branch=%s: %s", branch, result.get("error")
        )
        return {
            "ok": False,
            "branch": branch,
            "error": result.get("error") or "create_branch failed",
        }
    return {
        "ok": True,
        "branch": branch,
        "sha": result.get("sha", ""),
        "existed": bool(result.get("existed")),
    }
