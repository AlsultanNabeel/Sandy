"""GitHub REST API client for Sandy repo helpers (Project Builder + legacy flows).

Covers code search, file read/write, and branch ops. Has retry with
exponential backoff and a circuit breaker.

Every public function returns a `{ok: bool, ...}` dict and never raises.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_USER_AGENT = "Sandy-ProjectBuilder/1.0"
_TIMEOUT_SECS = 30
_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0  # exponential: 2, 4, 8 seconds
_MAX_FILE_BYTES = 200 * 1024  # 200KB — SA2 limit

# Code search has tight limits (10 req/min authenticated)
_SEARCH_BACKOFF_BASE = 6.0  # search-specific backoff

_cb = CircuitBreaker(
    name="github_api",
    failure_threshold=5,
    recovery_timeout=60.0,
)


# Token + session
def _get_token() -> str:
    """Read GitHub token from env. Empty string means not configured."""
    return (
        os.getenv("GITHUB_TOKEN")
        or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
        or ""
    ).strip()


def _get_default_repo() -> str:
    """Read default repo (owner/name) from env."""
    return (os.getenv("GITHUB_DEFAULT_REPO") or "").strip()


def _split_repo(repo: str) -> Optional[Tuple[str, str]]:
    """'owner/name' → ('owner', 'name'). None if invalid."""
    if not repo or "/" not in repo:
        return None
    parts = repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0].strip(), parts[1].strip()


def _make_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


# Core HTTP with retry
def _is_rate_limited(resp: requests.Response) -> bool:
    """True if response indicates rate limiting (403/429 with remaining=0)."""
    if resp.status_code == 429:
        return True
    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            return True
        if "rate limit" in (resp.text or "").lower():
            return True
    return False


def _retry_after_seconds(resp: requests.Response, attempt: int, base: float) -> float:
    """Compute backoff: prefer Retry-After header, else exponential."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass
    # Exponential backoff with attempt index
    return base * (2 ** attempt)


def _request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    headers_extra: Optional[Dict[str, str]] = None,
    backoff_base: float = _BASE_BACKOFF,
    accept_404: bool = False,
) -> Dict[str, Any]:
    """Perform GitHub API request with retry on 5xx/429/rate-limit.

    Returns `{ok, status, data, error, headers}`. Never raises.
    """
    if not _get_token():
        return {
            "ok": False,
            "status": 0,
            "error": "GITHUB_TOKEN غير مضبوط — Project Builder/Repo helpers متوقف",
        }

    url = f"{_GITHUB_API_BASE}{path}"
    headers = _make_headers(headers_extra)

    def _call_once() -> requests.Response:
        return requests.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=_TIMEOUT_SECS,
        )

    last_resp: Optional[requests.Response] = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = _cb.call(_call_once)
        except CircuitOpenError:
            return {
                "ok": False,
                "status": 0,
                "error": "GitHub API circuit مفتوح — انتظر استرداد",
            }
        except requests.exceptions.RequestException as exc:
            logger.warning("[github_api] network error attempt=%d: %s", attempt, exc)
            if attempt == _MAX_RETRIES - 1:
                return {
                    "ok": False,
                    "status": 0,
                    "error": f"network error: {exc}",
                }
            time.sleep(backoff_base * (2 ** attempt))
            continue

        last_resp = resp

        # 5xx → retry
        if 500 <= resp.status_code < 600 and attempt < _MAX_RETRIES - 1:
            sleep_s = _retry_after_seconds(resp, attempt, backoff_base)
            logger.warning(
                "[github_api] %d on %s — retrying in %.1fs", resp.status_code, path, sleep_s
            )
            time.sleep(sleep_s)
            continue

        # Rate limit → retry with backoff
        if _is_rate_limited(resp) and attempt < _MAX_RETRIES - 1:
            sleep_s = _retry_after_seconds(resp, attempt, backoff_base)
            logger.warning(
                "[github_api] rate limited on %s — sleeping %.1fs", path, sleep_s
            )
            time.sleep(sleep_s)
            continue

        # Success or terminal error
        return _parse_response(resp, accept_404=accept_404)

    # Exhausted retries
    if last_resp is not None:
        return _parse_response(last_resp, accept_404=accept_404)
    return {"ok": False, "status": 0, "error": "exhausted retries"}


def _parse_response(resp: requests.Response, *, accept_404: bool) -> Dict[str, Any]:
    """Parse response into standard {ok, status, data, error, headers} shape."""
    status = resp.status_code
    ok = 200 <= status < 300 or (accept_404 and status == 404)
    out: Dict[str, Any] = {
        "ok": ok,
        "status": status,
        "headers": dict(resp.headers),
    }
    # Best-effort JSON parse
    try:
        out["data"] = resp.json() if resp.content else None
    except ValueError:
        out["data"] = resp.text
    if not ok:
        data = out.get("data") or {}
        if isinstance(data, dict):
            out["error"] = data.get("message") or data.get("error") or f"HTTP {status}"
        else:
            out["error"] = f"HTTP {status}"
    return out


# SA1: Code Search
def search_code(
    query: str,
    *,
    repo: Optional[str] = None,
    per_page: int = 20,
    page: int = 1,
) -> Dict[str, Any]:
    """SA1: Search code via GitHub Code Search API.

    GitHub Code Search indexes the DEFAULT BRANCH only — feature branches are
    not searchable. For post-patch verification on a feature branch use
    `view_lines` directly, not this.

    Args:
        query: free-text search (string or `term in:file` syntax)
        repo: 'owner/name' — defaults to GITHUB_DEFAULT_REPO
        per_page: results per page (max 100)
        page: 1-indexed page number

    Returns dict shape:
        {ok, status, items: [{path, line_numbers: [int], excerpt: str, sha}], ...}
    """
    repo = repo or _get_default_repo()
    if not _split_repo(repo):
        return {
            "ok": False,
            "status": 0,
            "error": "repo غير محدد — GITHUB_DEFAULT_REPO فاضي",
            "items": [],
        }
    if not query or len(query.strip()) < 2:
        return {
            "ok": False,
            "status": 0,
            "error": "query قصيرة جداً (≥2 chars)",
            "items": [],
        }

    # Scope to repo
    full_query = f"{query.strip()} repo:{repo}"
    result = _request(
        "GET",
        "/search/code",
        params={"q": full_query, "per_page": min(per_page, 100), "page": page},
        headers_extra={"Accept": "application/vnd.github.text-match+json"},
        backoff_base=_SEARCH_BACKOFF_BASE,
    )

    if not result.get("ok"):
        result["items"] = []
        return result

    data = result.get("data") or {}
    items_raw = data.get("items", []) if isinstance(data, dict) else []
    items: List[Dict[str, Any]] = []
    for it in items_raw:
        path = it.get("path") or ""
        sha = it.get("sha") or ""
        line_numbers: List[int] = []
        excerpt = ""
        for tm in it.get("text_matches") or []:
            fragment = tm.get("fragment") or ""
            if fragment and not excerpt:
                excerpt = fragment[:300]
            for match in tm.get("matches") or []:
                indices = match.get("indices") or []
                # GitHub doesn't return line numbers directly — best-effort from fragment
                if indices and fragment:
                    char_idx = indices[0]
                    line_in_fragment = fragment[: max(0, char_idx)].count("\n") + 1
                    line_numbers.append(line_in_fragment)
        items.append(
            {
                "path": path,
                "sha": sha,
                "line_numbers": line_numbers,
                "excerpt": excerpt,
            }
        )

    result["items"] = items
    result["total_count"] = data.get("total_count", len(items)) if isinstance(data, dict) else len(items)
    return result


# SA2: View Lines
def get_file_contents(
    file_path: str,
    *,
    repo: Optional[str] = None,
    ref: Optional[str] = None,
) -> Dict[str, Any]:
    """SA2 (raw): Fetch full file content + metadata from GitHub.

    Returns:
        {ok, status, sha, content (str), size, encoding, error?}

    Rejects files > 200KB.
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    if not file_path:
        return {"ok": False, "status": 0, "error": "file_path فاضي"}

    # GitHub API path-encodes the file path
    path = f"/repos/{owner}/{name}/contents/{file_path.lstrip('/')}"
    params = {"ref": ref} if ref else None
    result = _request("GET", path, params=params)

    if not result.get("ok"):
        return result

    data = result.get("data") or {}
    if isinstance(data, list):
        return {
            "ok": False,
            "status": result.get("status"),
            "error": f"{file_path} مجلد، مش ملف",
        }

    size = int(data.get("size") or 0)
    if size > _MAX_FILE_BYTES:
        return {
            "ok": False,
            "status": result.get("status"),
            "error": f"الملف أكبر من الحد ({size} > {_MAX_FILE_BYTES} bytes)",
        }

    encoding = (data.get("encoding") or "").lower()
    raw_content = data.get("content") or ""
    if encoding == "base64":
        try:
            decoded = base64.b64decode(raw_content).decode("utf-8")
        except Exception as exc:
            return {
                "ok": False,
                "status": result.get("status"),
                "error": f"فشل فك ترميز الملف: {exc}",
            }
    else:
        decoded = raw_content

    return {
        "ok": True,
        "status": result.get("status"),
        "sha": data.get("sha") or "",
        "content": decoded,
        "size": size,
        "encoding": encoding,
        "path": data.get("path") or file_path,
    }


# SA3: Apply Patch (commit file update)
def update_file(
    file_path: str,
    *,
    new_content: str,
    sha: str,
    branch: str,
    message: str,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """SA3 (raw): Update a file's contents via Contents API.

    Args:
        file_path: path within repo
        new_content: full file content (string — will be UTF-8 encoded + base64'd)
        sha: blob SHA from the GET response (required for update — prevents lost writes)
        branch: target branch
        message: commit message

    Returns:
        {ok, status, commit_sha, new_blob_sha, error?}

    On 409: caller should clear cache, re-fetch, retry once.
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt

    encoded = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
    body = {
        "message": message,
        "content": encoded,
        "sha": sha,
        "branch": branch,
    }
    path = f"/repos/{owner}/{name}/contents/{file_path.lstrip('/')}"
    result = _request("PUT", path, json_body=body)

    if not result.get("ok"):
        return result

    data = result.get("data") or {}
    commit_sha = ((data.get("commit") or {}).get("sha")) or ""
    blob_sha = ((data.get("content") or {}).get("sha")) or ""
    return {
        "ok": True,
        "status": result.get("status"),
        "commit_sha": commit_sha,
        "new_blob_sha": blob_sha,
        "path": file_path,
    }


# SA8: Create File (new file in a branch)
def create_file(
    file_path: str,
    *,
    content: str,
    branch: str,
    message: str,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a NEW file on `branch` via Contents API.

    Uses the same `PUT /contents/{path}` endpoint as `update_file` but omits
    the `sha` parameter — GitHub treats this as a create. If the file already
    exists, GitHub returns 422 ("Invalid request: \"sha\" wasn't supplied").

    For idempotent "create or replace" semantics, use the higher-level
    `repo_create.repo_create_or_replace`.

    Returns:
        {ok, status, commit_sha, new_blob_sha, error?}
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt

    if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
        return {
            "ok": False,
            "status": 0,
            "error": f"الملف أكبر من الحد ({_MAX_FILE_BYTES} bytes)",
        }

    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    body = {
        "message": message,
        "content": encoded,
        "branch": branch,
    }
    path = f"/repos/{owner}/{name}/contents/{file_path.lstrip('/')}"
    result = _request("PUT", path, json_body=body)

    if not result.get("ok"):
        return result

    data = result.get("data") or {}
    commit_sha = ((data.get("commit") or {}).get("sha")) or ""
    blob_sha = ((data.get("content") or {}).get("sha")) or ""
    return {
        "ok": True,
        "status": result.get("status"),
        "commit_sha": commit_sha,
        "new_blob_sha": blob_sha,
        "path": file_path,
    }


# SA8: Create Repo (for external Project Builder)
def create_repo(
    name: str,
    *,
    description: str = "",
    private: bool = True,
    auto_init: bool = True,
    org: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new GitHub repo.

    If `org` is set, creates under that organization (token must have access).
    Otherwise creates under the authenticated user.

    `auto_init=True` adds a README.md as the initial commit so `main` exists —
    SA8 needs this so it can branch off and apply patches immediately.

    Returns:
        {ok, status, full_name, html_url, default_branch, error?}
    """
    if not name or not isinstance(name, str):
        return {"ok": False, "status": 0, "error": "اسم الـ repo فاضي"}

    body = {
        "name": name,
        "description": (description or "")[:350],
        "private": bool(private),
        "auto_init": bool(auto_init),
    }
    path = f"/orgs/{org}/repos" if org else "/user/repos"
    result = _request("POST", path, json_body=body)
    if not result.get("ok"):
        return result

    data = result.get("data") or {}
    return {
        "ok": True,
        "status": result.get("status"),
        "full_name": data.get("full_name", ""),
        "html_url": data.get("html_url", ""),
        "default_branch": data.get("default_branch", "main"),
    }


# SA4: Create Branch
def get_branch_ref(branch: str, *, repo: Optional[str] = None) -> Dict[str, Any]:
    """Read a ref. Returns ok=True with data if exists, ok=False if 404."""
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    return _request(
        "GET",
        f"/repos/{owner}/{name}/git/ref/heads/{branch}",
        accept_404=True,
    )


def get_default_branch(*, repo: Optional[str] = None) -> Dict[str, Any]:
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    result = _request("GET", f"/repos/{owner}/{name}")
    if result.get("ok"):
        result["default_branch"] = (result.get("data") or {}).get("default_branch")
    return result


def get_authenticated_login() -> Dict[str, Any]:
    """Return the login (username) the token belongs to.

    Used to construct full repo names (`<login>/<repo_name>`) when the caller
    only has a bare repo name.
    """
    result = _request("GET", "/user")
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "status": result.get("status"),
        "login": (result.get("data") or {}).get("login", ""),
    }


def get_repo(repo: str) -> Dict[str, Any]:
    """Look up a repo. `repo` is `owner/name`.

    Returns:
        exists  → {ok: True, exists: True, full_name, html_url, default_branch}
        absent  → {ok: True, exists: False, status: 404}
        error   → {ok: False, status, error}
    """
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    result = _request("GET", f"/repos/{owner}/{name}", accept_404=True)
    if not result.get("ok"):
        return result
    if result.get("status") == 404:
        return {"ok": True, "exists": False, "status": 404}
    data = result.get("data") or {}
    return {
        "ok": True,
        "exists": True,
        "status": result.get("status"),
        "full_name": data.get("full_name", ""),
        "html_url": data.get("html_url", ""),
        "default_branch": data.get("default_branch", "main"),
    }


def compare_branches(
    base: str,
    head: str,
    *,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the diff between `base` and `head` branches.

    Returns: {ok, status, files: [{path, status, additions, deletions, patch?}],
              total_changes, error?}
    `patch` is the unified-diff hunk per file (capped by GitHub at ~3000 lines).
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    result = _request("GET", f"/repos/{owner}/{name}/compare/{base}...{head}")
    if not result.get("ok"):
        return result

    data = result.get("data") or {}
    files = data.get("files") or []
    out_files = [
        {
            "path": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": int(f.get("additions") or 0),
            "deletions": int(f.get("deletions") or 0),
            "patch": f.get("patch") or "",
        }
        for f in files
    ]
    return {
        "ok": True,
        "status": result.get("status"),
        "files": out_files,
        "total_changes": sum(f["additions"] + f["deletions"] for f in out_files),
    }


def list_repo_tree(
    *,
    repo: Optional[str] = None,
    ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Recursive file listing of a repo at `ref` (default branch if None).

    Returns: {ok, status, paths: List[str], truncated: bool, error?}
    Filters to files only — directories excluded.
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt

    if not ref:
        db = get_default_branch(repo=repo)
        ref = db.get("default_branch") if db.get("ok") else "main"

    result = _request(
        "GET",
        f"/repos/{owner}/{name}/git/trees/{ref}",
        params={"recursive": "1"},
    )
    if not result.get("ok"):
        return result

    data = result.get("data") or {}
    tree = data.get("tree") or []
    paths = [item.get("path", "") for item in tree if item.get("type") == "blob"]
    return {
        "ok": True,
        "status": result.get("status"),
        "paths": paths,
        "truncated": bool(data.get("truncated")),
    }


def create_branch(
    branch: str,
    *,
    base_ref: Optional[str] = None,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """SA4: Create a branch from base_ref. Idempotent: returns ok=True if exists.

    Returns: {ok, status, branch, sha, existed, error?}
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt

    # Check existing — idempotent
    existing = get_branch_ref(branch, repo=repo)
    if existing.get("ok") and existing.get("status") != 404:
        data = existing.get("data") or {}
        sha = ((data.get("object") or {}).get("sha")) or ""
        if sha:
            return {
                "ok": True,
                "status": existing.get("status"),
                "branch": branch,
                "sha": sha,
                "existed": True,
            }

    # Resolve base ref → SHA
    if not base_ref:
        info = get_default_branch(repo=repo)
        if not info.get("ok"):
            return info
        base_ref = info.get("default_branch") or "main"

    base = get_branch_ref(base_ref, repo=repo)
    if not base.get("ok") or base.get("status") == 404:
        return {
            "ok": False,
            "status": base.get("status"),
            "error": f"base ref '{base_ref}' غير موجود",
        }
    base_sha = ((base.get("data") or {}).get("object") or {}).get("sha") or ""
    if not base_sha:
        return {"ok": False, "status": 0, "error": "ما قدرت أحصّل SHA للـ base"}

    result = _request(
        "POST",
        f"/repos/{owner}/{name}/git/refs",
        json_body={"ref": f"refs/heads/{branch}", "sha": base_sha},
    )
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "status": result.get("status"),
        "branch": branch,
        "sha": base_sha,
        "existed": False,
    }


# SA5: Workflow Runs
def list_workflow_runs_for_commit(
    commit_sha: str,
    *,
    branch: Optional[str] = None,
    repo: Optional[str] = None,
    per_page: int = 20,
) -> Dict[str, Any]:
    """SA5: List workflow runs filtered by HEAD SHA.

    Returns: {ok, status, runs: [{id, name, status, conclusion, html_url, head_sha, created_at}]}
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد", "runs": []}
    owner, name = rt

    params: Dict[str, Any] = {
        "head_sha": commit_sha,
        "per_page": min(per_page, 100),
    }
    if branch:
        params["branch"] = branch

    result = _request("GET", f"/repos/{owner}/{name}/actions/runs", params=params)
    if not result.get("ok"):
        result["runs"] = []
        return result

    data = result.get("data") or {}
    raw_runs = data.get("workflow_runs") or []
    runs = [
        {
            "id": r.get("id"),
            "name": r.get("name") or r.get("workflow_name"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "html_url": r.get("html_url"),
            "head_sha": r.get("head_sha"),
            "head_branch": r.get("head_branch"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        }
        for r in raw_runs
    ]
    result["runs"] = runs
    result["total_count"] = data.get("total_count", len(runs))
    return result


def get_workflow_run(
    run_id: int,
    *,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Get full details of a single workflow run."""
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    return _request("GET", f"/repos/{owner}/{name}/actions/runs/{run_id}")


def get_workflow_run_jobs_failed_logs(
    run_id: int,
    *,
    repo: Optional[str] = None,
    max_lines: int = 200,
) -> Dict[str, Any]:
    """Fetch failed jobs from a run + tail of their log output.

    Returns: {ok, jobs: [{name, conclusion, log_tail: str}]}
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد", "jobs": []}
    owner, name = rt

    jobs_resp = _request("GET", f"/repos/{owner}/{name}/actions/runs/{run_id}/jobs")
    if not jobs_resp.get("ok"):
        jobs_resp["jobs"] = []
        return jobs_resp

    raw_jobs = ((jobs_resp.get("data") or {}).get("jobs") or [])
    failed_jobs = [j for j in raw_jobs if j.get("conclusion") in {"failure", "timed_out"}]
    out: List[Dict[str, Any]] = []
    for job in failed_jobs[:5]:  # cap to 5 jobs to bound cost
        job_id = job.get("id")
        log_tail = ""
        if job_id:
            log_resp = _fetch_job_log_tail(owner, name, job_id, max_lines)
            log_tail = log_resp.get("log_tail", "") or ""
        out.append(
            {
                "id": job_id,
                "name": job.get("name"),
                "conclusion": job.get("conclusion"),
                "html_url": job.get("html_url"),
                "log_tail": log_tail,
            }
        )

    return {"ok": True, "status": jobs_resp.get("status"), "jobs": out}


def _fetch_job_log_tail(owner: str, name: str, job_id: int, max_lines: int) -> Dict[str, Any]:
    """Logs endpoint returns a redirect to plain text — fetch tail only."""
    token = _get_token()
    if not token:
        return {"log_tail": ""}
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{name}/actions/jobs/{job_id}/logs"
    try:
        resp = requests.get(
            url,
            headers=_make_headers(),
            timeout=_TIMEOUT_SECS,
            allow_redirects=True,
            stream=True,
        )
        if resp.status_code != 200:
            return {"log_tail": ""}
        # Read up to ~64KB then tail
        chunks: List[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > 256 * 1024:
                break
        text = b"".join(chunks).decode("utf-8", errors="replace")
        lines = text.splitlines()
        tail = "\n".join(lines[-max_lines:])
        return {"log_tail": tail}
    except requests.exceptions.RequestException as exc:
        logger.debug("[github_api] log fetch failed: %s", exc)
        return {"log_tail": ""}


# Pull Request
def create_pull_request(
    *,
    head: str,
    base: Optional[str] = None,
    title: str,
    body: str = "",
    draft: bool = False,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Open a PR from `head` to `base` (default: repo's default branch)."""
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt

    if not base:
        info = get_default_branch(repo=repo)
        if not info.get("ok"):
            return info
        base = info.get("default_branch") or "main"

    result = _request(
        "POST",
        f"/repos/{owner}/{name}/pulls",
        json_body={
            "title": title[:250],
            "head": head,
            "base": base,
            "body": body[:65000],
            "draft": draft,
        },
    )
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    return {
        "ok": True,
        "status": result.get("status"),
        "number": data.get("number"),
        "html_url": data.get("html_url"),
        "draft": data.get("draft", False),
    }


def is_configured() -> bool:
    """True if both token + default repo are configured."""
    return bool(_get_token()) and bool(_split_repo(_get_default_repo()))


# Issues (used by incident reporter, M5)
def create_issue(
    *,
    title: str,
    body: str = "",
    labels: Optional[List[str]] = None,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Open a new GitHub issue on `repo` (defaults to GITHUB_DEFAULT_REPO).

    Returns:
        {ok, status, number, html_url, error?}
    """
    repo = repo or _get_default_repo()
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    body_json: Dict[str, Any] = {"title": title[:250], "body": body}
    if labels:
        body_json["labels"] = [str(label)[:50] for label in labels][:10]
    result = _request("POST", f"/repos/{owner}/{name}/issues", json_body=body_json)
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    return {
        "ok": True,
        "status": result.get("status"),
        "number": data.get("number"),
        "html_url": data.get("html_url", ""),
    }


# GitHub Pages (M7)
def enable_pages(
    *,
    repo: str,
    source_branch: str = "main",
    source_path: str = "/",
) -> Dict[str, Any]:
    """Enable GitHub Pages for `repo`, deploying from `source_branch:source_path`.

    Idempotent: a 409 from GitHub means Pages is already enabled and we
    return ok=True with the existing config. Any other non-2xx is
    reported back unchanged so callers can decide what to do.

    Returns:
        {ok, status, url, source_branch, source_path, already_enabled, error?}
    """
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    body = {
        "source": {"branch": source_branch, "path": source_path},
    }
    result = _request(
        "POST",
        f"/repos/{owner}/{name}/pages",
        json_body=body,
        headers_extra={"Accept": "application/vnd.github+json"},
        accept_404=True,
    )
    already_enabled = False
    if not result.get("ok") and result.get("status") == 409:
        # Pages already on — fetch the live config instead of failing.
        already_enabled = True
        result = _request(
            "GET",
            f"/repos/{owner}/{name}/pages",
            headers_extra={"Accept": "application/vnd.github+json"},
            accept_404=True,
        )
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    return {
        "ok": True,
        "status": result.get("status"),
        "url": data.get("html_url", ""),
        "source_branch": (data.get("source") or {}).get("branch", source_branch),
        "source_path": (data.get("source") or {}).get("path", source_path),
        "already_enabled": already_enabled,
    }


def add_repo_topics(repo: str, topics: List[str]) -> Dict[str, Any]:
    """Merge `topics` into the repo's existing topics (idempotent).

    GitHub's topics endpoint replaces the whole set, so we GET the current
    list first and PUT the union. Used to tag Sandy's projects with `sandy`
    so the website's Projects page can auto-list them.

    Returns {ok, status, topics, unchanged?, error?}.
    """
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    want = [str(t).strip().lower() for t in (topics or []) if str(t).strip()]
    if not want:
        return {"ok": False, "status": 0, "error": "topics فاضية"}

    current = _request("GET", f"/repos/{owner}/{name}/topics")
    existing = list((current.get("data") or {}).get("names") or []) if current.get("ok") else []
    merged = list(dict.fromkeys([*existing, *want]))  # dedupe, keep order
    if set(merged) == set(existing):
        return {"ok": True, "status": current.get("status"), "topics": existing, "unchanged": True}

    result = _request("PUT", f"/repos/{owner}/{name}/topics", json_body={"names": merged})
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    return {"ok": True, "status": result.get("status"), "topics": data.get("names") or merged}


def get_pages_url(repo: str) -> Dict[str, Any]:
    """Read the configured Pages URL for `repo`. Returns ok=True with
    url='' when Pages isn't enabled (404), so callers can branch on the
    URL rather than the error."""
    rt = _split_repo(repo)
    if not rt:
        return {"ok": False, "status": 0, "error": "repo غير محدد"}
    owner, name = rt
    result = _request(
        "GET",
        f"/repos/{owner}/{name}/pages",
        headers_extra={"Accept": "application/vnd.github+json"},
        accept_404=True,
    )
    if not result.get("ok"):
        return result
    if result.get("status") == 404:
        return {"ok": True, "url": "", "enabled": False}
    data = result.get("data") or {}
    return {
        "ok": True,
        "enabled": True,
        "url": data.get("html_url", ""),
        "status_field": data.get("status"),  # 'built' | 'building' | 'errored' | null
    }
