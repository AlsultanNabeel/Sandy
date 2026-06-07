"""
Sandy Heroku Tool (Tasks 39 + 40 + 41).

Reads Heroku logs, monitors dyno health, manages restarts via the
Heroku Platform API, and tracks GitHub Actions build status.
Never restarts without an explicit user request.

Requires env vars:
    HEROKU_API_KEY       — Bearer token from Heroku dashboard → Account → API Key
    HEROKU_APP_NAME      — e.g. "sandy-robot"
    GITHUB_TOKEN         — GitHub API token (for build history)
    GITHUB_REPO          — GitHub repo in format "owner/repo" (for builds)

Public API:
    get_logs(lines)              -> str          (Task 39)
    diagnose_logs(log_text)      -> List[str]    (Task 39)
    get_dyno_status()            -> Dict         (Task 40)
    restart_dyno()               -> str          (Task 40)
    get_dyno_hours_used()        -> Dict         (Task 40)
    get_latest_build()           -> Dict         (Task 41)
    analyze_build_logs(text)     -> Dict         (Task 41)
    format_heroku_report(...)    -> str
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import requests


def _API_KEY() -> str:
    return os.getenv("HEROKU_API_KEY", "").strip()


def _APP_NAME() -> str:
    return os.getenv("HEROKU_APP_NAME", "sandy-robot").strip()


_BASE = "https://api.heroku.com"


def _HEADERS() -> dict:
    return {
        "Authorization": f"Bearer {_API_KEY()}",
        "Accept": "application/vnd.heroku+json; version=3",
        "Content-Type": "application/json",
    }


_TIMEOUT = 15

# Known Heroku error codes we scan logs for
_ERROR_PATTERNS: List[Dict[str, str]] = [
    {"code": "H10", "regex": r"H10|App crashed", "label": "🔴 H10 — App Crashed"},
    {
        "code": "H12",
        "regex": r"H12|Request timeout",
        "label": "🟠 H12 — Request Timeout",
    },
    {"code": "R14", "regex": r"R14|Memory quota", "label": "🟠 R14 — Memory Exceeded"},
    {
        "code": "R15",
        "regex": r"R15|Memory quota greatly exceeded",
        "label": "🔴 R15 — Memory Critical",
    },
    {
        "code": "503",
        "regex": r"503 Service Unavailable",
        "label": "🟠 503 — Service Unavailable",
    },
    {
        "code": "H14",
        "regex": r"H14|No web processes",
        "label": "🔴 H14 — No Web Dynos Running",
    },
]

_DYNO_HOURS_WARNING_PCT = 80  # warn when >= 80% of monthly hours used


def _require_key() -> None:
    if not _API_KEY():
        raise EnvironmentError(
            "HEROKU_API_KEY غير مضبوط — أضفه في Heroku config vars أو .env"
        )


# Task 39: Logs


def get_logs(lines: int = 100) -> str:
    """
    Fetch the last `lines` lines from the Heroku app log.
    Returns plain text log content.
    """
    _require_key()
    app = _APP_NAME()

    # Step 1: create a log session
    resp = requests.post(
        f"{_BASE}/apps/{app}/log-sessions",
        headers=_HEADERS(),
        json={"lines": lines, "source": "app", "tail": False},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    logplex_url = resp.json().get("logplex_url", "")
    if not logplex_url:
        raise RuntimeError(
            "Heroku لم يُرجع رابط log — تحقق من الـ API key والـ app name"
        )

    # Step 2: stream the log content
    log_resp = requests.get(logplex_url, timeout=_TIMEOUT)
    log_resp.raise_for_status()
    return log_resp.text


def diagnose_logs(log_text: str) -> List[str]:
    """
    Scan log text for known Heroku error codes.
    Returns a list of Arabic problem descriptions.
    """
    issues: List[str] = []
    for p in _ERROR_PATTERNS:
        if re.search(p["regex"], log_text, re.IGNORECASE):
            issues.append(p["label"])
    return issues


# Task 40: Dyno management


def get_dyno_status() -> Dict[str, Any]:
    """
    Return the current state of all dynos for the app.
    {
        "dynos": [{"name": "web.1", "state": "up", "updated_at": "..."}],
        "all_up": True,
        "crashed": ["web.1"],
    }
    """
    _require_key()
    app = _APP_NAME()

    resp = requests.get(
        f"{_BASE}/apps/{app}/dynos",
        headers=_HEADERS(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    dynos = resp.json()

    crashed = [d["name"] for d in dynos if d.get("state") in ("crashed", "error")]
    return {
        "dynos": [
            {
                "name": d.get("name", ""),
                "state": d.get("state", "unknown"),
                "updated_at": d.get("updated_at", ""),
            }
            for d in dynos
        ],
        "all_up": len(crashed) == 0,
        "crashed": crashed,
    }


def restart_dyno(dyno_name: Optional[str] = None) -> str:
    """
    Restart a specific dyno or all dynos.
    NEVER call this without explicit user confirmation.
    Returns a status message.
    """
    _require_key()
    app = _APP_NAME()

    if dyno_name:
        url = f"{_BASE}/apps/{app}/dynos/{dyno_name}"
    else:
        url = f"{_BASE}/apps/{app}/dynos"

    resp = requests.delete(url, headers=_HEADERS(), timeout=_TIMEOUT)
    resp.raise_for_status()
    target = dyno_name or "all dynos"
    return f"✅ تم إعادة تشغيل {target} بنجاح."


def get_dyno_hours_used() -> Dict[str, Any]:
    """
    Return current-month dyno hour usage for the app.
    {
        "hours_used": 450,
        "hours_quota": 550,
        "pct_used": 81.8,
        "warning": True,
    }
    Returns empty dict if the API endpoint is unavailable.
    """
    _require_key()
    try:
        resp = requests.get(
            f"{_BASE}/account/app-usage-current",
            headers=_HEADERS(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        usage_list = (
            resp.json()
        )  # list of {addons:[], app:{name:...}, dyno_hours:{...}}

        app = _APP_NAME()
        for entry in (usage_list if isinstance(usage_list, list) else []):
            if entry.get("app", {}).get("name", "") == app:
                dh = entry.get("dyno_hours") or {}
                # Heroku doesn't expose the dyno-hour quota via this API, and it
                # varies by tier (free/eco = 550h/month). Make it configurable so
                # paid tiers don't get false warnings; default to the eco quota.
                try:
                    quota = int(os.getenv("HEROKU_DYNO_HOURS_QUOTA", "550"))
                except ValueError:
                    quota = 550
                used = sum(dh.values()) if isinstance(dh, dict) else 0
                pct = round(used / quota * 100, 1) if quota else 0
                return {
                    "hours_used": used,
                    "hours_quota": quota,
                    "pct_used": pct,
                    "warning": pct >= _DYNO_HOURS_WARNING_PCT,
                    "breakdown": dh,
                }
    except Exception:
        pass
    return {}


# Formatting


def format_heroku_report(
    *,
    logs: Optional[str] = None,
    issues: Optional[List[str]] = None,
    dyno_status: Optional[Dict] = None,
    hours: Optional[Dict] = None,
) -> str:
    """Compose a human-readable Heroku health report in Arabic."""
    parts: List[str] = ["🖥️ *تقرير Heroku:*"]

    # Dyno status
    if dyno_status:
        if dyno_status.get("all_up"):
            parts.append("✅ جميع الـ Dynos تعمل بشكل طبيعي.")
        else:
            crashed = ", ".join(dyno_status.get("crashed", []))
            parts.append(f"🔴 Dynos متوقفة: `{crashed}`")
        for d in dyno_status.get("dynos", []):
            icon = "🟢" if d["state"] == "up" else "🔴"
            parts.append(f"  {icon} `{d['name']}` — {d['state']}")

    # Hour usage
    if hours:
        pct = hours.get("pct_used", 0)
        used = hours.get("hours_used", 0)
        quota = hours.get("hours_quota", 550)
        bar = "▓" * int(pct / 10) + "░" * (10 - int(pct / 10))
        warn = " ⚠️ اقتربت من الحد!" if hours.get("warning") else ""
        parts.append(f"\n⏱️ Dyno Hours: {used}/{quota}h [{bar}] {pct}%{warn}")

    # Detected issues
    if issues:
        parts.append("\n⚠️ *مشاكل مكتشفة:*")
        parts.extend(f"  • {issue}" for issue in issues)
    elif logs is not None:
        parts.append("✅ لا توجد أخطاء معروفة في الـ logs.")

    # Last log lines
    if logs:
        tail = "\n".join(logs.strip().splitlines()[-10:])
        parts.append(f"\n📋 *آخر 10 أسطر:*\n```\n{tail[:1200]}\n```")

    return "\n".join(parts)


# Task 41: Build & Deploy Monitoring

_BUILD_ERROR_PATTERNS: List[Dict[str, Any]] = [
    {
        "code": "MISSING_IMPORT",
        "regex": r"ModuleNotFoundError|ImportError|No module named",
        "label": "📦 مكتبة ناقصة",
        "detail": "أضفها في requirements.txt",
    },
    {
        "code": "SYNTAX_ERROR",
        "regex": r"SyntaxError|IndentationError|invalid syntax",
        "label": "❌ خطأ في الصيغة",
        "detail": "تحقق من قواعد Python syntax",
    },
    {
        "code": "PYTHON_VERSION",
        "regex": r"python.*version|pyenv|requires python|python 3\.[0-9]+ or",
        "label": "🐍 إصدار Python غير متوافق",
        "detail": "تحقق من الإصدار في runtime.txt",
    },
    {
        "code": "DEPENDENCY_CONFLICT",
        "regex": r"ERROR: pip's dependency resolver|conflict|incompatible",
        "label": "⚙️ تضارب في التبعيات",
        "detail": "حدّث requirements.txt",
    },
    {
        "code": "BUILD_FAILURE",
        "regex": r"build failed|failure",
        "label": "🏗️ فشل البناء",
        "detail": "راجع build logs",
    },
]


def get_latest_build() -> Dict[str, Any]:
    """
    Fetch the latest build from GitHub Actions for the repo.
    Returns:
    {
        "status": "success|failure|in_progress",
        "id": "...",
        "branch": "main",
        "commit": "abc123...",
        "message": "Commit message",
        "created_at": "2026-05-06T...",
        "logs_url": "...",
        "conclusion": "success|failure|neutral|cancelled"
    }
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPO", "").strip()

    if not token or not repo:
        return {
            "status": "unknown",
            "error": "GITHUB_TOKEN أو GITHUB_REPO لم تُضبط",
        }

    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

        # Get latest workflow runs
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/runs?per_page=1",
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        runs = data.get("workflow_runs", [])

        if not runs:
            return {"status": "no_runs", "message": "لا توجد build runs"}

        run = runs[0]
        return {
            "status": (
                "in_progress" if run["status"] == "in_progress" else run["conclusion"]
            ),
            "id": run.get("id"),
            "branch": run.get("head_branch"),
            "commit": run.get("head_sha", "")[:7],
            "message": run.get("name", ""),
            "created_at": run.get("created_at"),
            "logs_url": run.get("html_url"),
            "conclusion": run.get("conclusion", "pending"),
            "timestamp": run.get("updated_at"),
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"فشل جلب البناء: {str(e)[:100]}",
        }


def analyze_build_logs(log_text: str) -> Dict[str, Any]:
    """
    Analyze build logs for known error patterns.
    Returns:
    {
        "has_errors": bool,
        "errors": [
            {"code": "...", "label": "...", "detail": "..."},
            ...
        ]
    }
    """
    errors: List[Dict[str, str]] = []

    for pattern in _BUILD_ERROR_PATTERNS:
        # The generic "build failed/failure" catch-all only fires when no more
        # specific pattern matched — otherwise it just adds noise alongside them.
        if pattern["code"] == "BUILD_FAILURE" and errors:
            continue
        if re.search(pattern["regex"], log_text, re.IGNORECASE):
            errors.append(
                {
                    "code": pattern["code"],
                    "label": pattern["label"],
                    "detail": pattern["detail"],
                }
            )

    return {
        "has_errors": len(errors) > 0,
        "errors": errors,
    }


def format_build_alert(build_status: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    """
    Format a build alert for Telegram notification.
    """
    parts: List[str] = []

    # `status` is the single source of truth: get_latest_build folds the GitHub
    # `conclusion` into it (and uses "in_progress" while a run is still going).
    state = build_status.get("status", "unknown")
    if state == "error":
        parts.append(f"⚠️ *خطأ في جلب البناء:* {build_status.get('error')}")
        return "\n".join(parts)

    commit = build_status.get("commit", "?")[:7]
    branch = build_status.get("branch", "main")

    if state == "failure":
        parts.append(
            f"🔴 *نبيل، الـ Build فشل!*\n"
            f"Branch: `{branch}` | Commit: `{commit}`\n"
            f"Created: {build_status.get('created_at', '?')[:10]}"
        )

        if analysis.get("has_errors"):
            parts.append("\n*أسباب محتملة:*")
            for err in analysis.get("errors", []):
                parts.append(f"  • {err['label']}")
                parts.append(f"    💡 {err['detail']}")
        else:
            parts.append("⚠️ قد تكون هناك أخطاء أخرى — فتش الـ logs")

        parts.append(f"\n🔗 [عرض الـ Logs]({build_status.get('logs_url')})")
        parts.append("\n*هل تريد اللحاق بـ push جديد؟*")

    elif state == "success":
        parts.append(f"✅ *البناء نجح!*\n" f"Branch: `{branch}` | Commit: `{commit}`")
    else:
        parts.append(f"⏳ Build في حالة: {state}")

    return "\n".join(parts)
