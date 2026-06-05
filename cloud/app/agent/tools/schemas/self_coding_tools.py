"""FC tools that expose SA1-SA5 to the owner via Sandy chat.

All tool names start with `github_` so they inherit the owner-only guard from
`execute._OWNER_ONLY_PREFIXES`. Guests cannot trigger any of these.

Manual flow:
    Owner: "ساندي ابحثي بالـ repo عن KeyError"
        → github_repo_grep
    Owner: "اقرأي من 30 لـ 60 في cloud/app/foo.py"
        → github_repo_view
    Owner: "افتحي task لتصليح هاد"
        → github_enqueue_task (manual entry — bypass webhook)

The orchestrator + Worker dyno run independently and produce a PR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext


def github_repo_grep_handler(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.self_coding import repo_grep
    result = repo_grep.repo_grep(
        query=str(args.get("query") or "").strip(),
        max_results=int(args.get("max_results") or 20),
        path_filter=(args.get("path_filter") or None),
        language=(args.get("language") or None),
    )
    if not result.get("ok"):
        return {
            "handled": True,
            "reply": f"البحث فشل: {result.get('error')}",
            "results": [],
        }
    results = result.get("results") or []
    lines = []
    for r in results[:10]:
        lns = ",".join(str(n) for n in (r.get("line_numbers") or [])[:3])
        lines.append(f"• `{r['path']}`" + (f":{lns}" if lns else ""))
    summary = (
        f"لقيت {result.get('total_count', 0)} نتيجة"
        + (" (مقصوصة)" if result.get("truncated") else "")
        + "\n"
        + ("\n".join(lines) if lines else "ولا نتيجة معروضة")
    )
    return {"handled": True, "reply": summary, "results": results}


def github_repo_view_handler(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.self_coding import repo_view
    file_path = str(args.get("file") or args.get("path") or "").strip()
    start = int(args.get("start_line") or 1)
    end = int(args.get("end_line") or start + 30)
    result = repo_view.repo_view_lines(
        file_path,
        start,
        end,
        ref=args.get("ref"),
    )
    if not result.get("ok"):
        return {
            "handled": True,
            "reply": f"ما قدرت أقرأ {file_path}: {result.get('error')}",
        }
    snippet = result.get("snippet", "")
    msg = (
        f"`{file_path}` (L{result['start_line']}-L{result['end_line']} "
        f"من أصل {result['total_lines']}):\n```\n{snippet[:3500]}\n```"
    )
    return {"handled": True, "reply": msg, "result": result}


def github_repo_status_handler(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """List queued + processing tasks with id + description + status."""
    import json as _json
    from app.agent.self_coding import _redis as sa_redis, task_state

    if not sa_redis.is_available():
        return {"handled": True, "reply": "Redis غير متاح — ما عندي task state"}

    client = sa_redis.get_client()
    try:
        queued_raw = client.lrange(sa_redis.k_task_queue(), 0, -1) or []
        processing_raw = client.lrange(sa_redis.k_task_processing(), 0, -1) or []
    except Exception as exc:
        return {"handled": True, "reply": f"ما قدرت أقرأ الـ queue: {exc}"}

    qlen, plen = len(queued_raw), len(processing_raw)
    if qlen == 0 and plen == 0:
        return {
            "handled": True,
            "reply": "📊 Self-Coding فاضية — لا queue ولا processing.",
            "queue_size": 0,
            "processing_size": 0,
        }

    lines = [f"📊 Self-Coding state:\n• queue: {qlen}\n• processing: {plen}"]

    def _describe(raw_payload: str, *, processing: bool) -> str:
        try:
            payload = _json.loads(raw_payload)
        except Exception:
            return "• (corrupt payload)"
        task_id = payload.get("task_id") or "?"
        task = task_state.get_task(task_id) or {}
        desc = (task.get("description") or "").strip().replace("\n", " ")[:90]
        if processing:
            status = task.get("status") or "?"
            stage = task.get("stage") or ""
            stage_part = f" ({stage})" if stage else ""
            attempts = task.get("attempts") or 0
            return f"• `{task_id}` [{status}{stage_part}, attempt {attempts}] — {desc or '(no description)'}"
        return f"• `{task_id}` — {desc or '(no description)'}"

    if processing_raw:
        lines.append("\n🔄 شغّالة هلق:")
        lines.extend(_describe(raw, processing=True) for raw in processing_raw[:5])

    if queued_raw:
        lines.append("\n📥 في الـ queue:")
        lines.extend(_describe(raw, processing=False) for raw in queued_raw[:10])
        if qlen > 10:
            lines.append(f"... و{qlen - 10} غيرها")

    return {
        "handled": True,
        "reply": "\n".join(lines),
        "queue_size": qlen,
        "processing_size": plen,
    }


def github_build_project_handler(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """Owner-initiated task: build (or extend) a standalone GitHub repo.

    Project Builder either creates a fresh external repo or — when the
    `repo_name` already exists under the authenticated user — reuses it
    and plans patches/additions instead of a fresh scaffold (M9).
    Missing fields come back as a clear error so the owner can re-issue
    the request — no in-place negotiation flow.
    """
    from app.agent.self_coding import task_state

    description = str(args.get("description") or "").strip()
    repo_name = str(args.get("repo_name") or "").strip()
    autonomous = bool(args.get("autonomous"))

    # Auto-normalize / generate a repo_name from the description if missing.
    repo_name = _normalize_or_generate_repo_name(repo_name, description)

    missing: List[str] = []
    if not description:
        missing.append("description")
    if not repo_name:
        missing.append("repo_name")
    elif not _is_valid_github_repo_name(repo_name):
        missing.append("repo_name (صيغة غير صالحة — حروف/أرقام/`._-` فقط)")
    if missing:
        return {
            "handled": True,
            "reply": "الحقول الناقصة: " + ", ".join(missing),
        }

    payload = task_state.build_task_payload(
        task_type=task_state.TYPE_PROJECT_BUILDER,
        chat_id=str(ctx.session.get("chat_id") or "") if hasattr(ctx, "session") and isinstance(ctx.session, dict) else "",
        description=description,
        extra={
            "repo_name": repo_name,
            "autonomous": "1" if autonomous else "",
        },
    )
    result = task_state.enqueue(payload)
    if not result.get("ok"):
        return {"handled": True, "reply": f"ما قدرت أضيف task: {result.get('error')}"}

    # Surface repo + mode on their own lines so the owner can spot a wrong
    # repo name or an unexpected autonomous flag and cancel early.
    if autonomous:
        mode_line = "• الوضع: autonomous — ساندي تكمل كل الـ features بدون توقّف بينها"
    else:
        mode_line = "• الوضع: تفاعلي — ساندي تستأذنك بعد كل feature"
    reply = (
        f"🏗️ تمام، أضفت task بناء `{result['task_id']}`.\n\n"
        f"• Repo: `{repo_name}` (رح أتأكد لو موجود وأكمل عليه، أو أنشئه جديد)\n"
        f"{mode_line}\n\n"
        "التالي: PLAN → موافقتك → بناء → PR. "
        "ردّ `الغي` لو الـ task مش زي ما تتوقّع."
    )
    return {"handled": True, "reply": reply, "task_id": result["task_id"]}


def _is_valid_github_repo_name(name: str) -> bool:
    """GitHub allows alphanumeric + `.`, `_`, `-`. 1-100 chars."""
    import re
    if not 1 <= len(name) <= 100:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", name))


def _generate_repo_name_from_text(text: str) -> str:
    """Try to create a simple GitHub-friendly repo name from free text.

    - Normalize unicode to ascii where possible.
    - Lowercase, replace runs of non-allowed chars with `-`.
    - Trim to 100 chars and strip separator edges.
    Returns empty string if nothing valid can be produced.
    """
    import re
    import unicodedata

    if not text:
        return ""
    s = unicodedata.normalize("NFKD", text)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = re.sub(r"[-_.]{2,}", "-", s)
    s = s.strip("-._")
    if len(s) > 100:
        s = s[:100].rstrip("-._")
    return s if _is_valid_github_repo_name(s) else ""


def _normalize_or_generate_repo_name(candidate: str, description: str) -> str:
    """Return a valid repo name using candidate if valid, otherwise try to
    sanitize it, then fall back to generating from description.
    """
    cand = (candidate or "").strip()
    if cand and _is_valid_github_repo_name(cand):
        return cand
    if cand:
        sanitized = _generate_repo_name_from_text(cand)
        if sanitized:
            return sanitized
    # fallback to description-derived name
    return _generate_repo_name_from_text(description or "")


# Tool registry entries
SELF_CODING_TOOLS = [
    {
        "name": "github_repo_grep",
        "description": (
            "ابحث في كود الـ default branch للمستودع. "
            "يرجع قائمة من المسارات + أرقام الأسطر المحتملة (بدون كامل المحتوى). "
            "يستخدم GitHub Code Search API."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "نص البحث (≥2 حرف)"},
                "max_results": {"type": "integer", "description": "أقصى نتائج (افتراضي 20)"},
                "path_filter": {"type": "string", "description": "مثلاً src/ أو cloud/app/"},
                "language": {"type": "string", "description": "مثلاً python"},
            },
            "required": ["query"],
        },
        "handler": github_repo_grep_handler,
    },
    {
        "name": "github_repo_view",
        "description": (
            "اقرأ سطور محددة من ملف في المستودع (1-indexed، شامل). "
            "احفظ الملف في cache لمدة 30 دقيقة. الحد الأقصى 200KB للملف."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "المسار داخل المستودع"},
                "start_line": {"type": "integer", "description": "1-indexed inclusive"},
                "end_line": {"type": "integer", "description": "1-indexed inclusive"},
                "ref": {"type": "string", "description": "branch/tag/SHA (اختياري)"},
            },
            "required": ["file", "start_line", "end_line"],
        },
        "handler": github_repo_view_handler,
    },
    {
        "name": "github_status",
        "description": (
            "عرض حالة Self-Coding queue + processing مع تفاصيل كل task "
            "(id + الوصف + الـ status + الـ stage). "
            "استخدمه لما الأونر يسأل أسئلة زي: 'شو في الـ queue', 'كم task فيها', "
            "'وريني التاسكات', 'في اشي شغّال', 'حالة ساندي الكوديّة'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": github_repo_status_handler,
    },
    {
        "name": "github_build_project",
        "description": (
            "🏗️ بناء أو تعديل repo على GitHub — Project Builder ينشئ repo "
            "جديد أو يكمل على واحد موجود، يولّد/يعدّل الملفات، ويفتح PR.\n\n"
            "حالتين مدعومتين:\n"
            "1) **مشروع جديد:** repo_name مش موجود → ساندي تنشئه من الصفر.\n"
            "2) **تعديل على موجود:** repo_name موجود → ساندي تكمل عليه "
            "(patches/إضافات، ما بتعمل scaffold ثاني). اذكر اسم الـ repo "
            "صراحة في الوصف لو الأونر طلب تعديل على repo معين.\n\n"
            "أمثلة جديد: 'ابني bot ترجمة اسمه translation-bot'، 'اعملي repo "
            "جديد لموقع منيو مطعم'، 'ابني CLI صغير بـ Python'، 'ابني موقع "
            "ألعاب HTML/CSS/JS'.\n"
            "أمثلة تعديل: 'عدّلي على sandy-games — اربطي اللعبة الفلانية'، "
            "'في الـ repo X صلحي البگ Y'، 'ضيفي ميزة Z على repo W'.\n\n"
            "❌ ممنوع للـ to-do tasks الشخصية اليومية (ذكّر/جدّل/...) — هاي "
            "tools أخرى."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "وصف المشروع المطلوب بناؤه — كل تفصيلة تساعد ساندي "
                        "تكتب PLAN دقيق (الهدف، الـ stack، المكوّنات، tests؟، إلخ). "
                        "ساندي دائماً تنشئ repo منفصل جديد — مفيش تعديل على كود قائم."
                    ),
                },
                "repo_name": {
                    "type": "string",
                    "description": (
                        "اسم الـ repo الجديد (مثل 'translation-bot' أو 'cafe-menu'). "
                        "إذا تركته فاضي بنولّد واحد من الوصف. "
                        "❌ مش مسار ملف ولا امتداد — اسم بسيط فقط. "
                        "مسموح: حروف/أرقام/. _ - فقط، حتى 100 حرف."
                    ),
                },
                "autonomous": {
                    "type": "boolean",
                    "description": (
                        "true = ساندي تكمل كل الـ features بدون توقف بينهم — استخدمها فقط "
                        "إذا الأونر صراحة قال 'كملي بدون ما ترجعيلي', 'autonomous', "
                        "'بدون توقف', 'لحالك'. PLAN لازم يبقى موافق عليه قبل البدء حتى "
                        "في autonomous. default=false (الأونر يستأذن بعد كل feature)."
                    ),
                },
            },
            "required": ["description"],
        },
        "handler": github_build_project_handler,
    },
]
