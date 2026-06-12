"""SA2: repo_view_lines — read a slice of a file from GitHub.

Stores {sha, lines, full_content, line_ending} in Redis cache (TTL 30min) so
that SA3 can apply a patch using the EXACT lines + endings the LLM saw.

Key invariants:
- All line indices are 1-indexed throughout the Project Builder stack.
- Original line endings (CRLF/CR/LF) are preserved in `full_content`.
  We normalize ONLY the working `lines[]` list for indexing.
- Files larger than 200KB are rejected — caller must narrow the query.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.agent.project_builder import _redis as sa_redis
from app.integrations import github_api

logger = logging.getLogger(__name__)

_CACHE_TTL_SECS = 1800  # 30 minutes — matches plan
_MAX_BYTES = 200 * 1024  # mirror of github_api limit
_MAX_LINES_RETURNED = 400  # cap range the LLM can request in one call


# Line ending detection and normalization.
def detect_line_ending(text: str) -> str:
    """Detect dominant line ending: '\\r\\n', '\\r', or '\\n' (default).

    We sample the first 8KB to avoid scanning huge files repeatedly.
    """
    sample = text[:8192]
    crlf = sample.count("\r\n")
    if crlf > 0:
        # CRLF dominant if it appears at all (Windows files)
        return "\r\n"
    lone_cr = sample.count("\r") - crlf  # CR not part of CRLF
    if lone_cr > sample.count("\n"):
        return "\r"
    return "\n"


def split_lines_preserve(text: str) -> List[str]:
    """Split text into logical lines WITHOUT endings (for index-based ops).

    Uses splitlines(keepends=False) so all of \\r\\n, \\r, \\n produce the same
    logical split. Empty trailing line is preserved if file ends with newline.
    """
    if not text:
        return [""]
    lines = text.splitlines(keepends=False)
    # splitlines drops a single trailing empty line — add it back if file ends with newline
    if text.endswith(("\n", "\r")):
        lines.append("")
    return lines


def join_lines_with_ending(lines: List[str], ending: str) -> str:
    """Inverse of split_lines_preserve — produces file content with original ending.

    If the original file ended with a newline (last logical line == ""), we keep that.
    """
    if not lines:
        return ""
    if lines[-1] == "":
        body = ending.join(lines[:-1])
        return body + (ending if body else ending if len(lines) > 1 else "")
    return ending.join(lines)


# Cache layer.
def _cache_set(path: str, payload: Dict[str, Any]) -> None:
    sha = payload.get("sha") or ""
    if not sha:
        return
    sa_redis.set_json(sa_redis.k_file_cache(path, sha), payload, ex=_CACHE_TTL_SECS)


def _cache_get(path: str, sha: str) -> Optional[Dict[str, Any]]:
    return sa_redis.get_json(sa_redis.k_file_cache(path, sha))


def invalidate_file_cache(path: str, sha: str = "") -> None:
    """Drop a file from cache. Call after SA3 successfully patches."""
    if sha:
        sa_redis.delete(sa_redis.k_file_cache(path, sha))
        return
    # Without sha we can't target the exact key — best-effort scan via known sha is the only path.
    # In practice SA3 always passes the sha it patched.
    logger.debug("[SA2] invalidate_file_cache called without sha for %s", path)


def get_cached_or_fetch(
    path: str,
    *,
    ref: Optional[str] = None,
    repo: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Fetch file from cache if fresh, otherwise from GitHub. Updates cache.

    Returns: {ok, sha, lines, full_content, line_ending, size, error?}
    """
    # We can't look up the cache without a SHA, and fetching the file is the
    # same endpoint that returns it, so always fetch and then check the cache
    # by SHA.
    api_result = github_api.get_file_contents(path, ref=ref, repo=repo)
    if not api_result.get("ok"):
        return api_result

    sha = api_result.get("sha") or ""
    full_content = api_result.get("content") or ""

    if use_cache and sha:
        cached = _cache_get(path, sha)
        if cached and cached.get("sha") == sha:
            return cached

    ending = detect_line_ending(full_content)
    lines = split_lines_preserve(full_content)

    payload = {
        "ok": True,
        "sha": sha,
        "lines": lines,
        "full_content": full_content,
        "line_ending": ending,
        "size": api_result.get("size", 0),
        "path": path,
    }
    _cache_set(path, payload)
    return payload


# Public API (SA2).
def _validate_range(start_line: int, end_line: int, total_lines: int) -> Optional[str]:
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return "start_line و end_line لازم يكونوا أرقام صحيحة"
    if start_line < 1:
        return "start_line لازم ≥1 (1-indexed)"
    if end_line < start_line:
        return "end_line لازم ≥ start_line"
    if start_line > total_lines:
        return f"start_line ({start_line}) أكبر من عدد سطور الملف ({total_lines})"
    if end_line - start_line + 1 > _MAX_LINES_RETURNED:
        return f"النطاق المطلوب أكبر من الحد الأقصى ({_MAX_LINES_RETURNED} سطر)"
    return None


def repo_view_lines(
    file_path: str,
    start_line: int,
    end_line: int,
    *,
    ref: Optional[str] = None,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Return lines [start_line .. end_line] (1-indexed, inclusive) of a file.

    Args:
        file_path: path within the repo (e.g. 'cloud/app/foo.py')
        start_line: 1-indexed start (inclusive)
        end_line: 1-indexed end (inclusive)
        ref: branch/tag/SHA — defaults to repo default branch
        repo: 'owner/name' — defaults to GITHUB_DEFAULT_REPO

    Returns:
        {
          ok, file: str, sha: str, start_line: int, end_line: int,
          total_lines: int, lines: [str], snippet: str, error?
        }
    """
    if not file_path:
        return {"ok": False, "error": "file_path فاضي", "lines": []}

    fetched = get_cached_or_fetch(file_path, ref=ref, repo=repo)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "file": file_path,
            "error": fetched.get("error") or "fetch failed",
            "lines": [],
        }

    all_lines: List[str] = fetched.get("lines") or [""]
    total = len(all_lines)

    err = _validate_range(start_line, end_line, total)
    if err:
        return {
            "ok": False,
            "file": file_path,
            "sha": fetched.get("sha", ""),
            "total_lines": total,
            "lines": [],
            "error": err,
        }

    end_clamped = min(end_line, total)
    slice_lines = all_lines[start_line - 1 : end_clamped]

    # Numbered snippet so the LLM can refer to exact line numbers.
    snippet = "\n".join(
        f"{i:5d}: {line}" for i, line in enumerate(slice_lines, start=start_line)
    )

    return {
        "ok": True,
        "file": file_path,
        "sha": fetched.get("sha", ""),
        "start_line": start_line,
        "end_line": end_clamped,
        "total_lines": total,
        "lines": slice_lines,
        "snippet": snippet,
    }
