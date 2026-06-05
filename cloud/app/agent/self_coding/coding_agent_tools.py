"""M8 — Tool schemas + dispatch for the Self-Coding agent loop.

Wraps the existing repo primitives (repo_grep / repo_view / repo_patch /
repo_create / github_api.list_repo_tree) as Anthropic-format tools so the
LLM can decide what to search, read, and edit iteratively — instead of the
current one-shot JSON-generation flow.

Each tool wrapper takes the LLM-provided arguments dict, calls the primitive
with a shared `AgentContext`, and returns a short string that gets fed back
to the LLM as a `tool_result` block.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.agent.self_coding import repo_create, repo_grep, repo_patch, repo_view
from app.integrations import github_api

logger = logging.getLogger(__name__)


# Shared execution context.
_FORBIDDEN_PATHS: tuple[str, ...] = (
    ".env",
    "Procfile",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    ".github/workflows/",
)


def _is_forbidden(path: str) -> bool:
    """True if `path` matches a protected file/prefix (requires owner approval)."""
    if not path:
        return False
    for forbidden in _FORBIDDEN_PATHS:
        if forbidden.endswith("/"):
            if path.startswith(forbidden):
                return True
        elif path == forbidden:
            return True
    return False


@dataclass
class AgentContext:
    """Per-task state threaded through every tool call.

    The LLM doesn't see these — they're filled by the caller (project_builder
    or orchestrator) and used by the wrappers to scope reads/writes to the
    right branch and repo, and to record progress on the task hash.
    """

    branch: str
    base_branch: str = "main"
    repo: Optional[str] = None
    task_id: Optional[str] = None
    # Mutated as the agent works — surface back to the caller for summaries
    files_read: List[str] = field(default_factory=list)
    file_read_shas: Dict[str, str] = field(default_factory=dict)
    files_created: List[str] = field(default_factory=list)
    files_patched: List[str] = field(default_factory=list)
    searches: List[str] = field(default_factory=list)
    done_summary: Optional[str] = None
    # Suspension signal — the runner pauses the loop and notifies the owner
    pending_owner_question: Optional[str] = None
    # Paths the agent included with the current ask_owner — if the owner
    # answers positively the runner promotes these into `approved_paths`.
    pending_paths_to_approve: List[str] = field(default_factory=list)
    # Forbidden paths that the owner approved for this task only
    approved_paths: set = field(default_factory=set)


# Result formatting helpers.
_MAX_TOOL_RESULT_CHARS = 8000


def _truncate(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [اقتطعت {len(text) - limit} حرف]"


def _error(msg: str) -> str:
    return f"ERROR: {msg}"


# Tool implementations.
def _do_search_code(args: Dict[str, Any], ctx: AgentContext) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return _error("query فاضي")
    path_filter = args.get("path_filter") or None
    ctx.searches.append(query)
    result = repo_grep.repo_grep(
        query, repo=ctx.repo, path_filter=path_filter, max_results=15
    )
    if not result.get("ok"):
        return _error(result.get("error") or "search failed")
    matches = result.get("results") or []
    if not matches:
        return f"لا نتائج لـ '{query}'."
    lines = [f"وُجد {result.get('total_count', len(matches))} (عرض {len(matches)}):"]
    for m in matches:
        path = m.get("path", "")
        lns = m.get("line_numbers") or []
        excerpt = (m.get("excerpt") or "").strip()[:200]
        lines.append(f"- {path}:{','.join(map(str, lns))}\n  {excerpt}")
    return _truncate("\n".join(lines))


def _do_list_tree(args: Dict[str, Any], ctx: AgentContext) -> str:
    path_prefix = (args.get("path_prefix") or "").strip()
    result = github_api.list_repo_tree(repo=ctx.repo, ref=ctx.branch)
    if not result.get("ok"):
        return _error(result.get("error") or "tree failed")
    paths = result.get("paths") or []
    if path_prefix:
        paths = [p for p in paths if p.startswith(path_prefix)]
    skip_prefixes = (".github/", "node_modules/", "__pycache__")
    skip_suffixes = (".pyc", ".lock")
    filtered = [
        p for p in paths
        if not p.startswith(skip_prefixes) and not p.endswith(skip_suffixes)
    ]
    if not filtered:
        return f"لا ملفات تحت '{path_prefix}'."
    header = (
        f"{len(filtered)} ملف"
        + (f" تحت '{path_prefix}'" if path_prefix else "")
        + ":"
    )
    return _truncate(header + "\n" + "\n".join(filtered[:300]))


def _parse_line_range(raw: Optional[str], total: int) -> tuple[int, int]:
    """'10-50' → (10, 50). Empty/invalid → (1, min(total, 200))."""
    if not raw:
        return 1, min(total, 200)
    try:
        parts = str(raw).split("-", 1)
        start = max(1, int(parts[0]))
        end = int(parts[1]) if len(parts) > 1 else min(total, start + 199)
        return start, min(end, total)
    except (ValueError, IndexError):
        return 1, min(total, 200)


def _do_read_file(args: Dict[str, Any], ctx: AgentContext) -> str:
    path = (args.get("path") or "").strip()
    if not path:
        return _error("path فاضي")
    line_range = args.get("line_range")
    # Fetch full file once to know total, then slice
    fetched = repo_view.get_cached_or_fetch(path, ref=ctx.branch, repo=ctx.repo)
    if not fetched.get("ok"):
        return _error(fetched.get("error") or "fetch failed")
    total = len(fetched.get("lines") or [])
    start, end = _parse_line_range(line_range, total)
    view = repo_view.repo_view_lines(
        path, start, end, ref=ctx.branch, repo=ctx.repo
    )
    if not view.get("ok"):
        return _error(view.get("error") or "view failed")
    if path not in ctx.files_read:
        ctx.files_read.append(path)
    sha = fetched.get("sha") or ""
    if sha:
        ctx.file_read_shas[path] = sha
    snippet = view.get("snippet", "")
    header = (
        f"{path} (سطر {view.get('start_line')}-{view.get('end_line')} "
        f"من أصل {view.get('total_lines')}):\n"
    )
    return _truncate(header + snippet)


def _do_apply_patch(args: Dict[str, Any], ctx: AgentContext) -> str:
    path = (args.get("path") or "").strip()
    if not path:
        return _error("path فاضي")
    if _is_forbidden(path) and path not in ctx.approved_paths:
        return _error(
            f"'{path}' محمي — نادي `ask_owner` أولاً واطلب الموافقة قبل التعديل."
        )
    try:
        start_line = int(args.get("start_line"))
        end_line = int(args.get("end_line"))
    except (TypeError, ValueError):
        return _error("start_line و end_line لازم integers")
    new_lines = args.get("new_lines")
    if isinstance(new_lines, str):
        new_lines = new_lines.split("\n")
    if not isinstance(new_lines, list):
        return _error("new_lines لازم list of strings")
    result = repo_patch.repo_apply_patch(
        path,
        start_line,
        end_line,
        new_lines,
        branch=ctx.branch,
        message=args.get("message"),
        repo=ctx.repo,
        task_id=ctx.task_id,
        expected_sha=ctx.file_read_shas.get(path, ""),
    )
    if not result.get("ok"):
        return _error(result.get("error") or "patch failed")
    if path not in ctx.files_patched:
        ctx.files_patched.append(path)
    return (
        f"✅ patched {path}: {result.get('lines_before')} → "
        f"{result.get('lines_after')} سطر, sha={result.get('commit_sha', '')[:7]}"
    )


def _do_write_new_file(args: Dict[str, Any], ctx: AgentContext) -> str:
    path = (args.get("path") or "").strip()
    if not path:
        return _error("path فاضي")
    if _is_forbidden(path) and path not in ctx.approved_paths:
        return _error(
            f"'{path}' محمي — نادي `ask_owner` أولاً واطلب الموافقة قبل الكتابة."
        )
    content = args.get("content")
    if not isinstance(content, str):
        return _error("content لازم string")
    result = repo_create.repo_create_or_replace(
        path,
        content,
        branch=ctx.branch,
        message=args.get("message"),
        repo=ctx.repo,
        task_id=ctx.task_id,
        replace_if_exists=bool(args.get("replace_if_exists", False)),
        expected_sha=ctx.file_read_shas.get(path, ""),
    )
    if not result.get("ok"):
        return _error(result.get("error") or "write failed")
    if path not in ctx.files_created:
        ctx.files_created.append(path)
    verb = "created" if result.get("created") else (
        "replaced" if result.get("replaced") else "no-op"
    )
    return f"✅ {verb} {path}, sha={result.get('commit_sha', '')[:7]}"


def _do_ask_owner(args: Dict[str, Any], ctx: AgentContext) -> str:
    question = (args.get("question") or "").strip()
    if not question:
        return _error("question فاضي")
    ctx.pending_owner_question = question
    # Optional: agent can declare which protected paths it's asking permission
    # for. A positive owner reply will promote them into `approved_paths` so
    # the next write_new_file/apply_patch doesn't hit "نادي ask_owner" again
    # and loop forever.
    raw_paths = args.get("paths_to_approve") or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if isinstance(raw_paths, list):
        ctx.pending_paths_to_approve = [
            str(p).strip() for p in raw_paths if str(p).strip()
        ]
    # The runner detects ctx.pending_owner_question after this returns,
    # suspends the loop, notifies via Telegram, and resumes with the answer.
    return "SUSPENDED: انتظار رد المالك على Telegram."


def _do_get_branch_diff(args: Dict[str, Any], ctx: AgentContext) -> str:
    result = github_api.compare_branches(
        ctx.base_branch, ctx.branch, repo=ctx.repo
    )
    if not result.get("ok"):
        return _error(result.get("error") or "compare failed")
    files = result.get("files") or []
    if not files:
        return "لا تعديلات على الـ branch لحد الآن."
    lines = [
        f"تعديلات على branch '{ctx.branch}' ({result.get('total_changes')} change):"
    ]
    for f in files:
        lines.append(
            f"- {f['path']}: {f['status']} (+{f['additions']} -{f['deletions']})"
        )
    return _truncate("\n".join(lines))


def _do_done(args: Dict[str, Any], ctx: AgentContext) -> str:
    summary = (args.get("summary") or "").strip() or "خلصت بدون تفاصيل."
    ctx.done_summary = summary
    return f"DONE: {summary}"


# Anthropic tool schemas.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "search_code",
        "description": (
            "ابحث في كود الـ repo عن نص (function name, class, keyword, etc.). "
            "استخدمها لإيجاد patterns موجودة قبل ما تكتب كود جديد."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "النص اللي بدك تبحث عنه (≥ 2 أحرف)",
                },
                "path_filter": {
                    "type": "string",
                    "description": "اختياري — `path:cloud/app/` لتقييد البحث",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_tree",
        "description": (
            "اعرض ملفات الـ repo (recursive). استخدمها لفهم البنية أو لتأكد من "
            "وجود مجلد قبل ما تكتب فيه."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path_prefix": {
                    "type": "string",
                    "description": (
                        "اختياري — pre-filter (مثلاً 'cloud/app/agent/tools/')"
                    ),
                },
            },
        },
    },
    {
        "name": "read_file",
        "description": (
            "اقرأ ملف من الـ repo. line_range اختياري (مثلاً '10-50'). "
            "بدونه بترجع أول 200 سطر."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line_range": {
                    "type": "string",
                    "description": "صيغة 'start-end' (1-indexed، inclusive)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "apply_patch",
        "description": (
            "استبدل أسطر [start_line..end_line] (1-indexed، inclusive) بـ "
            "new_lines. استخدمها للتعديل على ملف موجود — أسرع وأرخص من إعادة "
            "كتابته كاملاً. اقرأ الملف أولاً للحصول على أرقام أسطر دقيقة."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "description": "1-indexed inclusive"},
                "end_line": {"type": "integer", "description": "1-indexed inclusive"},
                "new_lines": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "كل عنصر سطر كامل بدون \\n",
                },
                "message": {
                    "type": "string",
                    "description": "اختياري — commit message",
                },
            },
            "required": ["path", "start_line", "end_line", "new_lines"],
        },
    },
    {
        "name": "write_new_file",
        "description": (
            "اكتب ملف جديد. لو الملف موجود ورح replace_if_exists=False بترجع "
            "خطأ — استخدم apply_patch بدلاً منها للتعديل."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "replace_if_exists": {
                    "type": "boolean",
                    "description": "افتراضي false — يفشل لو الملف موجود",
                },
                "message": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "ask_owner",
        "description": (
            "اسأل المالك سؤال على Telegram إذا كنت بحاجة لتوضيح، موافقة على "
            "إضافة dependency، أو إذن للتعديل على ملف محمي (.env, Procfile, "
            "requirements.txt, package.json, .github/workflows/). الـ loop "
            "بيوقف وبستنى رد منه قبل ما يكمل. لما تطلب إذن لملف محمي مرر "
            "مساره بـ `paths_to_approve` — لو الأونر رد إيجابي بنحرّر الملف "
            "تلقائياً، فما تضطري تسأل عنه مرة ثانية."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "السؤال للمالك بالعربي. وضّح ليش بدك الجواب وايش الخيارات."
                    ),
                },
                "paths_to_approve": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "اختياري — قائمة المسارات المحمية اللي بدك تطلب إذن لها "
                        "(مثلاً ['package.json']). الموافقة بتنطبق على باقي "
                        "الـ task، فما تحتاج تسأل عن نفس الملف من جديد."
                    ),
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_branch_diff",
        "description": (
            "اعرض ملخص التعديلات اللي عملتها على الـ branch لحد الآن "
            "(filename + additions + deletions). استخدمها قبل `done` للـ "
            "self-review."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "done",
        "description": (
            "ادي هاي اللي بتنادي عليها لما تخلص الميزة كاملة. summary بـ سطر "
            "أو سطرين عربي يلخص شو عملت."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
        },
    },
]


_DISPATCH: Dict[str, Callable[[Dict[str, Any], AgentContext], str]] = {
    "search_code": _do_search_code,
    "list_tree": _do_list_tree,
    "read_file": _do_read_file,
    "apply_patch": _do_apply_patch,
    "write_new_file": _do_write_new_file,
    "ask_owner": _do_ask_owner,
    "get_branch_diff": _do_get_branch_diff,
    "done": _do_done,
}


def dispatch_tool_call(
    name: str, arguments: Dict[str, Any], ctx: AgentContext
) -> str:
    """Run a tool by name and return its string result for the LLM."""
    handler = _DISPATCH.get(name)
    if handler is None:
        return _error(f"أداة غير معروفة: {name}")
    try:
        return handler(arguments or {}, ctx)
    except Exception as exc:
        logger.exception("[coding_agent_tools] %s فشلت", name)
        return _error(f"exception in {name}: {exc}")


def short_args_log(name: str, arguments: Dict[str, Any]) -> str:
    """Human-readable one-liner of a tool call for Telegram/logs."""
    if name == "search_code":
        return f"🔍 search_code('{arguments.get('query', '')}')"
    if name == "list_tree":
        prefix = arguments.get("path_prefix") or ""
        return f"📂 list_tree({prefix!r})" if prefix else "📂 list_tree()"
    if name == "read_file":
        rng = arguments.get("line_range") or "1-200"
        return f"📖 read_file('{arguments.get('path', '')}', {rng})"
    if name == "apply_patch":
        return (
            f"✏️  apply_patch('{arguments.get('path', '')}', "
            f"{arguments.get('start_line')}-{arguments.get('end_line')})"
        )
    if name == "write_new_file":
        return f"📝 write_new_file('{arguments.get('path', '')}')"
    if name == "ask_owner":
        q = (arguments.get("question") or "")[:80]
        return f"❓ ask_owner('{q}')"
    if name == "get_branch_diff":
        return "📊 get_branch_diff()"
    if name == "done":
        return f"✅ done({arguments.get('summary', '')[:80]})"
    return f"{name}({json.dumps(arguments, ensure_ascii=False)[:80]})"
