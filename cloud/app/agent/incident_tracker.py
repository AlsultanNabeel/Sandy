"""Incident tracker + auto-issue escalator (M5).

Three sources push signals in: the Heroku log/dyno watcher, the Sentry
webhook, and the GitHub Actions build webhook. Each signal becomes a
`(source, category)` key with a 1-hour ring buffer of timestamps.

When a key crosses `_THRESHOLD` events within `_WINDOW_SECONDS`, the next
call to `maybe_escalate()` opens a GitHub issue on Sandy's main repo, so
the spike outlives the dyno restart that would wipe this in-process state.
The same key stays quiet for `_DEDUPE_SECONDS` after that; the open issue
is the one to update or close.

State lives in process memory, not Redis. Crossing the threshold is what
matters; losing a count on restart is fine. The escalation step swallows
its own errors so GitHub being down can't crash an alerter."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from app.integrations import github_api

logger = logging.getLogger(__name__)

# Window we look back over when deciding "is this spiking?"
_WINDOW_SECONDS = 60 * 60
# Number of events within the window to count as a spike worth filing.
_THRESHOLD = 3
# How long we suppress a duplicate auto-issue for the same key.
_DEDUPE_SECONDS = 24 * 60 * 60

_lock = threading.Lock()
_incidents: Dict[str, Deque[float]] = {}
_opened_issues: Dict[str, Dict[str, Any]] = {}


def _make_key(source: str, category: str) -> str:
    src = (source or "unknown").strip().lower()
    cat = (category or "").strip().lower()[:120]
    return f"{src}::{cat}"


def _prune(buf: Deque[float], now: float) -> None:
    cutoff = now - _WINDOW_SECONDS
    while buf and buf[0] < cutoff:
        buf.popleft()


def record(source: str, category: str) -> str:
    """Register one event and return its dedupe key. Cheap; call it freely."""
    key = _make_key(source, category)
    now = time.time()
    with _lock:
        buf = _incidents.setdefault(key, deque())
        _prune(buf, now)
        buf.append(now)
    return key


def recent_count(key: str) -> int:
    """How many events the key has accumulated in the rolling window."""
    now = time.time()
    with _lock:
        buf = _incidents.get(key)
        if not buf:
            return 0
        _prune(buf, now)
        return len(buf)


def should_open_issue(key: str) -> bool:
    now = time.time()
    with _lock:
        existing = _opened_issues.get(key)
        if existing and (now - existing.get("ts", 0)) < _DEDUPE_SECONDS:
            return False
        buf = _incidents.get(key)
        if not buf:
            return False
        _prune(buf, now)
        return len(buf) >= _THRESHOLD


def mark_issue_opened(key: str, issue_url: str) -> None:
    now = time.time()
    with _lock:
        # Drop entries past the dedupe window so the dict can't grow forever.
        for k in [k for k, v in _opened_issues.items()
                  if now - v.get("ts", 0) >= _DEDUPE_SECONDS]:
            del _opened_issues[k]
        _opened_issues[key] = {"url": issue_url, "ts": now}


def _build_issue_body(
    *,
    source: str,
    category: str,
    count: int,
    detail: str,
    evidence: str,
) -> str:
    parts = [
        "## Auto-detected recurring incident",
        "",
        f"- **Source:** `{source}`",
        f"- **Category:** `{category[:200]}`",
        f"- **Frequency:** {count} occurrences in the last hour",
        f"- **First spotted:** ~{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "_Filed automatically by Sandy. Close this issue once the "
        "underlying cause is fixed._",
    ]
    if detail:
        parts.append("")
        parts.append("### Detail")
        parts.append(detail[:1500])
    if evidence:
        parts.append("")
        parts.append("### Evidence (tail)")
        parts.append("```")
        parts.append(evidence[:2000])
        parts.append("```")
    return "\n".join(parts)


def maybe_escalate(
    *,
    source: str,
    category: str,
    detail: str = "",
    evidence: str = "",
    notify: bool = True,
) -> Optional[str]:
    """Record an incident; if the key just crossed the threshold,
    open a GitHub issue and (optionally) ping Telegram with the link.
    Returns the issue URL when it opens one this call, else None."""
    key = record(source, category)
    if not should_open_issue(key):
        return None
    count = recent_count(key)
    repo = os.getenv("GITHUB_DEFAULT_REPO", "").strip()
    if not repo:
        logger.warning(
            "[incident_tracker] threshold crossed for %s but GITHUB_DEFAULT_REPO unset",
            key,
        )
        return None
    title = f"[Auto] {source}: {category[:80] or 'recurring incident'}"
    body = _build_issue_body(
        source=source, category=category, count=count,
        detail=detail, evidence=evidence,
    )
    try:
        res = github_api.create_issue(
            title=title, body=body, labels=["sandy-auto", source], repo=repo,
        )
    except Exception as exc:
        logger.warning("[incident_tracker] create_issue raised: %s", exc)
        return None
    if not res.get("ok"):
        logger.info(
            "[incident_tracker] create_issue failed for %s: %s",
            key, res.get("error") or res.get("status"),
        )
        return None
    url = res.get("html_url", "")
    if not url:
        return None
    mark_issue_opened(key, url)
    if notify:
        try:
            from app.agent.project_builder import notifier
            notifier.notify_owner(
                f"💀 خلل متكرر من `{source}` — فتحت GitHub issue تلقائياً.\n"
                f"التكرار: {count} مرة في آخر ساعة.\n{url}"
            )
        except Exception as exc:
            logger.debug("[incident_tracker] notify failed: %s", exc)
    logger.info(
        "[incident_tracker] auto-issue opened key=%s url=%s",
        key, url,
    )
    return url


def reset_for_testing() -> None:
    """Test hook: drop all tracked state."""
    with _lock:
        _incidents.clear()
        _opened_issues.clear()
