"""Pre-PR self-review for the Project Builder (M10).

After every feature is built and before the PR is opened, scan the branch
for two kinds of inconsistency:

1. Suspicious placeholders in HTML entry points: href="#",
   aria-disabled="true", or text like `قريباً` / `coming soon` / `TODO`
   next to link or feature markup. These usually mean a feature was
   scaffolded on disk but never wired into the homepage.

2. Broken local refs: every relative href / src / link target in an HTML
   file should resolve to a real file on the branch.

The checks are kept narrow. Anything we can't verify cheaply falls through
as a non-issue, and the agent's own tools can dig deeper during the
correction round if needed.

run_review() returns a small dict the caller can drop straight into
Telegram or a PR body.
"""

from __future__ import annotations

import logging
import os
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Set

from app.integrations import github_api

logger = logging.getLogger(__name__)


_SUSPICIOUS_TEXT_PATTERNS = (
    re.compile(r"قريب[اًةًا]?", re.IGNORECASE),
    re.compile(r"\bcoming soon\b", re.IGNORECASE),
    re.compile(r"\btodo\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
)

# Schemes we treat as external (skip existence checks).
_EXTERNAL_SCHEMES = ("http:", "https:", "mailto:", "tel:", "data:", "javascript:", "//")

# File extensions whose internal links we validate. Limit to HTML for now —
# CSS/JS imports vary too much across stacks to check naively.
_HTML_EXTS = (".html", ".htm")


def _is_review_enabled() -> bool:
    """Feature flag — set SANDY_SELF_REVIEW=off to disable in prod."""
    return os.getenv("SANDY_SELF_REVIEW", "on").strip().lower() not in (
        "off", "0", "false", "no"
    )


class _LinkHarvester(HTMLParser):
    """Pull every href/src/data-* link plus surrounding text out of an HTML
    document. The stdlib parser is forgiving enough for the generated content
    we deal with, so malformed markup is fine."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Dict[str, Any]] = []
        self._current_anchor: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs):  # type: ignore[override]
        attrs_d = {k: (v or "") for k, v in attrs}
        href = attrs_d.get("href", "")
        src = attrs_d.get("src", "")
        target = href or src
        if tag == "a":
            self._current_anchor = {
                "tag": tag,
                "href": href,
                "aria_disabled": attrs_d.get("aria-disabled", "").lower() == "true",
                "text": "",
            }
            if target:
                self.links.append(self._current_anchor)
            return
        if target:
            self.links.append({
                "tag": tag,
                "href": target,
                "aria_disabled": False,
                "text": "",
            })

    def handle_endtag(self, tag: str):  # type: ignore[override]
        if tag == "a":
            self._current_anchor = None

    def handle_data(self, data: str):  # type: ignore[override]
        if self._current_anchor is not None:
            self._current_anchor["text"] += data


def _list_branch_files(repo: str, branch: str) -> Set[str]:
    """Snapshot all blob paths on `branch`. The returned set is normalized:
    leading './' stripped and paths lowercased, so lookups are
    case-insensitive (most static-site hosts serve paths that way)."""
    tree = github_api.list_repo_tree(repo=repo, ref=branch)
    if not tree.get("ok"):
        logger.warning(
            "[self_review] couldn't fetch tree for %s@%s: %s",
            repo, branch, tree.get("error") or tree.get("status"),
        )
        return set()
    paths = tree.get("paths") or []
    return {p.lower().lstrip("./") for p in paths if isinstance(p, str)}


def _normalize_ref(ref: str, from_file: str) -> str:
    """Resolve a relative ref against the page that contains it.

    `from_file` is the path of the HTML file the link lives in. Returns
    a normalized lowercase path with no leading `./` or `/`.
    """
    # Strip query + fragment
    ref = ref.split("#", 1)[0].split("?", 1)[0].strip()
    if not ref:
        return ""
    # Absolute (from repo root)
    if ref.startswith("/"):
        return ref.lstrip("/").lower()
    # Relative — anchor to the directory of `from_file`
    base = from_file.rsplit("/", 1)[0] if "/" in from_file else ""
    parts = (base.split("/") if base else []) + ref.split("/")
    stack: List[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if stack:
                stack.pop()
            continue
        stack.append(p)
    return "/".join(stack).lower()


def _is_external(ref: str) -> bool:
    low = ref.lower()
    return low.startswith(_EXTERNAL_SCHEMES)


def _fetch_html(repo: str, branch: str, path: str) -> str:
    res = github_api.get_file_contents(path, repo=repo, ref=branch)
    if not res.get("ok"):
        return ""
    return res.get("content") or ""


def _check_html_file(
    repo: str,
    branch: str,
    path: str,
    branch_files: Set[str],
) -> List[Dict[str, Any]]:
    """Run all HTML-level checks on a single file. Returns a list of
    issue dicts — empty if everything looks fine."""
    html = _fetch_html(repo, branch, path)
    if not html:
        return []

    issues: List[Dict[str, Any]] = []
    parser = _LinkHarvester()
    try:
        parser.feed(html)
    except Exception as exc:  # malformed HTML — log but don't fail review
        logger.debug("[self_review] HTML parse failed on %s: %s", path, exc)
        return []

    for link in parser.links:
        href = (link.get("href") or "").strip()
        text = (link.get("text") or "").strip()

        # Placeholder anchors: href="#" + disabled or suspicious text
        if link.get("tag") == "a" and (href in ("", "#")):
            if link.get("aria_disabled") or any(
                p.search(text) for p in _SUSPICIOUS_TEXT_PATTERNS
            ):
                issues.append({
                    "kind": "placeholder_link",
                    "file": path,
                    "detail": (
                        f"رابط placeholder: text={text[:60]!r} href={href!r} "
                        f"(غالباً ميزة مبنية بس مش مربوطة بالصفحة)"
                    ),
                })
            continue

        if _is_external(href) or href.startswith("#"):
            continue

        normalized = _normalize_ref(href, path)
        if not normalized:
            continue
        if normalized in branch_files:
            continue
        # Allow directory references that have an index.html
        if (normalized + "/index.html") in branch_files:
            continue
        issues.append({
            "kind": "broken_ref",
            "file": path,
            "detail": (
                f"الرابط/المصدر `{href}` ما بيوصل لملف موجود على البرانش "
                f"(محاولة الـ resolve: `{normalized}`)"
            ),
        })

    # Free-text placeholder scan (catches "قريباً" inside non-link markup too)
    for pat in _SUSPICIOUS_TEXT_PATTERNS:
        m = pat.search(html)
        if not m:
            continue
        # Only flag if the same file isn't already flagged with a more
        # specific placeholder_link entry — keeps noise down.
        if any(i["kind"] == "placeholder_link" and i["file"] == path for i in issues):
            continue
        snippet = html[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
        issues.append({
            "kind": "suspicious_text",
            "file": path,
            "detail": f"نص مشبوه قرب: `…{snippet.strip()}…`",
        })
        break  # one suspicious_text per file is enough

    return issues


def run_review(
    *,
    repo: str,
    branch: str,
    plan: Dict[str, Any],
    applied_files: List[str],
) -> Dict[str, Any]:
    """Run the full pre-PR review.

    Returns:
        {
            "ok": True,
            "skipped": bool,        # true if env-disabled or no HTML files
            "issues": List[Dict],   # see _check_html_file for shape
            "blocking": bool,       # currently mirrors `len(issues) > 0`
            "html_files_scanned": int,
        }
    """
    _ = plan  # reserved for future feature-coverage checks
    if not _is_review_enabled():
        return {"ok": True, "skipped": True, "issues": [], "blocking": False, "html_files_scanned": 0}

    if not repo or not branch:
        return {"ok": True, "skipped": True, "issues": [], "blocking": False, "html_files_scanned": 0}

    branch_files = _list_branch_files(repo, branch)
    if not branch_files:
        return {"ok": True, "skipped": True, "issues": [], "blocking": False, "html_files_scanned": 0}

    # Limit the scan to HTML files the agent actually touched, or fall back
    # to every HTML on the branch when the applied list is empty (resume case).
    candidates: List[str] = []
    if applied_files:
        for f in applied_files:
            if isinstance(f, str) and f.lower().endswith(_HTML_EXTS):
                candidates.append(f)
    if not candidates:
        candidates = [f for f in branch_files if f.endswith(_HTML_EXTS)]
    # Deduplicate while preserving order
    seen: Set[str] = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    issues: List[Dict[str, Any]] = []
    for path in candidates[:25]:  # cap — long-tail files unlikely to matter
        issues.extend(_check_html_file(repo, branch, path, branch_files))

    return {
        "ok": True,
        "skipped": False,
        "issues": issues,
        "blocking": bool(issues),
        "html_files_scanned": len(candidates),
    }


def format_issues_for_owner(issues: List[Dict[str, Any]], *, limit: int = 8) -> str:
    """Arabic summary of the issues for Telegram or a PR body."""
    if not issues:
        return ""
    lines = [f"⚠️ {len(issues)} ملاحظة من المراجعة الذاتية:"]
    for issue in issues[:limit]:
        lines.append(f"• `{issue.get('file', '?')}` — {issue.get('detail', '')}")
    if len(issues) > limit:
        lines.append(f"… و{len(issues) - limit} ملاحظات أخرى.")
    return "\n".join(lines)


def format_correction_task(issues: List[Dict[str, Any]]) -> str:
    """Build a focused agent-loop task description that asks the agent to
    fix the listed issues. Keeps it directive — `done` when finished."""
    bullets = []
    for issue in issues[:20]:
        bullets.append(f"- [{issue.get('kind')}] {issue.get('file')}: {issue.get('detail')}")
    return (
        "🔧 جولة تصحيح ذاتية — راجعت شغلك ولقيت الملاحظات التالية:\n\n"
        + "\n".join(bullets)
        + "\n\nصلّحيهم باستخدام apply_patch (أو write_new_file لو الملف فعلاً ناقص). "
        "بعد ما تخلصي، نادي done(summary) بعربي يلخّص اللي عدّلتيه. لا تفتحي PR — "
        "أنا اللي بيفتحها."
    )
