"""SA3: repo_apply_patch — replace lines [start_line..end_line] with new_lines.

This is the only write path to GitHub, so every safety check here matters.

Critical invariants:
1. All line indices are 1-indexed and INCLUSIVE.
   Splice = `lines[start_line-1 : end_line]`.
2. Original line ending of the file is preserved (CRLF stays CRLF).
3. new_lines must contain NO line ending characters (we add them on join).
4. SA2 cache is invalidated immediately after a successful patch.
5. On 409 conflict: invalidate → re-fetch → retry ONCE → fail.
6. The resulting commit_sha is written to the task hash (atomic) before
   we return, so a crash after the API call doesn't lose the SHA.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from app.agent.self_coding import _redis as sa_redis
from app.agent.self_coding.post_write_validate import (
    mark_path_written,
    validate_content,
)
from app.agent.self_coding.repo_view import (
    get_cached_or_fetch,
    invalidate_file_cache,
    join_lines_with_ending,
)
from app.integrations import github_api

logger = logging.getLogger(__name__)

# Cap how big a single patch can be — defense against runaway LLM output
_MAX_NEW_LINES = 1000
_MAX_NEW_LINE_LEN = 4096


# Input normalization.
def _normalize_new_lines(new_lines: List[str]) -> List[str]:
    """Strip any line endings the LLM may have included in individual entries.

    LLMs sometimes return ["a\\n", "b\\n"] — we want ["a", "b"] so that
    join_lines_with_ending controls the ending used.
    """
    out: List[str] = []
    for raw in new_lines:
        if raw is None:
            out.append("")
            continue
        s = str(raw)
        # Remove trailing CRLF/LF/CR but NOT internal ones (those should never appear)
        s = s.rstrip("\r\n")
        out.append(s)
    return out


def _validate_patch_input(
    file_path: str,
    start_line: int,
    end_line: int,
    new_lines: List[str],
    total_lines: int,
) -> Optional[str]:
    if not file_path:
        return "file_path فاضي"
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return "start_line و end_line لازم يكونوا أرقام صحيحة"
    if start_line < 1:
        return "start_line لازم ≥1"
    if end_line < start_line:
        return "end_line لازم ≥ start_line"
    if start_line > total_lines + 1:
        return f"start_line ({start_line}) أبعد من نهاية الملف ({total_lines} سطر)"
    if end_line > total_lines:
        return f"end_line ({end_line}) أكبر من عدد سطور الملف ({total_lines})"
    if not isinstance(new_lines, list):
        return "new_lines لازم يكون list من strings"
    if len(new_lines) > _MAX_NEW_LINES:
        return f"new_lines كبير جداً (>{_MAX_NEW_LINES})"
    for i, line in enumerate(new_lines):
        if not isinstance(line, (str, type(None))):
            return f"new_lines[{i}] لازم string، حصلنا {type(line).__name__}"
        if isinstance(line, str) and len(line) > _MAX_NEW_LINE_LEN:
            return f"new_lines[{i}] أطول من الحد ({_MAX_NEW_LINE_LEN} char)"
        # Internal newline in a single line entry is a sign the LLM split wrong
        if isinstance(line, str) and ("\n" in line or "\r" in line):
            return f"new_lines[{i}] فيه newline داخلي — كل عنصر = سطر واحد"
    return None


# Core splice.
def _apply_splice(
    lines: List[str],
    start_line: int,
    end_line: int,
    new_lines: List[str],
) -> List[str]:
    """Return new list: lines with [start_line..end_line] replaced by new_lines.

    1-indexed inclusive. Pure function — no I/O.
    """
    before = lines[: start_line - 1]
    after = lines[end_line:]
    return before + new_lines + after


# Public API (SA3).
def repo_apply_patch(
    file_path: str,
    start_line: int,
    end_line: int,
    new_lines: List[str],
    *,
    branch: str,
    message: Optional[str] = None,
    repo: Optional[str] = None,
    task_id: Optional[str] = None,
    expected_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Replace lines [start_line..end_line] (1-indexed, inclusive) with new_lines.

    Args:
        file_path: path within the repo
        start_line: 1-indexed inclusive start
        end_line: 1-indexed inclusive end
        new_lines: list of strings, each = one full line WITHOUT newline char
        branch: target branch (must exist — call github_create_branch first)
        message: commit message (default: auto-generated)
        repo: 'owner/name' (default: GITHUB_DEFAULT_REPO)
        task_id: if provided, commit_sha + patched_files are written to task hash

    Returns:
        {
          ok: bool,
          file: str,
          commit_sha: str,        # empty on failure
          new_blob_sha: str,
          lines_before: int,      # original total
          lines_after: int,       # post-patch total
          retried: bool,          # True if 409 → reload → retry happened
          error?: str
        }
    """
    if not branch:
        return {"ok": False, "file": file_path, "error": "branch مطلوب", "commit_sha": ""}

    norm_new = _normalize_new_lines(new_lines)
    lock_owner = f"{task_id or 'anon'}:{uuid.uuid4().hex}"
    if not sa_redis.file_lock_acquire(repo, file_path, lock_owner):
        return {"ok": False, "file": file_path, "error": f"الملف '{file_path}' قيد التعديل حالياً — جرّب بعد قليل", "commit_sha": ""}

    try:
        # First attempt
        result = _attempt_patch(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            new_lines=norm_new,
            branch=branch,
            message=message,
            repo=repo,
            force_reload=False,
            task_id=task_id,
            expected_sha=expected_sha,
        )
        if result.get("ok"):
            _record_task_progress(task_id, file_path, result.get("commit_sha", ""))
            return {**result, "retried": False}

        # 409 → re-fetch and retry once
        if result.get("status") == 409:
            logger.warning(
                "[SA3] 409 conflict on %s — invalidating cache and retrying", file_path
            )
            result2 = _attempt_patch(
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                new_lines=norm_new,
                branch=branch,
                message=message,
                repo=repo,
                force_reload=True,
                task_id=task_id,
                expected_sha=expected_sha,
            )
            if result2.get("ok"):
                _record_task_progress(task_id, file_path, result2.get("commit_sha", ""))
                return {**result2, "retried": True}
            return {
                **result2,
                "retried": True,
                "error": "فيه تعارض، راجع يدوياً — " + (result2.get("error") or ""),
            }

        return {**result, "retried": False}
    finally:
        sa_redis.file_lock_release(repo, file_path, lock_owner)


def _attempt_patch(
    *,
    file_path: str,
    start_line: int,
    end_line: int,
    new_lines: List[str],
    branch: str,
    message: Optional[str],
    repo: Optional[str],
    force_reload: bool,
    task_id: Optional[str],
    expected_sha: Optional[str],
) -> Dict[str, Any]:
    """Single patch attempt — returns full result dict (incl. status)."""
    # Always fetch from the TARGET branch — the patch must apply to its head.
    fetched = get_cached_or_fetch(
        file_path,
        ref=branch,
        repo=repo,
        use_cache=not force_reload,
    )
    if not fetched.get("ok"):
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": 0,
            "error": fetched.get("error") or "view failed",
        }

    sha = fetched.get("sha") or ""
    if not sha:
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": 0,
            "error": "ما حصلنا blob SHA — مش قادرين نعمل update آمن",
        }

    if expected_sha and sha != expected_sha:
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": 0,
            "error": f"hash تغيّر منذ آخر قراءة (expected={expected_sha[:7]}, current={sha[:7]})",
        }

    lines: List[str] = fetched.get("lines") or [""]
    total = len(lines)
    err = _validate_patch_input(file_path, start_line, end_line, new_lines, total)
    if err:
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": 0,
            "error": err,
        }

    ending: str = fetched.get("line_ending") or "\n"
    patched = _apply_splice(lines, start_line, end_line, new_lines)
    new_content = join_lines_with_ending(patched, ending)

    # No-op short-circuit
    if new_content == (fetched.get("full_content") or ""):
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": 0,
            "error": "الـ patch ما غيّر شي (no-op)",
        }

    # Pre-flight: validate the spliced full content before the API call.
    # Catches things like Python SyntaxError introduced by an overlapping
    # patch + missing local imports before the GitHub round-trip, so the
    # agent gets a clear error and can fix in place instead of pushing a
    # broken commit that breaks CI later.
    ok_pre, msg_pre = validate_content(file_path, new_content, repo=repo, branch=branch)
    if not ok_pre:
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": 0,
            "error": msg_pre,
        }

    msg = message or _build_default_message(file_path, start_line, end_line)

    api_result = github_api.update_file(
        file_path,
        new_content=new_content,
        sha=sha,
        branch=branch,
        message=msg,
        repo=repo,
    )

    status = api_result.get("status", 0)
    if not api_result.get("ok"):
        return {
            "ok": False,
            "file": file_path,
            "commit_sha": "",
            "status": status,
            "error": api_result.get("error") or f"HTTP {status}",
        }

    # Invalidate cache for old SHA — next read pulls the new file
    invalidate_file_cache(file_path, sha=sha)
    mark_path_written(repo, branch, file_path)

    commit_sha = api_result.get("commit_sha", "")
    _record_task_progress(task_id, file_path, commit_sha)
    logger.info(
        "[SA3] patched %s:%d-%d on %s -> %s",
        file_path,
        start_line,
        end_line,
        branch,
        commit_sha[:7],
    )

    return {
        "ok": True,
        "file": file_path,
        "commit_sha": commit_sha,
        "new_blob_sha": api_result.get("new_blob_sha", ""),
        "status": status,
        "lines_before": total,
        "lines_after": len(patched),
    }


def _build_default_message(file_path: str, start_line: int, end_line: int) -> str:
    return f"sandy: patch {file_path} L{start_line}-L{end_line}"


def _record_task_progress(task_id: Optional[str], file_path: str, commit_sha: str) -> None:
    """Persist last_commit_sha + patched_files immediately (no local state)."""
    if not task_id:
        return
    if not sa_redis.is_available():
        return

    # Append to patched_files list (read-modify-write — but the task is single-worker so safe)
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
