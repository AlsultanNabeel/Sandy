"""MCP-backed tools — memory + fetch + github.

كل adapter يستدعي MCPHub الذي يتحدث مع Node.js subprocess.
إذا Node.js غير متاح يرجع رد graceful بدل crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext


# Memory adapters — stored in MongoDB so they survive restarts.

_COLL = "sandy_memories"


def _mem_db(ctx: "DispatchContext"):
    return ctx.mongo_db[_COLL] if ctx.mongo_db is not None else None


def memory_store(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    content = str(args.get("content", "")).strip()
    if not content:
        return {"handled": True, "reply": "شو اللي تريديني أحفظه؟"}

    coll = _mem_db(ctx)
    if coll is None:
        return {"handled": True, "reply": "دوّنتها 📝"}  # graceful in tests

    from datetime import datetime, timezone
    chat_id = str((ctx.state or {}).get("chat_id", "default"))
    coll.insert_one({
        "chat_id": chat_id,
        "label": str(args.get("label") or "user_fact").strip(),
        "content": content,
        "created_at": datetime.now(timezone.utc),
    })
    return {"handled": True, "reply": "دوّنتها 📝"}


def memory_recall(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"handled": True, "reply": "شو تبحثين عنه في ذاكرتي؟"}

    coll = _mem_db(ctx)
    if coll is None:
        return {"handled": True, "reply": "ما عندي ذكريات محفوظة بعد."}

    chat_id = str((ctx.state or {}).get("chat_id", "default"))

    # Try regex match on content first
    docs = list(coll.find(
        {"chat_id": chat_id, "content": {"$regex": query, "$options": "i"}},
        {"_id": 0, "content": 1},
        limit=10,
    ))

    # Broad query (e.g. "شو تعرفيه عني") → return all
    if not docs:
        docs = list(coll.find(
            {"chat_id": chat_id},
            {"_id": 0, "content": 1},
            sort=[("created_at", -1)],
            limit=20,
        ))

    if not docs:
        return {"handled": True, "reply": "ما عندي ذكريات محفوظة بعد."}

    return {"handled": True, "reply": "\n".join(f"• {d['content']}" for d in docs)}


# Fetch adapter — plain requests, no MCP needed.

def _html_to_text(html: str, max_length: int = 4000) -> str:
    """يشيل HTML tags ويرجع نص نظيف مقسم بفقرات."""
    import re
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # كسر السطر عند عناصر الهيكل
    text = re.sub(r"<(?:br|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<(?:h[1-6])[^>]*>", "\n## ", text, flags=re.IGNORECASE)
    text = re.sub(r"<(?:li)[^>]*>", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    # دمج السطور الفارغة المتكررة
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length] + "\n…"
    return text


_SUMMARIZE_THRESHOLD = 1500  # حروف — فوقها نلخص بالـ AI


def _ai_summarize(text: str, url: str, fn) -> str:
    """يلخص النص الطويل عبر LLM ويرجع ملخص عربي موجز."""
    try:
        messages = [
            {
                "role": "system",
                "content": "أنت مساعد. لخّص النص التالي بالعربية في فقرة واحدة أو فقرتين بلغة سهلة ومفهومة.",
            },
            {
                "role": "user",
                "content": f"الرابط: {url}\n\n{text[:8000]}",
            },
        ]
        result = fn(messages=messages, max_tokens=600, temperature=0.3)
        if isinstance(result, str):
            return result.strip()
        # Azure/OpenAI response object
        return result.choices[0].message.content.strip()
    except Exception:
        return text[:1500] + "\n…"


def fetch_url(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    import requests

    url = str(args.get("url", "")).strip()
    if not url:
        return {"handled": True, "reply": "أعطيني الرابط."}

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Sandy-Bot/1.0"})
        resp.raise_for_status()
        text = _html_to_text(resp.text, max_length=8000)
        if not text:
            return {"handled": True, "reply": "الصفحة فارغة أو ما فيها نص."}

        if len(text) > _SUMMARIZE_THRESHOLD and ctx.create_chat_completion_fn:
            summary = _ai_summarize(text, url, ctx.create_chat_completion_fn)
            return {"handled": True, "reply": summary}

        return {"handled": True, "reply": text[:4000] + ("\n…" if len(text) > 4000 else "")}
    except Exception as exc:
        return {"handled": True, "reply": f"ما قدرت أجلب الصفحة: {exc}"}


# GitHub adapters

def _gh(tool: str, args: dict) -> Dict[str, Any]:
    from app.integrations.mcp_client import get_mcp_hub
    return get_mcp_hub().call("github", tool, args)


def _gh_repo() -> tuple[str, str, str]:
    """يقرأ owner + repo من GITHUB_DEFAULT_REPO فقط."""
    import os
    default = os.environ.get("GITHUB_DEFAULT_REPO", "/")
    owner, _, repo = default.partition("/")
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return "", "", "GITHUB_DEFAULT_REPO غير مضبوط — أضفه على Heroku بصيغة owner/repo"
    return owner, repo, ""


def _format_commits(raw: str) -> str:
    """يحوّل JSON commits لنص مقروء."""
    import json as _json
    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return raw  # مش JSON — ارجعه كما هو

    if not isinstance(data, list):
        return raw

    import os
    repo = os.environ.get("GITHUB_DEFAULT_REPO", "")
    base_url = f"https://github.com/{repo}/commit/" if repo else ""

    lines = []
    for c in data:
        sha = c.get("sha", "")
        sha7 = sha[:7]
        commit = c.get("commit") or {}
        msg = (commit.get("message") or "").split("\n")[0][:80]
        author = (commit.get("author") or {}).get("name", "")
        date = (commit.get("author") or {}).get("date", "")[:10]
        link = f"\n  🔗 {base_url}{sha}" if base_url else ""
        lines.append(f"• `{sha7}` — {msg}\n  👤 {author} | 📅 {date}{link}")

    return "\n\n".join(lines)


def github_commits(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    result = _gh("list_commits", {
        "owner": owner,
        "repo": repo,
        "page": 1,
        "perPage": int(args.get("count") or 5),
    })
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_commits(result["reply"])
    return result


def _format_issues(raw: str) -> str:
    import json as _json
    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(data, list):
        return raw
    if not data:
        return "لا توجد issues مفتوحة."
    lines = []
    for issue in data[:20]:
        num = issue.get("number", "")
        title = (issue.get("title") or "")[:80]
        state = issue.get("state", "")
        label_names = [lb.get("name", "") for lb in (issue.get("labels") or [])]
        labels = " · ".join(label_names) if label_names else ""
        url = issue.get("html_url", "")
        emoji = "🟢" if state == "open" else "🔴"
        line = f"{emoji} *#{num}* — {title}"
        if labels:
            line += f"\n  🏷 {labels}"
        if url:
            line += f"\n  🔗 {url}"
        lines.append(line)
    header = f"📋 *{len(data)} issues*\n\n"
    return header + "\n\n".join(lines)


def github_issues(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    result = _gh("list_issues", {
        "owner": owner,
        "repo": repo,
        "state": str(args.get("state") or "open"),
    })
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_issues(result["reply"])
    return result


def _short_title(title: str) -> str:
    """≤ ١٠ كلمات → العنوان الكامل. > ١٠ → أول ٥ كلمات + '...'"""
    words = (title or "").strip().split()
    if len(words) <= 10:
        return " ".join(words)
    return " ".join(words[:5]) + "..."


def _fetch_issue(num: int, owner: str, repo: str) -> tuple[bool, str, str, str]:
    """Pre-fetch issue object.

    Returns (exists, title, state, html_url).
    - exists=False → الـ issue غير موجود في المستودع (404)
    - exists=True مع title="" → موجود بس فشل التحليل (نادر)
    """
    result = _gh("get_issue", {"owner": owner, "repo": repo, "issue_number": num})

    # MCP يرجع handled=False مع رسالة "خطأ MCP (github): {...Not Found...}"
    if not result.get("handled"):
        reply_text = str(result.get("reply") or "")
        if "Not Found" in reply_text or "not found" in reply_text.lower() or "404" in reply_text:
            return False, "", "", ""
        return True, "", "", ""  # خطأ آخر — نسيب الـ handler يكمل

    if not result.get("reply"):
        return True, "", "", ""

    import json as _json
    try:
        data = _json.loads(result["reply"])
    except (ValueError, TypeError):
        return True, "", "", ""
    if not isinstance(data, dict):
        return True, "", "", ""
    return (
        True,
        (data.get("title") or "").strip(),
        (data.get("state") or "").strip(),
        data.get("html_url", ""),
    )


def _format_created_issue(raw: str) -> str:
    import json as _json
    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(data, dict):
        return raw
    num = data.get("number", "")
    title = (data.get("title") or "").strip()
    url = data.get("html_url", "")
    if not num and not url:
        return raw
    snippet = _short_title(title)
    return f"✅ فتحت Issue #{num} ({snippet})\n🔗 {url}"


_GENERIC_ISSUE_TITLES = {
    "issue", "Issue", "ISSUE",
    "جديد", "تجربة", "test", "Test", "TEST",
    "new", "New", "NEW",
}


def github_create_issue(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    title = str(args.get("title") or "").strip()
    if not title or title in _GENERIC_ISSUE_TITLES:
        return {"handled": True, "reply": "شو اسم الـ issue؟"}
    params: Dict[str, Any] = {"owner": owner, "repo": repo, "title": title}
    if args.get("body"):
        params["body"] = str(args["body"])
    result = _gh("create_issue", params)
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_created_issue(result["reply"])
    return result


def _format_issue_action(raw: str, prefix: str) -> str:
    import json as _json
    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(data, dict):
        return raw
    num = data.get("number", "")
    title = (data.get("title") or "").strip()
    url = data.get("html_url", "")
    if not num and not url:
        return raw
    snippet = _short_title(title)
    return f"{prefix} Issue #{num} ({snippet})\n🔗 {url}"


def _format_created_comment(raw: str, num: int = 0, title: str = "") -> str:
    import json as _json
    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(data, dict):
        return raw
    url = data.get("html_url", "")
    body = (data.get("body") or "").strip()
    body_snippet = body[:80] + ("…" if len(body) > 80 else "")
    if not url:
        return raw
    title_part = ""
    if num and title:
        title_part = f" على Issue #{num} ({_short_title(title)})"
    return f"💬 أضفت تعليق{title_part}:\n{body_snippet}\n🔗 {url}"


def _parse_issue_number(args: Dict[str, Any]) -> tuple[int, str]:
    raw = args.get("issue_number") if args.get("issue_number") is not None else args.get("number")
    try:
        num = int(raw)
        if num <= 0:
            raise ValueError
        return num, ""
    except (TypeError, ValueError):
        return 0, "حدّدي رقم الـ issue (issue_number)."


def _resolve_issue(args: Dict[str, Any], owner: str, repo: str) -> tuple[int, str]:
    """يقبل issue_number مباشرة، أو title يطابقه fuzzy مع قائمة issues."""
    # 1) رقم صريح
    if args.get("issue_number") is not None or args.get("number") is not None:
        return _parse_issue_number(args)

    # 2) عنوان نصي → ابحث
    title = str(args.get("title") or "").strip()
    if not title:
        return 0, "حدّدي رقم الـ issue (issue_number) أو عنوانه (title)."

    raw = _gh("list_issues", {"owner": owner, "repo": repo, "state": "all"})
    if not raw.get("handled") or not raw.get("reply"):
        return 0, "ما قدرت أجيب قائمة الـ issues — جربي بالرقم."

    import json as _json
    try:
        issues = _json.loads(raw["reply"])
    except (ValueError, TypeError):
        return 0, "ما قدرت أحلل قائمة الـ issues — جربي بالرقم."

    if not isinstance(issues, list) or not issues:
        return 0, f"ما لقيت issue يطابق '{title}'."

    title_lower = title.lower()
    matches = [
        i for i in issues
        if isinstance(i, dict) and title_lower in (i.get("title") or "").lower()
    ]

    if not matches:
        return 0, f"ما لقيت issue يطابق '{title}'."

    if len(matches) == 1:
        try:
            return int(matches[0].get("number") or 0), ""
        except (TypeError, ValueError):
            return 0, "رقم الـ issue من GitHub غير صالح."

    # غموض: في أكثر من match → اطلبي الرقم
    lines = [
        f"  • #{m.get('number')} — {(m.get('title') or '')[:70]}"
        for m in matches[:5]
    ]
    return 0, "في أكثر من issue يطابق:\n" + "\n".join(lines) + "\nحدّدي بالرقم."


def github_close_issue(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    num, perr = _resolve_issue(args, owner, repo)
    if perr:
        return {"handled": True, "reply": perr}

    # Pre-fetch — لو الـ issue غير موجودة، رسالة لطيفة بدل خطأ MCP raw
    exists, cur_title, cur_state, cur_url = _fetch_issue(num, owner, repo)
    if not exists:
        return {
            "handled": True,
            "reply": f"ما لقيت Issue #{num} في المستودع — تأكد من الرقم يا نبيل",
        }
    # Idempotent message لو الـ issue مغلقة أصلاً
    if cur_state == "closed":
        snippet = _short_title(cur_title)
        return {
            "handled": True,
            "reply": f"تم اقفال Issue #{num} ({snippet}) بالفعل يا نبيل\n🔗 {cur_url}",
        }

    result = _gh("update_issue", {
        "owner": owner,
        "repo": repo,
        "issue_number": num,
        "state": "closed",
    })
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_issue_action(result["reply"], "🔒 أغلقت")
    return result


def github_reopen_issue(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    num, perr = _resolve_issue(args, owner, repo)
    if perr:
        return {"handled": True, "reply": perr}

    # Pre-fetch — لو الـ issue غير موجودة، رسالة لطيفة
    exists, cur_title, cur_state, cur_url = _fetch_issue(num, owner, repo)
    if not exists:
        return {
            "handled": True,
            "reply": f"ما لقيت Issue #{num} في المستودع — تأكد من الرقم يا نبيل",
        }
    # Idempotent message لو الـ issue مفتوحة أصلاً
    if cur_state == "open":
        snippet = _short_title(cur_title)
        return {
            "handled": True,
            "reply": f"Issue #{num} ({snippet}) مفتوحة أصلاً يا نبيل\n🔗 {cur_url}",
        }

    result = _gh("update_issue", {
        "owner": owner,
        "repo": repo,
        "issue_number": num,
        "state": "open",
    })
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_issue_action(result["reply"], "🔓 فتحت من جديد")
    return result


def github_comment_issue(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    body = str(args.get("body") or "").strip()
    if not body:
        return {"handled": True, "reply": "نص التعليق فاضي — اكتبي شو بدك تضيفي."}
    num, perr = _resolve_issue(args, owner, repo)
    if perr:
        return {"handled": True, "reply": perr}

    # Pre-fetch — لو الـ issue غير موجودة، رسالة لطيفة
    exists, cur_title, _state, _url = _fetch_issue(num, owner, repo)
    if not exists:
        return {
            "handled": True,
            "reply": f"ما لقيت Issue #{num} في المستودع — تأكد من الرقم يا نبيل",
        }

    result = _gh("add_issue_comment", {
        "owner": owner,
        "repo": repo,
        "issue_number": num,
        "body": body,
    })
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_created_comment(result["reply"], num=num, title=cur_title)
    return result


def _format_file_contents(raw: str) -> str:
    import json as _json
    import base64 as _b64
    import requests as _req

    # parse first JSON object (ignore trailing content if any)
    try:
        decoder = _json.JSONDecoder()
        data, _ = decoder.raw_decode(raw.strip())
    except (ValueError, TypeError):
        return raw
    if not isinstance(data, dict):
        return raw

    name = data.get("name", "")
    path = data.get("path", "")
    size = data.get("size", 0)
    header = f"📄 *{name}* (`{path}`) — {size} bytes\n\n"

    # محاولة 1: content field (base64)
    content_b64 = data.get("content", "")
    if content_b64:
        try:
            decoded = _b64.b64decode(content_b64.replace("\n", "")).decode("utf-8", errors="replace")
            if len(decoded) > 3000:
                decoded = decoded[:3000] + "\n…"
            return header + f"```\n{decoded}\n```"
        except Exception:
            pass

    # محاولة 2: download_url → HTTP GET
    download_url = data.get("download_url", "")
    if download_url:
        try:
            resp = _req.get(download_url, timeout=10, headers={"User-Agent": "Sandy-Bot/1.0"})
            resp.raise_for_status()
            decoded = resp.text
            if len(decoded) > 3000:
                decoded = decoded[:3000] + "\n…"
            return header + f"```\n{decoded}\n```"
        except Exception:
            pass

    return raw


def github_file(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}
    path = str(args.get("path") or "").strip()
    if not path:
        return {"handled": True, "reply": "حدّد مسار الملف."}
    result = _gh("get_file_contents", {"owner": owner, "repo": repo, "path": path})
    if result.get("handled") and result.get("reply"):
        result["reply"] = _format_file_contents(result["reply"])
    return result


# CHANGELOG generator

_COMMIT_TYPE_LABELS = {
    "feat": "✨ ميزات جديدة",
    "fix": "🐛 إصلاحات",
    "refactor": "♻️ تحسينات",
    "docs": "📚 توثيق",
    "test": "🧪 اختبارات",
    "perf": "⚡ أداء",
    "chore": "🔧 صيانة",
    "style": "💄 تنسيق",
    "ci": "👷 CI/CD",
    "revert": "⏪ تراجع",
}


def _build_changelog(raw: str) -> str:
    import json as _json
    from datetime import datetime, timezone

    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return raw

    if not isinstance(data, list) or not data:
        return "لا يوجد commits لعرضها."

    groups: dict[str, list[str]] = {label: [] for label in _COMMIT_TYPE_LABELS.values()}
    groups["📌 أخرى"] = []

    for c in data:
        commit = c.get("commit") or {}
        full_msg = (commit.get("message") or "").split("\n")[0].strip()
        date = ((commit.get("author") or {}).get("date") or "")[:10]

        matched = False
        for prefix, label in _COMMIT_TYPE_LABELS.items():
            if full_msg.lower().startswith(f"{prefix}(") or full_msg.lower().startswith(f"{prefix}:"):
                clean = full_msg.split(":", 1)[-1].strip() if ":" in full_msg else full_msg
                groups[label].append(f"  · {clean}  —  {date}")
                matched = True
                break

        if not matched:
            groups["📌 أخرى"].append(f"  · {full_msg}  —  {date}")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"📋 آخر التغييرات  |  {today}", "─" * 30]

    for label, entries in groups.items():
        if entries:
            lines.append(f"\n{label}")
            lines.extend(entries)

    return "\n".join(lines)


def generate_changelog(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    owner, repo, err = _gh_repo()
    if err:
        return {"handled": True, "reply": err}

    count = min(int(args.get("count") or 20), 50)
    result = _gh("list_commits", {"owner": owner, "repo": repo, "page": 1, "perPage": count})

    raw = (result or {}).get("reply", "")
    if not raw:
        return {"handled": True, "reply": "ما قدرت أجيب الـ commits من GitHub."}

    return {"handled": True, "reply": _build_changelog(raw)}


# Schemas

MCP_TOOLS = [
    {
        "name": "memory_store",
        "description": "احفظي معلومة أو ملاحظة في الذاكرة الدائمة",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "المعلومة أو الملاحظة المراد حفظها"},
                "label": {"type": "string", "description": "تسمية تصنيفية اختيارية مثل 'تفضيلات' أو 'عمل'"},
            },
            "required": ["content"],
        },
        "handler": memory_store,
    },
    {
        "name": "memory_recall",
        "description": "ابحثي في الذاكرة عن معلومات سبق حفظها",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "ما تبحث عنه في الذاكرة"},
            },
            "required": ["query"],
        },
        "handler": memory_recall,
    },
    {
        "name": "fetch_url",
        "description": "اجلبي محتوى صفحة ويب من رابط URL",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "رابط الصفحة"},
                "max_length": {"type": "integer", "description": "الحد الأقصى للأحرف (افتراضي 5000)"},
            },
            "required": ["url"],
        },
        "handler": fetch_url,
    },
    {
        "name": "github_commits",
        "description": "اعرضي آخر commits في مستودع Sandy على GitHub",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "عدد الـ commits (افتراضي 5)"},
            },
            "required": [],
        },
        "handler": github_commits,
    },
    {
        "name": "github_issues",
        "description": "اعرضي الـ issues في مستودع Sandy على GitHub",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "open (افتراضي) | closed | all"},
            },
            "required": [],
        },
        "handler": github_issues,
    },
    {
        "name": "github_create_issue",
        "description": "أضيفي issue جديد في مستودع Sandy على GitHub",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "عنوان الـ issue"},
                "body": {"type": "string", "description": "تفاصيل اختيارية"},
            },
            "required": ["title"],
        },
        "handler": github_create_issue,
    },
    {
        "name": "github_close_issue",
        "description": (
            "أغلقي issue موجود في مستودع Sandy. استخدميها لما الأونر يقول "
            "'اقفلي issue #N', 'سكّري الـ issue N', 'بطّلي issue رقم N'، "
            "أو لو ذكر العنوان مباشرة بدون رقم: 'اقفلي issue memory R14' "
            "→ مرّري العنوان في title.\n\n"
            "🚨 مهم جداً: استدعي هاد الـ FC **دائماً** لأي طلب إغلاق — حتى لو "
            "STM فيه محادثة سابقة عن نفس الـ issue. لا تردّي شات 'مغلقة أصلاً' "
            "من غير ما تستدعي — الـ handler يفحص الحالة الفعلية في GitHub "
            "ويرد بالرسالة الصحيحة. STM ممكن يكون قديم/خاطئ."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_number": {
                    "type": "integer",
                    "description": "رقم الـ issue (مفضّل لو الأونر ذكر رقم)",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "عنوان الـ issue أو جزء منه — Sandy تبحث وتطابقه. "
                        "استخدميه فقط لو الأونر ما ذكر رقم. واحد منهم مطلوب."
                    ),
                },
            },
            "required": [],
        },
        "handler": github_close_issue,
    },
    {
        "name": "github_reopen_issue",
        "description": (
            "أعيدي فتح issue مغلق في مستودع Sandy. استخدميها لما الأونر يقول "
            "'افتحي issue #N من جديد', 'reopen رقم N'، أو ذكر العنوان مباشرة → "
            "مرّري العنوان في title.\n\n"
            "🚨 مهم جداً: استدعي هاد الـ FC **دائماً** لأي طلب إعادة فتح — حتى لو "
            "STM فيه محادثة سابقة. لا تردّي شات 'مفتوحة أصلاً' من غير ما تستدعي "
            "— الـ handler يفحص الحالة الفعلية في GitHub."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_number": {
                    "type": "integer",
                    "description": "رقم الـ issue (مفضّل لو الأونر ذكر رقم)",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "عنوان الـ issue أو جزء منه — Sandy تبحث وتطابقه. "
                        "استخدميه فقط لو الأونر ما ذكر رقم. واحد منهم مطلوب."
                    ),
                },
            },
            "required": [],
        },
        "handler": github_reopen_issue,
    },
    {
        "name": "github_comment_issue",
        "description": (
            "أضيفي تعليق على issue موجود في مستودع Sandy. استخدميها لما الأونر "
            "يقول 'علّقي على issue #N بـ ...', 'ضيفي تعليق على رقم N'، أو ذكر "
            "العنوان مباشرة → مرّري العنوان في title.\n\n"
            "🚨 مهم: استدعي هاد الـ FC **دائماً** لأي طلب تعليق — حتى لو STM "
            "فيه تعليقات سابقة. كل تعليق جديد منفصل."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_number": {
                    "type": "integer",
                    "description": "رقم الـ issue (مفضّل لو الأونر ذكر رقم)",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "عنوان الـ issue أو جزء منه — Sandy تبحث وتطابقه. "
                        "استخدميه فقط لو الأونر ما ذكر رقم. واحد منهم مطلوب."
                    ),
                },
                "body": {"type": "string", "description": "نص التعليق (مطلوب)"},
            },
            "required": ["body"],
        },
        "handler": github_comment_issue,
    },
    {
        "name": "github_file",
        "description": "اقرئي محتوى ملف من مستودع Sandy على GitHub",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "مسار الملف مثل: cloud/app/agent/agents/fc_router.py"},
            },
            "required": ["path"],
        },
        "handler": github_file,
    },
    {
        "name": "generate_changelog",
        "description": "ولّدي CHANGELOG تلقائي من آخر commits في GitHub مصنّفة حسب النوع (feat/fix/refactor...)",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "عدد الـ commits (افتراضي 20، أقصاه 50)"},
            },
            "required": [],
        },
        "handler": generate_changelog,
    },
]
