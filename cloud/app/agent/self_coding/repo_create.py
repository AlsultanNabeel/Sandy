"""SA8 helper: idempotent file creation in a branch.

Unlike `repo_patch.repo_apply_patch` which replaces line ranges in EXISTING
files, this writes a full file — used by the Project Builder when scaffolding
new modules.

Critical invariants:
1. Idempotent: re-running with the same (file, content, branch) is a no-op.
2. The write is committed to `branch` only — no merging to default branch.
3. The resulting commit_sha is written to the task hash (atomic) before
   we return, so a crash after the API call doesn't lose the SHA.
4. SA2 cache for the file is invalidated immediately after a write.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from app.agent.self_coding import _redis as sa_redis
from app.agent.self_coding.post_write_validate import (
    mark_path_written,
    validate_content,
    validate_written_paths,
)
from app.agent.self_coding.repo_view import invalidate_file_cache
from app.integrations import github_api

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 200 * 1024  # mirror SA2 limit


def repo_create_or_replace(
    file_path: str,
    content: str,
    *,
    branch: str,
    message: Optional[str] = None,
    repo: Optional[str] = None,
    task_id: Optional[str] = None,
    replace_if_exists: bool = True,
    expected_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Write `content` to `file_path` on `branch`. Creates if missing, replaces
    if present (when `replace_if_exists` is True).

    Args:
        file_path: path within the repo (no leading slash needed)
        content: full file content as a UTF-8 string
        branch: target branch (must exist — call github_create_branch first)
        message: commit message (default: auto-generated)
        repo: 'owner/name' (default: GITHUB_DEFAULT_REPO)
        task_id: if provided, last_commit_sha + patched_files are written
        replace_if_exists: if False and file exists, returns error instead of
            replacing (used when caller doesn't want to clobber).

    Returns:
        {
          ok: bool,
          file: str,
          commit_sha: str,
          created: bool,        # True if a new file was created
          replaced: bool,       # True if an existing file was overwritten
          no_op: bool,          # True if file existed with identical content
          error?: str,
        }
    """
    if not branch:
        return _err(file_path, "branch مطلوب")
    if not file_path:
        return _err(file_path, "file_path فاضي")
    if not isinstance(content, str):
        return _err(file_path, "content لازم يكون string")
    if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
        return _err(file_path, f"الملف أكبر من الحد ({_MAX_FILE_BYTES} bytes)")

    # Pre-flight syntactic validation on the proposed content. Catches things
    # like Python SyntaxError / invalid JSON / missing local imports before
    # the GitHub round-trip so the agent gets a clear error and can fix in
    # place. Pass repo+branch so the local-import existence check can see
    # the branch tree.
    ok_pre, msg_pre = validate_content(file_path, content, repo=repo, branch=branch)
    if not ok_pre:
        return _err(file_path, msg_pre)

    msg = message or f"sandy: scaffold {file_path}"
    lock_owner = f"{task_id or 'anon'}:{uuid.uuid4().hex}"

    if not sa_redis.file_lock_acquire(repo, file_path, lock_owner):
        return _err(file_path, f"الملف '{file_path}' قيد التعديل حالياً — جرّب بعد قليل")

    try:
        # 1) See if the file already exists on the target branch.
        existing = github_api.get_file_contents(file_path, ref=branch, repo=repo)
        file_exists = existing.get("ok") is True
        current_sha = existing.get("sha") or ""

        if expected_sha and current_sha != expected_sha:
            return _err(
                file_path,
                f"hash تغيّر منذ آخر قراءة (expected={expected_sha[:7] if expected_sha else ''}, current={current_sha[:7] if current_sha else 'missing'})",
            )

        if file_exists:
            if not replace_if_exists:
                return _err(
                    file_path,
                    f"الملف موجود فعلاً على {branch} — replace_if_exists=False",
                )
            # Same content already there, so nothing to do.
            if (existing.get("content") or "") == content:
                return {
                    "ok": True,
                    "file": file_path,
                    "commit_sha": "",
                    "created": False,
                    "replaced": False,
                    "no_op": True,
                }
            # Replace via update_file (requires blob sha)
            sha = existing.get("sha") or ""
            if not sha:
                return _err(file_path, "ما حصلنا blob SHA — مش قادرين نعمل replace آمن")
            api = github_api.update_file(
                file_path,
                new_content=content,
                sha=sha,
                branch=branch,
                message=msg,
                repo=repo,
            )
            if not api.get("ok"):
                return _err(file_path, api.get("error") or f"HTTP {api.get('status')}")
            commit_sha = api.get("commit_sha", "")
            invalidate_file_cache(file_path, sha=sha)
            mark_path_written(repo, branch, file_path)
            ok, message = validate_written_paths([file_path])
            if not ok:
                logger.error("[SA8] post-write validation failed for %s: %s", file_path, message)
                return _err(file_path, f"post-write validation failed: {message}")
            _record_task_progress(task_id, file_path, commit_sha)
            logger.info("[SA8] replaced %s on %s -> %s", file_path, branch, commit_sha[:7])
            return {
                "ok": True,
                "file": file_path,
                "commit_sha": commit_sha,
                "created": False,
                "replaced": True,
                "no_op": False,
            }

        # 2) New file — use create_file (no sha).
        # `get_file_contents` returns ok=False on 404; we only proceed if that's the
        # reason. Any other error (auth, rate limit) we surface directly.
        if existing.get("status") not in (404, 0):
            return _err(
                file_path,
                existing.get("error") or f"unexpected status {existing.get('status')}",
            )

        api = github_api.create_file(
            file_path,
            content=content,
            branch=branch,
            message=msg,
            repo=repo,
        )
        if not api.get("ok"):
            return _err(file_path, api.get("error") or f"HTTP {api.get('status')}")
        commit_sha = api.get("commit_sha", "")
        invalidate_file_cache(file_path)
        mark_path_written(repo, branch, file_path)
        ok, message = validate_written_paths([file_path])
        if not ok:
            logger.error("[SA8] post-write validation failed for %s: %s", file_path, message)
            return _err(file_path, f"post-write validation failed: {message}")
        _record_task_progress(task_id, file_path, commit_sha)
        logger.info("[SA8] created %s on %s -> %s", file_path, branch, commit_sha[:7])
        return {
            "ok": True,
            "file": file_path,
            "commit_sha": commit_sha,
            "created": True,
            "replaced": False,
            "no_op": False,
        }
    finally:
        sa_redis.file_lock_release(repo, file_path, lock_owner)


def _err(file_path: str, reason: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "file": file_path,
        "commit_sha": "",
        "created": False,
        "replaced": False,
        "no_op": False,
        "error": reason,
    }


def _record_task_progress(task_id: Optional[str], file_path: str, commit_sha: str) -> None:
    """Persist last_commit_sha + patched_files immediately (no local state).

    Mirrors `repo_patch._record_task_progress` so task record format stays
    consistent across line-patches and full-file scaffolds. `patched_files`
    here means "files Sandy touched on this task" — applies to both line-
    patches and full-file scaffolds.
    """
    if not task_id or not commit_sha:
        return
    if not sa_redis.is_available():
        return

    existing_raw = sa_redis.task_hget(task_id, "patched_files") or "[]"
    try:
        import json as _json
        existing = _json.loads(existing_raw) if existing_raw else []
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    if file_path not in existing:
        existing.append(file_path)

    sa_redis.task_hset(
        task_id,
        {
            "last_commit_sha": commit_sha,
            "patched_files": existing,
        },
    )
