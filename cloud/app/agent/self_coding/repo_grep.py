"""SA1: repo_grep — search code by keyword via GitHub Code Search API.

Returns only file paths + line numbers (best effort), never full file content.
The caller (LLM) then uses SA2 to read specific ranges.

Note: GitHub Code Search only indexes the default branch. To verify a feature
branch after a patch, call repo_view_lines directly instead.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.integrations import github_api

logger = logging.getLogger(__name__)

_MAX_RESULTS = 30


def repo_grep(
    query: str,
    *,
    repo: Optional[str] = None,
    max_results: int = _MAX_RESULTS,
    path_filter: Optional[str] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Search code across the default branch of `repo`.

    Args:
        query: search term (≥2 chars, raw text or GitHub qualifiers like `func in:file`)
        repo: 'owner/name' — defaults to GITHUB_DEFAULT_REPO
        max_results: cap returned matches (default 30)
        path_filter: optional `path:src/` style filter
        language: optional `language:python` style filter

    Returns:
        {
          ok: bool,
          total_count: int,
          truncated: bool,           # True if results exceeded max_results
          results: [
            {path: str, line_numbers: [int], excerpt: str}
          ],
          error?: str
        }
    """
    if not query or not query.strip():
        return {
            "ok": False,
            "total_count": 0,
            "truncated": False,
            "results": [],
            "error": "query فاضي",
        }

    parts = [query.strip()]
    if path_filter:
        parts.append(f"path:{path_filter}")
    if language:
        parts.append(f"language:{language}")
    full_query = " ".join(parts)

    api_result = github_api.search_code(
        full_query,
        repo=repo,
        per_page=min(max_results, 100),
    )

    if not api_result.get("ok"):
        return {
            "ok": False,
            "total_count": 0,
            "truncated": False,
            "results": [],
            "error": api_result.get("error") or "search failed",
        }

    items = api_result.get("items") or []
    total = int(api_result.get("total_count") or len(items))

    truncated = len(items) > max_results
    items = items[:max_results]

    # file path + line hints + excerpt, nothing more
    results: List[Dict[str, Any]] = []
    for it in items:
        path = it.get("path") or ""
        line_numbers = it.get("line_numbers") or []
        excerpt = it.get("excerpt") or ""
        if not path:
            continue
        unique_lines = sorted({int(n) for n in line_numbers if isinstance(n, int) and n > 0})
        results.append(
            {
                "path": path,
                "line_numbers": unique_lines[:10],  # cap per file
                "excerpt": excerpt[:200],
            }
        )

    logger.info(
        "[SA1] repo_grep '%s' → %d/%d results (truncated=%s)",
        query[:60],
        len(results),
        total,
        truncated,
    )

    return {
        "ok": True,
        "total_count": total,
        "truncated": truncated,
        "results": results,
    }
