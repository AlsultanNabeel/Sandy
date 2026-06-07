"""SA8 Project Builder orchestrator.

Handles the "build something new from scratch" flow. Same Worker, same Redis queue,
same Telegram channel — only the orchestrator entry point differs.

Phases:
  A — Plan  (this module, Step 3): read repo guide → Sonnet → PLAN.md → owner
       review → STAGE_PROJECT_PLAN_REVIEW checkpoint → wait_for_resume.
  B — Build (Step 4): iterate over plan.groups, generate files per group, gate
       between groups in non-autonomous mode.
  C — Final (Step 5): wait_for_ci → open PR → done.

Checkpoint design mirrors the shared task-state pattern: every blocking wait is preceded by a write to
the task hash so a Worker SIGTERM mid-wait can re-enqueue and resume cleanly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.agent.self_coding import (
    _redis as sa_redis,
    branch_ops,
    ci_status,
    coding_agent,
    coding_agent_tools,
    notifier,
    self_review,
    shutdown as sa_shutdown,
    task_state,
)
from app.integrations import code_llm, github_api

logger = logging.getLogger(__name__)


# Context reader.
_EXISTING_CONTEXT_MAX_CHARS = 4000


def _read_project_context(repo: Optional[str], *, repo_mode: str = "new") -> str:
    """Build the project context block fed to the plan generator.

    - `new` (default): generic "fresh repo, no prior context" message.
    - `existing`: fetch the README (if any) and the root-level tree so the
      planner sees what already lives in the repo and can plan patches
      instead of a fresh scaffold.

    On any GitHub error we fall back to the generic context — the build
    can still proceed; the agent loop has its own tools to discover the
    repo at execution time.
    """
    if repo_mode != "existing" or not repo:
        return (
            "(مشروع جديد منفصل — لا يوجد سياق سابق. استخدم best practices "
            "قياسية للـ stack المطلوب. لا تحقن أي conventions غير ضرورية. "
            "اطلب توضيح في open_questions لأي تفصيلة غامضة.)"
        )

    parts: List[str] = ["## الـ repo موجود — لقطة من محتواه الحالي"]

    # README — best effort, no failure path
    try:
        readme = github_api.get_file_contents("README.md", repo=repo)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[SA8] README fetch failed for %s: %s", repo, exc)
        readme = {"ok": False}
    if readme.get("ok"):
        text = (readme.get("content") or "").strip()
        if text:
            cap = _EXISTING_CONTEXT_MAX_CHARS // 2
            parts.append(
                "### README.md (مقتطف)\n```\n"
                + text[:cap]
                + ("\n... (مقتطع)" if len(text) > cap else "")
                + "\n```"
            )

    # Root tree — top-level entries only
    try:
        tree = github_api.list_repo_tree(repo=repo)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[SA8] tree fetch failed for %s: %s", repo, exc)
        tree = {"ok": False}
    if tree.get("ok"):
        all_paths = tree.get("paths") or []
        root_entries = sorted(
            {p.split("/", 1)[0] for p in all_paths if isinstance(p, str) and p}
        )
        if root_entries:
            parts.append(
                "### مدخل الـ repo (مستوى أول)\n"
                + "\n".join(f"- {p}" for p in root_entries[:40])
            )

    if len(parts) == 1:
        # Couldn't fetch anything useful — fall back to generic
        return (
            "(الـ repo موجود بس ما قدرت أجيب محتواه الحالي. اعتمد على أدوات "
            "الـ agent (list_tree/read_file) في وقت التنفيذ لاستكشاف "
            "البنية قبل أي تعديل.)"
        )

    parts.append(
        "\nاستخدم هاي اللقطة لفهم البنية. الـ agent عنده list_tree/read_file "
        "للحفر أعمق وقت التنفيذ — اللقطة هون مجرد توجيه للخطة."
    )
    return "\n\n".join(parts)


# Plan generator.
# Project Builder always scaffolds a fresh, standalone repo. There is no
# "internal" path — Sandy modifying herself was an SA7 capability removed in
# 14d8642. The plan instructions assume a clean external project from the
# start so the LLM doesn't try to graft Sandy-isms into unrelated work.
_PLAN_SYSTEM = (
    "أنت معماري برمجيات يعمل كـ assistant لتقسيم طلبات الأونر إلى PLANs قابلة للتنفيذ.",
    "\n\nالـ Project Builder دائماً ينشئ مستودعاً منفصلاً جديداً من الصفر — مفيش أي تعديل على كود قائم. لا تخمن — أي غموض ضعه في 'open_questions'.",
    "\n\nقواعد عامة:\n",
    "- اقسم الطلب لميزات صغيرة مستقلة؛ كل feature يجب أن تكون قابلة للبناء عبر agent loop باستخدام الأدوات (search/read/edit/write).\n",
    "- لا تحدد مسارات ملفات دقيقة في الـ PLAN؛ اشرح الهدف والسلوك والحدود والاختبارات المتوقعة، ودع الـ agent يختار المسارات المناسبة من بنية الريبو الجديد.\n",
    "- 'intent' دائماً 'create_only' (الريبو جديد فاضي — ما في كود قائم لتعديله). 'modify_existing' و'mixed' محجوزين لاحتمالات مستقبلية بس مش مستخدمين هلق.\n",
    "- 'wiring_required': true عندما تحتاج الميزة لربط بـ registry/tools أو إضافة واجهات خارجية (اذكر أي متطلبات واجهات).\n",
    "- كن واقعياً في 'estimated_loc' و'estimated_files' (قيم مرجعية مذكورة في schema).\n",
    "- إذا الوصف ذكر 'بدون tests' لا تضف tests تلقائيًا؛ خلاف ذلك اقترح نقاط اختبار أساسية.\n",
    "- ارجع JSON فقط مطابق للـ schema المحدد؛ لا تدمج نصوص حرة خارج الحقول المصرح بها.\n\n",
    "اختيار الـ stack (مهم — لا تتجاوز):\n",
    "- الافتراضي لمواقع/أدوات متصفّحية: vanilla HTML + CSS + JavaScript فقط، بدون build tools.\n",
    "- إشارات على الافتراضي: كلمات مثل 'بسيط'، 'صغير'، 'تجريبي'، 'mini'، 'demo'، 'static'، أو scope متوقع ≤ 10 ملفات، أو الأونر ذكر 'html/css/js' حرفياً.\n",
    "- React / Vue / Svelte / Angular / TypeScript / Vite / webpack / Next.js: استخدمهم فقط إذا الأونر ذكر اسم واحد منهم صراحة، أو الـ scope ≥ 15 ملف فعلاً مع SPA routing أو state management معقّد. لا تخترع الحاجة لهم.\n",
    "- خلاف ذلك (CLI / API / data tool): اختر أبسط stack يلبي الطلب — Python بـ stdlib قبل أي framework، Node بدون TypeScript قبل ما تضيفه.\n",
    "- لو شككت بين stack بسيط وstack أعقد: اختر البسيط واذكر الأعقد في 'open_questions'.\n\n",
    "ملفات لازمة لأي مستودع جديد:\n",
    "- يجب أن يتضمن الـ PLAN قائمة ملفات لتوليد: README.md (شرح سريع، تعليمات تشغيل، إعدادات dev), LICENSE (اقترح MIT أو اسأل الأونر), .gitignore ملائم للـ stack، وملفات package manager اللازمة (requirements.txt, package.json, pyproject.toml إلخ).\n",
    "- اقترح بنية مجلدات مبدئية، اسم الحزمة/module الرئيسي، وأمثلة لأوامر تشغيل محلية ولقطة أولية لملف الاختبارات إذا طُلبت.\n",
    "- اذكر صراحة أي اعتماديات أساسية مع نطاق إصدارات مقترح (مثلاً Python '>=3.11,<4.0' أو Node '>=18'). CI/Dockerfile اختياريين — لا تضفهم بدون طلب صريح.\n",
    "- لا تدخل أي اعتمادات داخلية لساندي (مثل Telegram hooks أو maestro FC wiring) — كل مشروع منفصل تماماً.\n\n",
    "أمثلة description (واضح ومفصل، 80 حرف على الأقل):\n",
    "❌ سيء: 'implement drawing' / 'make it work' / 'add feature'\n",
    "✅ جيد: 'ابني microservice صغير بلغة Python يعرض API لتحويل نص إلى ASCII art، يتضمن Dockerfile وGitHub Actions لتشغيل الاختبارات، ويحتوي README يشرح تشغيله محلياً.'"
)

_PLAN_SCHEMA = (
    "{"
    '"title": "<عنوان قصير بالعربي>", '
    '"summary": "<جملتين-ثلاث وصف عام بالعربي>", '
    '"stack": ["<لغة/framework>", "..."], '
    '"features": ['
    '{'
    '"name": "<اسم الفيتشر بالعربي>", '
    '"description": "<شرح مفصّل بالعربي للـ agent — ايش الفيتشر تعمله، '
    'وين منطقياً تتموضع، أي patterns موجودة لازم تتبعها>", '
    '"intent": "create_only|modify_existing|mixed", '
    '"wiring_required": <bool>, '
    '"estimated_files": <int>, '
    '"estimated_loc": <int>'
    "}"
    "], "
    '"tests_question": "<سؤال قصير للأونر عن الـ tests، أو فارغ>", '
    '"estimated_files": <int>, '
    '"estimated_loc": <int>, '
    '"open_questions": ["<سؤال 1>", "..."]'
    "}"
)

_MAX_FILES = 30
_MAX_FEATURES = 8

# Budget warning thresholds. Anything above these adds a warning to the plan
# notification so the owner sees the scope before approving.
_WARN_FILES = 15
_WARN_LOC = 1500
_WARN_GROUPS = 5
# A "large" project (double the warn levels) gets a stronger warning.
_LARGE_FILES = 25
_LARGE_LOC = 3000


def _is_autonomous(task: Dict[str, Any]) -> bool:
    """Owner asked Sandy to run end-to-end without per-group gates.

    Stored as a string in Redis hash — empty / '0' / 'false' / 'no' are all
    treated as off.
    """
    raw = task.get("autonomous")
    if raw is None or raw is False:
        return False
    if raw is True:
        return True
    s = str(raw).strip().lower()
    return s not in ("", "0", "false", "no", "off")


def _estimate_budget(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Compute size signals + a warning level the owner will see in the PLAN.

    Returns:
        {n_files, n_loc, n_features, level: 'ok'|'warn'|'large', label, notes}
    """
    features = plan.get("features") or []
    n_features = len(features)
    n_files = 0
    n_loc = 0
    for ft in features:
        try:
            n_files += int(ft.get("estimated_files") or 0)
            n_loc += int(ft.get("estimated_loc") or 0)
        except (TypeError, ValueError):
            pass

    # Cross-check with plan-level estimates if higher (defend against
    # under-counting at the feature level).
    n_files = max(n_files, int(plan.get("estimated_files") or 0))
    n_loc = max(n_loc, int(plan.get("estimated_loc") or 0))

    if n_files >= _LARGE_FILES or n_loc >= _LARGE_LOC:
        level = "large"
        label = "⚠️⚠️ مشروع ضخم"
    elif n_files >= _WARN_FILES or n_loc >= _WARN_LOC or n_features >= _WARN_GROUPS:
        level = "warn"
        label = "⚠️ مشروع متوسط-كبير"
    else:
        level = "ok"
        label = ""

    notes = []
    if n_files >= _WARN_FILES:
        notes.append(f"{n_files} ملف (الحد المريح ≤{_WARN_FILES})")
    if n_loc >= _WARN_LOC:
        notes.append(f"~{n_loc} سطر (الحد المريح ≤{_WARN_LOC})")
    if n_features >= _WARN_GROUPS:
        notes.append(f"{n_features} features (الحد المريح ≤{_WARN_GROUPS-1})")

    return {
        "n_files": n_files,
        "n_loc": n_loc,
        "n_features": n_features,
        "level": level,
        "label": label,
        "notes": notes,
    }


# M13: feature-concreteness validator
# Arabic intent verbs that signal a feature description is too vague to act on
# without further clarification. They describe an *outcome* ("make it match")
# instead of a *step* ("add X to Y, remove Z from W").
_VAGUE_VERBS = (
    "مواءمة", "موائمة", "واءمي", "وائمي",
    "تحسين", "حسّني", "حسني",
    "تنسيق", "نسّقي", "نسقي",
    "ضبط", "اضبطي",
    "تجميل", "جمّلي", "جملي",
    "تطوير", "طوّري", "طوري",
    "تعزيز", "عزّزي", "عززي",
)
# Concrete verbs / explicit file markers — at least one of these must appear
# in a feature description for it to count as actionable.
_CONCRETE_MARKERS = (
    "أضيفي", "اضيفي", "احذفي", "استبدلي", "أنشئي", "انشئي",
    "اربطي", "اربط", "انقلي", "غيّري", "غيري",
    "اكتبي", "اكتب", "عدّلي على", "بدّلي", "بدلي",
    "add ", "remove ", "replace ", "create ", "wire ",
)
# Substrings that imply a concrete file/path. If the description mentions a
# real-looking file, we trust the planner picked something specific.
_FILE_HINTS = (
    ".html", ".css", ".js", ".py", ".md", ".json", ".ts", ".tsx",
    ".yml", ".yaml", ".toml", "/", "index.", "main.", "app.",
)


def _is_feature_concrete(feature: Dict[str, Any]) -> Optional[str]:
    """Return None if the feature looks actionable, else a short reason
    string explaining what's vague about it."""
    desc = str(feature.get("description") or "").strip()
    if len(desc) < 80:
        return f"description قصير جداً ({len(desc)} حرف، الحد الأدنى 80)"

    lowered = desc.lower()
    has_concrete = any(marker in desc or marker in lowered for marker in _CONCRETE_MARKERS)
    has_file_hint = any(hint in lowered for hint in _FILE_HINTS)
    has_vague = any(verb in desc for verb in _VAGUE_VERBS)

    if has_vague and not (has_concrete or has_file_hint):
        return "وصف غامض (verb نية بدون أفعال محددة أو مسارات ملفات)"
    if not (has_concrete or has_file_hint):
        return "ما في فعل ملموس (add/remove/replace) أو إشارة لملف"
    return None


def _audit_plan_concreteness(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    """Run _is_feature_concrete across every feature. Returns a list of
    {name, reason} for the vague ones; empty list means the plan is OK."""
    issues: List[Dict[str, str]] = []
    for feat in plan.get("features") or []:
        if not isinstance(feat, dict):
            continue
        reason = _is_feature_concrete(feat)
        if reason:
            issues.append({
                "name": str(feat.get("name") or "?"),
                "reason": reason,
            })
    return issues


def _generate_plan(
    description: str,
    project_context: str,
    *,
    revision_text: str = "",
    previous_plan: Optional[Dict[str, Any]] = None,
    repo_mode: str = "new",
) -> Dict[str, Any]:
    """Call Sonnet to produce a structured PLAN. Returns {ok, plan, error?}.

    `revision_text` is owner free-text overriding the previous plan. When
    present it's added as a constraints block that takes precedence over
    any default heuristic in `_PLAN_SYSTEM`.

    `previous_plan` is the plan the owner is revising. Passing it lets the
    model edit the existing plan incrementally rather than re-planning
    from scratch — so changes accepted in earlier revisions don't get
    silently dropped when the next revision arrives.

    `repo_mode` is "new" (fresh scaffold expected) or "existing" (target
    repo already has code; plan should be patches/additions, not scaffold).
    """
    if not code_llm.is_available():
        return {"ok": False, "error": "Claude Vertex غير متاح — مش قادر أبني الخطة"}

    parts = [
        f"## وصف الطلب من الأونر\n{description[:2000]}",
        project_context,
    ]
    if repo_mode == "existing":
        parts.append(
            "## ⚠️ وضع الـ repo: موجود (existing)\n"
            "الـ repo فيه كود فعلاً. الخطة لازم تكون patches/إضافات صغيرة "
            "على الموجود — لا تعمل scaffold كامل. لا تعيد توليد README/"
            "LICENSE/.gitignore أو package manifests إلا لو الأونر طلب "
            "صراحة. كل feature في 'intent' لازم تكون `modify_existing` أو "
            "`mixed` (مش `create_only`) إلا لو فعلاً ملف جديد بحت."
        )
    if previous_plan and revision_text:
        try:
            prev_json = json.dumps(previous_plan, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            prev_json = "{}"
        # Cap to ~4K chars; the schema is small so this is always enough.
        parts.append(
            "## الخطة السابقة (نقطة البدء — لا تعد التخطيط من الصفر)\n"
            f"```json\n{prev_json[:4000]}\n```\n"
            "⬆️ هاي آخر خطة وافق/اعترض عليها الأونر. عدّل عليها فقط حسب "
            "التعديلات المرفقة — احتفظ بكل قرار سابق ما طلب الأونر تغييره "
            "(الـ stack، أسماء الـ features الأخرى، الـ scope، إلخ)."
        )
    if revision_text:
        parts.append(
            "## تعديلات الأونر على الخطة السابقة (إلزامية — تتقدم على أي default)\n"
            f"{revision_text[:1500]}\n"
            "⚠️ هاي قيود الأونر الحرفية. طبّقها بدقة على الخطة السابقة، ولا "
            "تتجاوز ما طلبه — لا تعيدي ترتيب أو حذف ما لم يذكره."
        )
    parts.append("أنشئ PLAN كامل بـ JSON حسب الـ schema.")
    user_msg = "\n\n".join(parts)

    resp = code_llm.complete_json(
        system=_PLAN_SYSTEM,
        user_message=user_msg,
        schema_hint=_PLAN_SCHEMA,
        max_tokens=16000,
        temperature=0.2,
    )
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error") or "Sonnet فشل في توليد الخطة"}

    plan = resp.get("json")
    if not isinstance(plan, dict):
        return {"ok": False, "error": "Sonnet رد بشي مش JSON dict"}

    _normalize_plan(plan)
    err = _validate_plan(plan)
    if err:
        return {"ok": False, "error": err, "raw_plan": plan}

    # M13: feature-concreteness audit. If any feature is too vague, retry the
    # plan once with an explicit directive pointing out which feature failed
    # and what's missing. Cap at one retry — beyond that the owner will see
    # the vague features in the plan preview and can revise.
    vague_issues = _audit_plan_concreteness(plan)
    if vague_issues:
        logger.info(
            "[SA8] plan has %d vague feature(s) — regenerating once: %s",
            len(vague_issues),
            ", ".join(f"{i['name']} ({i['reason']})" for i in vague_issues),
        )
        retry_msg = user_msg + "\n\n" + (
            "## ⚠️ المحاولة السابقة فيها features غامضة — التزمي بهالقواعد:\n"
            + "\n".join(
                f"- `{i['name']}`: {i['reason']}" for i in vague_issues
            )
            + "\n\nقواعد ملزمة لكل feature في الـ retry:\n"
            "1. description لازم يكون ≥ 80 حرف.\n"
            "2. لازم يحتوي فعل ملموس واحد على الأقل (أضيفي/احذفي/استبدلي/"
            "اربطي/أنشئي/عدّلي/انقلي)، مش فقط أفعال نية "
            "(مواءمة/تحسين/تنسيق/ضبط).\n"
            "3. لازم يذكر ملف أو مسار محدد (`index.html`, `assets/main.css`, "
            "إلخ) أو السلوك الملموس المتوقع.\n"
            "4. لو الـ feature أصلاً غامضة، فكّكيها لـ 2-3 features أصغر "
            "بدل ما تتركيها كما هي."
        )
        retry_resp = code_llm.complete_json(
            system=_PLAN_SYSTEM,
            user_message=retry_msg,
            schema_hint=_PLAN_SCHEMA,
            max_tokens=16000,
            temperature=0.2,
        )
        retry_plan = retry_resp.get("json") if retry_resp.get("ok") else None
        if isinstance(retry_plan, dict):
            _normalize_plan(retry_plan)
            if not _validate_plan(retry_plan):
                # Always prefer the retry — even if some features are still
                # vague, the retry can't be worse than the original (the
                # owner will still see warnings).
                plan = retry_plan
                vague_issues = _audit_plan_concreteness(plan)

    # Surface any remaining vague features so the plan-formatter can warn
    # the owner before they approve.
    if vague_issues:
        plan["_vague_warnings"] = vague_issues
        logger.warning(
            "[SA8] plan still has %d vague feature(s) after retry",
            len(vague_issues),
        )

    return {"ok": True, "plan": plan}


def _normalize_plan(plan: Dict[str, Any]) -> None:
    """Coerce common LLM output variants into the canonical features schema.

    Handles legacy `groups` output by folding each group's files into a
    single feature description so the rest of the pipeline can run.
    """
    features = plan.get("features")
    if isinstance(features, list) and features:
        return

    groups = plan.get("groups") or []
    if isinstance(groups, list) and groups:
        converted = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            files = g.get("files") or []
            n_loc = sum(
                int(f.get("estimated_loc") or 0)
                for f in files
                if isinstance(f, dict)
            )
            hints = "; ".join(
                f"{(f.get('path') or '').lstrip('/')} — {f.get('purpose', '')}"
                for f in files
                if isinstance(f, dict)
            )
            converted.append({
                "name": g.get("name") or "feature",
                "description": hints or g.get("name", "feature"),
                "intent": "create_only",
                "wiring_required": False,
                "estimated_files": len(files),
                "estimated_loc": n_loc,
            })
        if converted:
            plan["features"] = converted


def _validate_plan(plan: Dict[str, Any]) -> Optional[str]:
    """Cheap structural validation — defense against runaway/malformed LLM output."""
    features = plan.get("features")
    if not isinstance(features, list) or not features:
        return "الخطة بدون features"
    if len(features) > _MAX_FEATURES:
        return f"عدد الـ features كبير ({len(features)} > {_MAX_FEATURES})"
    total_files = 0
    for i, ft in enumerate(features):
        if not isinstance(ft, dict):
            return f"feature #{i} ليس object"
        if not (ft.get("name") or "").strip():
            return f"feature #{i} بدون name"
        desc = (ft.get("description") or "").strip()
        if not desc:
            return f"feature '{ft.get('name')}' بدون description"
        if len(desc) < 60:
            return (
                f"feature '{ft.get('name')}' description قصير جداً "
                f"({len(desc)} حرف) — لازم 60+ حرف يوضّح ايش الميزة بالضبط"
            )
        try:
            total_files += int(ft.get("estimated_files") or 0)
        except (TypeError, ValueError):
            pass
    if total_files > _MAX_FILES:
        return f"عدد الملفات المتوقع كبير ({total_files} > {_MAX_FILES})"
    return None


def _format_plan_for_owner(
    plan: Dict[str, Any],
    *,
    autonomous: bool = False,
    repo_name: str = "",
) -> str:
    """Render the structured plan into a Telegram-friendly markdown block.

    Repo name + mode are surfaced near the top so the owner sees both
    in one glance and can `الغي` early if either is wrong.
    """
    lines: List[str] = []
    title = plan.get("title", "(بدون عنوان)")
    summary = plan.get("summary", "")
    stack = plan.get("stack") or []
    budget = _estimate_budget(plan)

    lines.append(f"**{title}**")
    if summary:
        lines.append(summary[:400])
    lines.append("")
    if repo_name:
        lines.append(f"📦 Repo: `{repo_name}`")
    if stack:
        lines.append(f"⚙️ Stack: {', '.join(str(s) for s in stack[:6])}")
    lines.append(f"📁 {budget['n_files']} ملف، ~{budget['n_loc']} سطر")
    if budget["level"] != "ok":
        lines.append("")
        lines.append(budget["label"])
        for note in budget["notes"]:
            lines.append(f"  • {note}")
        lines.append("  هاد المشروع حيخصم من رصيدك بسبب حجمه — قول 'الغي' لو ما بدك تكمل.")
    if autonomous:
        lines.append("")
        lines.append("🤖 وضع autonomous — رح أكمل كل الـ features بدون توقف بعد ما توافق على الخطة")
    lines.append("")
    lines.append("📋 الفيتشرز:")
    for i, ft in enumerate(plan.get("features", []), 1):
        wiring = " 🔌" if ft.get("wiring_required") else ""
        intent = ft.get("intent", "create_only")
        intent_label = {
            "create_only": "ملفات جديدة",
            "modify_existing": "تعديل موجود",
            "mixed": "جديد + تعديل",
        }.get(intent, intent)
        lines.append(
            f"{i}. **{ft.get('name', '?')}**{wiring} — {intent_label} "
            f"(~{ft.get('estimated_files', '?')} ملف، ~{ft.get('estimated_loc', '?')} سطر)"
        )
        desc = (ft.get("description") or "").strip()
        if desc:
            lines.append(f"   {desc[:200]}")

    qs = plan.get("open_questions") or []
    if qs:
        lines.append("")
        lines.append("❓ أسئلة قبل البدء:")
        for q in qs[:5]:
            lines.append(f"   • {q}")

    tests_q = plan.get("tests_question", "")
    if tests_q:
        lines.append("")
        lines.append(f"🧪 {tests_q}")

    # M13: warn the owner if any feature is still vague after our retry.
    vague = plan.get("_vague_warnings") or []
    if vague:
        lines.append("")
        lines.append("⚠️ Features غامضة — يفضّل تعدّل عليها قبل الموافقة:")
        for v in vague[:5]:
            lines.append(f"   • `{v.get('name', '?')}` — {v.get('reason', '')}")
        lines.append("   اكتبلي تعديلك (مثلاً 'وضّحي feature X: عدّلي ملف Y بـ Z').")

    lines.append("")
    lines.append("اتفقنا؟ ردّ 'اه' للموافقة، أو اكتب تعديلاتك على الخطة.")
    return "\n".join(lines)


# Public entry point.
def process_project_task(task_id: str) -> Dict[str, Any]:
    """Dispatched from `orchestrator.process_task` when type == project_builder."""
    task = task_state.get_task(task_id)
    if task is None:
        return {"ok": False, "final_status": "unknown", "error": "task not found"}

    # Resume from checkpoint (Worker SIGTERM mid wait_for_resume).
    stage = task.get("stage") or ""
    if stage == task_state.STAGE_PROJECT_PLAN_REVIEW and task.get("plan_json"):
        logger.info("[SA8] resuming task=%s from project_plan_review", task_id)
        return _continue_after_plan_review(task_id, task)
    if stage == task_state.STAGE_PROJECT_GROUP_REVIEW and task.get("plan_json"):
        logger.info("[SA8] resuming task=%s from project_group_review", task_id)
        return _continue_after_group_review(task_id, task)
    if stage == task_state.STAGE_PROJECT_AGENT_QUESTION and task.get("plan_json"):
        logger.info("[SA8] resuming task=%s from project_agent_question", task_id)
        return _continue_after_agent_question(task_id, task)
    if stage == task_state.STAGE_WAITING_CI and task.get("last_commit_sha") and task.get("plan_json"):
        logger.info("[SA8] resuming task=%s from waiting_ci", task_id)
        return _continue_after_ci_wait(task_id, task)

    attempts = int(task.get("attempts") or 0)
    if attempts >= task_state.MAX_ATTEMPTS:
        task_state.set_status(
            task_id,
            task_state.STATUS_FAILED,
            where_we_stopped=f"تجاوز الحد ({task_state.MAX_ATTEMPTS} محاولات)",
        )
        notifier.notify_needs_human(
            task_id=task_id,
            reason=f"تجاوز {task_state.MAX_ATTEMPTS} محاولات بدون نجاح",
            branch=task.get("branch", ""),
        )
        return {"ok": False, "final_status": "failed", "error": "max attempts exceeded"}

    new_attempt = task_state.increment_attempts(task_id)
    task_state.set_status(task_id, task_state.STATUS_IN_PROGRESS)
    logger.info(
        "[project_builder] starting task=%s attempt=%d",
        task_id, new_attempt,
    )

    # 1) Provision a fresh GitHub repo BEFORE we branch into it.
    prov = _ensure_external_repo(task_id, task)
    if not prov.get("ok"):
        return _fail(task_id, task, prov.get("error") or "external repo provision failed")
    # Refresh task so task.repo points to the new repo for the rest of the flow
    task = task_state.get_task(task_id) or task

    # 2) Isolated branch on the new repo
    br = branch_ops.github_create_branch(task_id, repo=task.get("repo") or None)
    if not br.get("ok"):
        return _fail(task_id, task, f"branch creation failed: {br.get('error')}")
    branch = br["branch"]
    task_state.record_branch(task_id, branch)

    # 3) Project context — generic for new repos, README+tree for existing
    repo_mode = str(task.get("repo_mode") or "new")
    context = _read_project_context(task.get("repo") or None, repo_mode=repo_mode)

    # 4) Sonnet → PLAN
    plan_result = _generate_plan(
        task.get("description") or "",
        context,
        repo_mode=repo_mode,
    )
    if not plan_result.get("ok"):
        return _fail(task_id, task, plan_result.get("error") or "plan generation failed")
    plan = plan_result["plan"]

    # 5) Persist BEFORE notify so a SIGTERM in the next 100ms doesn't lose it
    task_state.save_project_plan(task_id, plan)

    # 6) Notify + mark waiting (include budget warning + autonomous flag)
    autonomous = _is_autonomous(task)
    plan_md = _format_plan_for_owner(
        plan,
        autonomous=autonomous,
        repo_name=str(task.get("repo_name") or ""),
    )
    chat_id = task.get("chat_id") or notifier.get_owner_chat_id()
    notifier.notify_project_plan(task_id=task_id, plan_md=plan_md)
    task_state.mark_waiting_user(
        task_id,
        where_we_stopped=f"PLAN: {plan.get('title', '')[:200]}",
        chat_id=str(chat_id) if chat_id else None,
    )

    # 7) Wait for owner decision (approve / revise / cancel / expire)
    decision = _await_plan_decision(task_id, task, plan, context)
    if decision["outcome"] == "shutdown":
        return _requeue_for_shutdown(task_id, task, where="project_plan_review")
    if decision["outcome"] == "expired":
        notifier.notify_expired(task_id=task_id)
        return {"ok": False, "final_status": "expired"}
    if decision["outcome"] == "cancelled":
        return {"ok": False, "final_status": "failed", "error": "cancelled"}

    task = task_state.get_task(task_id) or task
    plan = decision["plan"]
    return _after_plan_approval(task_id, task, plan, branch, attempts_so_far=new_attempt)


def _continue_after_plan_review(task_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Resume entry after Worker SIGTERM while we were waiting for plan approval."""
    branch = task.get("branch") or ""
    plan = task_state.get_project_plan(task_id)
    if not branch or not isinstance(plan, dict):
        return _fail(task_id, task, "resume: branch أو plan ناقصين")

    attempts_so_far = int(task.get("attempts") or 1)
    task_state.set_status(task_id, task_state.STATUS_WAITING_USER)
    logger.info("[project_builder] task=%s resumed wait_for_resume (PLAN already in hash)", task_id)

    repo_mode = str(task.get("repo_mode") or "new")
    context = _read_project_context(task.get("repo") or None, repo_mode=repo_mode)
    decision = _await_plan_decision(task_id, task, plan, context)
    if decision["outcome"] == "shutdown":
        return _requeue_for_shutdown(task_id, task, where="project_plan_review")
    if decision["outcome"] == "expired":
        notifier.notify_expired(task_id=task_id)
        return {"ok": False, "final_status": "expired"}
    if decision["outcome"] == "cancelled":
        return {"ok": False, "final_status": "failed", "error": "cancelled"}

    task = task_state.get_task(task_id) or task
    plan = decision["plan"]
    return _after_plan_approval(task_id, task, plan, branch, attempts_so_far=attempts_so_far)


def _await_plan_decision(
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    project_context: str,
) -> Dict[str, Any]:
    """Block on plan review; loop while the owner sends revisions.

    Outcomes (in returned dict):
        {"outcome": "approved", "plan": <dict>}
        {"outcome": "cancelled"}      — resume_hook already set status=failed
        {"outcome": "expired"}        — wait_for_resume timed out
        {"outcome": "shutdown"}       — Worker SIGTERM, caller should requeue
    """
    current_plan = plan
    description = task.get("description") or ""
    revision_count = 0
    while True:
        resumed = task_state.wait_for_resume(task_id)
        if not resumed:
            if sa_shutdown.is_shutdown_requested():
                return {"outcome": "shutdown"}
            return {"outcome": "expired"}

        # Check status — if resume_hook marked it failed (cancel intent) we
        # bail before reading revision text so we don't accidentally re-plan.
        latest = task_state.get_task(task_id) or {}
        if (latest.get("status") or "") == task_state.STATUS_FAILED:
            return {"outcome": "cancelled"}

        revision_text = task_state.pop_plan_revision_text(task_id)
        if not revision_text:
            return {"outcome": "approved", "plan": current_plan}

        revision_count += 1
        logger.info(
            "[SA8] task=%s plan revision #%d (len=%d)",
            task_id, revision_count, len(revision_text),
        )
        new_plan_result = _generate_plan(
            description,
            project_context,
            revision_text=revision_text,
            previous_plan=current_plan,
            repo_mode=str(task.get("repo_mode") or "new"),
        )
        if not new_plan_result.get("ok"):
            err = new_plan_result.get("error") or "plan regeneration failed"
            notifier.notify_owner(
                f"⚠️ ما قدرت أعيد توليد الخطة بناء على تعديلك (task `{task_id}`): "
                f"{err[:300]}. الخطة الأصلية لسا قيد المراجعة — جرب 'اه' للموافقة "
                "عليها زي ما هي، أو 'الغي'، أو ابعت صيغة تانية للتعديل."
            )
            task_state.mark_waiting_user(
                task_id,
                where_we_stopped=f"PLAN (revision #{revision_count} فشلت): "
                                 f"{current_plan.get('title', '')[:200]}",
                chat_id=str(task.get("chat_id") or "") or None,
            )
            continue

        current_plan = new_plan_result["plan"]
        task_state.save_project_plan(task_id, current_plan)
        plan_md = _format_plan_for_owner(
            current_plan,
            autonomous=_is_autonomous(task),
            repo_name=str(task.get("repo_name") or ""),
        )
        notifier.notify_owner(
            f"🏗️ خطة محدّثة (revision #{revision_count}) — task `{task_id}`\n\n"
            f"{plan_md[:3700]}"
        )
        task_state.mark_waiting_user(
            task_id,
            where_we_stopped=f"PLAN (revision #{revision_count}): "
                             f"{current_plan.get('title', '')[:200]}",
            chat_id=str(task.get("chat_id") or "") or None,
        )
        # Loop — wait for next decision on the revised plan


def _after_plan_approval(
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
    *,
    attempts_so_far: int,
) -> Dict[str, Any]:
    """Phase B entry — iterate over plan.groups from index 0."""
    return _build_groups_from(
        task_id, task, plan, branch, start_index=0, applied_so_far=[]
    )


def _continue_after_agent_question(task_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Resume entry after owner answers an `ask_owner` from the agent loop.

    Resumes at the SAME feature index — the agent picks up via resume_state
    (which holds the prior messages list + the pending tool_use_id).
    """
    branch = task.get("branch") or ""
    plan = task_state.get_project_plan(task_id)
    if not branch or not isinstance(plan, dict):
        return _fail(task_id, task, "resume: branch أو plan ناقصين")

    try:
        feature_index = int(task.get("current_group_index") or "0")
    except (TypeError, ValueError):
        feature_index = 0

    applied_so_far = task.get("patched_files") or []
    if isinstance(applied_so_far, str):
        try:
            applied_so_far = json.loads(applied_so_far) or []
        except Exception:
            applied_so_far = []

    return _build_groups_from(
        task_id, task, plan, branch,
        start_index=feature_index, applied_so_far=list(applied_so_far),
    )


def _continue_after_group_review(task_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Resume entry after Worker SIGTERM during between-groups wait."""
    branch = task.get("branch") or ""
    plan = task_state.get_project_plan(task_id)
    if not branch or not isinstance(plan, dict):
        return _fail(task_id, task, "resume: branch أو plan ناقصين")

    try:
        next_index = int(task.get("current_group_index") or "0") + 1
    except (TypeError, ValueError):
        next_index = 0

    applied_so_far = task.get("patched_files") or []
    if isinstance(applied_so_far, str):
        try:
            applied_so_far = json.loads(applied_so_far) or []
        except Exception:
            applied_so_far = []

    task_state.set_status(task_id, task_state.STATUS_WAITING_USER)
    logger.info(
        "[SA8] task=%s resumed at group_review (next_index=%d)", task_id, next_index
    )

    resumed = task_state.wait_for_resume(task_id)
    if not resumed:
        if sa_shutdown.is_shutdown_requested():
            return _requeue_for_shutdown(task_id, task, where="project_group_review")
        notifier.notify_expired(task_id=task_id)
        return {"ok": False, "final_status": "expired"}

    task = task_state.get_task(task_id) or task
    return _build_groups_from(
        task_id, task, plan, branch,
        start_index=next_index, applied_so_far=list(applied_so_far),
    )


def _build_groups_from(
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
    *,
    start_index: int,
    applied_so_far: List[str],
) -> Dict[str, Any]:
    """Sequential loop over features starting at `start_index`, each driven
    by an agent loop (`_run_agent_for_feature`).

    Each iteration:
      1. Pick up any pending `ask_owner` resume_state from Redis.
      2. Run the agent until it calls `done`, suspends with `ask_owner`,
         or fails.
      3. If suspended: persist resume_state + notify owner + wait_for_resume,
         then re-enter the same feature with the owner's answer fed back in.
      4. Between features (manual mode): notify + wait_for_resume gate.

    Fails fast on any feature; previous applied files stay on the branch.
    """
    features = plan.get("features") or []
    description = task.get("description") or ""
    repo = task.get("repo") or None
    autonomous = _is_autonomous(task)
    # Cache context once per resume — repo content doesn't change mid-build
    context = _read_project_context(repo, repo_mode=str(task.get("repo_mode") or "new"))

    all_applied: List[str] = list(applied_so_far)
    if autonomous and start_index == 0:
        notifier.notify_owner(
            f"🤖 ببدأ بناء كل الـ features بدون توقف (task `{task_id}`). "
            f"حنخبرك أول ما الـ PR يجهز."
        )

    i = start_index
    while i < len(features):
        feature = features[i] or {}
        feature_name = feature.get("name") or f"Feature {i+1}"

        # Pick up any prior ask_owner resume on this feature
        resume_state = task_state.get_agent_resume_state(task_id)
        if resume_state:
            owner_answer = (task.get("agreed_solution") or "").strip()
            if owner_answer:
                resume_state["owner_answer"] = owner_answer

        on_call = _make_tool_call_notifier(task_id, feature_name)
        agent_result = _run_agent_for_feature(
            feature=feature,
            plan=plan,
            description=description,
            project_context=context,
            branch=branch,
            base_branch=task.get("base_branch") or "main",
            repo=repo,
            task_id=task_id,
            resume_state=resume_state,
            on_tool_call=on_call,
        )
        all_applied.extend(agent_result.get("applied", []))

        if agent_result.get("suspended"):
            # ask_owner OR stall fired — persist resume state and wait for
            # owner reply. The agent already wrote the full question text;
            # we just frame it (stall vs normal question) and add a branch
            # link so the owner can inspect what's already on the branch.
            chat_id = task.get("chat_id") or notifier.get_owner_chat_id()
            resume_state = agent_result.get("resume_state") or {}
            task_state.save_agent_resume_state(
                task_id,
                resume_state=resume_state,
                current_feature_index=i,
                chat_id=chat_id,
            )
            question = agent_result.get("question", "")
            is_stall = bool(resume_state.get("stall"))
            repo = task.get("repo") or ""
            branch_url = (
                f"https://github.com/{repo}/tree/{branch}" if repo and branch else ""
            )
            if is_stall:
                header = f"🛑 وقفت على feature '{feature_name}' (task `{task_id}`)"
                footer = (
                    f"\n\n🔗 شوف البرانش الآن: {branch_url}" if branch_url else ""
                )
                notifier.notify_owner(f"{header}\n\n{question}{footer}")
            else:
                notifier.notify_owner(
                    f"❓ ساندي بتسألك على feature '{feature_name}' "
                    f"(task `{task_id}`):\n\n{question}\n\nرد لتكمل، أو 'الغي'."
                )
            chat_id = task.get("chat_id") or notifier.get_owner_chat_id()
            task_state.mark_waiting_user(
                task_id,
                where_we_stopped=f"Feature {i+1}/{len(features)} — agent سأل",
                chat_id=str(chat_id) if chat_id else None,
            )
            resumed = task_state.wait_for_resume(task_id)
            if not resumed:
                if sa_shutdown.is_shutdown_requested():
                    return _requeue_for_shutdown(
                        task_id, task, where="project_agent_question"
                    )
                notifier.notify_expired(task_id=task_id)
                return {"ok": False, "final_status": "expired"}
            task = task_state.get_task(task_id) or task
            # Loop back into the same feature index (don't increment i)
            continue

        # Clear any prior resume state — feature finished (success or fail)
        task_state.clear_agent_resume_state(task_id)

        if not agent_result.get("ok"):
            return _fail_partial(
                task_id, task,
                reason=f"agent فشل على feature {feature_name}: {agent_result.get('error')}",
                applied=all_applied,
                stopped_at=feature_name,
            )

        # Last feature → skip the gate, go to Phase C
        if i == len(features) - 1:
            break

        # Save progress checkpoint (protects against mid-build SIGTERM)
        task_state.save_project_group_progress(task_id, current_group_index=i)

        if sa_shutdown.is_shutdown_requested():
            return _requeue_for_shutdown(task_id, task, where="project_group_review")

        if autonomous:
            logger.info(
                "[SA8] task=%s autonomous — Feature %d/%d done, continuing",
                task_id, i + 1, len(features),
            )
            i += 1
            continue

        notifier.notify_project_group_done(
            task_id=task_id,
            group_name=feature_name,
            files=agent_result.get("applied", []),
            n_done=i + 1,
            n_total=len(features),
        )
        chat_id = task.get("chat_id") or notifier.get_owner_chat_id()
        task_state.mark_waiting_user(
            task_id,
            where_we_stopped=f"Feature {i+1}/{len(features)} — {feature_name}",
            chat_id=str(chat_id) if chat_id else None,
        )
        resumed = task_state.wait_for_resume(task_id)
        if not resumed:
            if sa_shutdown.is_shutdown_requested():
                return _requeue_for_shutdown(task_id, task, where="project_group_review")
            notifier.notify_expired(task_id=task_id)
            return {"ok": False, "final_status": "expired"}
        task = task_state.get_task(task_id) or task
        i += 1

    # All features done — Phase C
    return _phase_c_placeholder(task_id, task, plan, branch, all_applied)


# Agent-driven feature build (M8).
_AGENT_SYSTEM_BASE = (
    "أنت ساندي (مهندسة برمجيات تعمل على نفسها أو على مشروع خارجي). "
    "تستخدمين أدوات (search_code / list_tree / read_file / apply_patch / "
    "write_new_file / ask_owner / get_branch_diff / done) لتنفيذ feature "
    "وحدة من الخطة المعتمدة.\n\n"
    "أسلوب العمل:\n"
    "1) ابدئي بفحص بنية الـ repo (`list_tree`) لو ما عندك سياق كافٍ.\n"
    "2) ابحثي عن patterns موجودة (`search_code`) قبل ما تكتبي كود جديد — اتّبعي "
    "أسلوب الكود الحالي بدل ما تختلقي شي جديد.\n"
    "3) اقرأي الملفات المرجعية المختصرة (`read_file` بـ line_range) بدل ما "
    "تقرئي ملفات كاملة — وفّري tokens.\n"
    "4) للتعديل على ملف موجود: اقرأيه أولاً علشان تعرفي الأسطر بالضبط، بعدين "
    "`apply_patch`. لا تعيدي كتابة الملف كله.\n"
    "5) لو الفيتشر محتاج wiring (tool registry, dispatcher, maestro prompt) "
    "لا تنسي تربطي الجديد بالقديم.\n"
    "6) لو في غموض أو محتاجة موافقة على ملف محمي (requirements.txt مثلاً): "
    "`ask_owner`. سؤال واضح، خيارات مقترحة.\n"
    "7) قبل `done`: `get_branch_diff` للمراجعة الذاتية.\n"
    "8) `done(summary)` بعربي مختصر يلخّص الملفات الجديدة + التعديلات + أي "
    "تنبيهات للأونر.\n\n"
    "⚡ كوني حازمة وسريعة:\n"
    "- بعد 3-5 reads أقصى، ابدئي تكتبي/تعدّلي. الـ search/read بدون كتابة "
    "هدر للـ tokens.\n"
    "- لو بحثتي عن نمط ولاقيتيه — لا تقرئي 5 نسخ مختلفة منه. واحد كافي.\n"
    "- اقرئي بـ line_range محدد (مثلاً '1-50') بدل ما تقرئي ملف كامل.\n\n"
    "🚨 قاعدة حازمة: كل رد لازم ينتهي بـ tool call. لو خلصتي الفيتشر — "
    "نادي `done(summary=...)`. لا ترجعي نص توصيف بدون tool — الـ runner "
    "بيفشل المهمة لو ما استدعيتي شي.\n\n"
    "🆘 الـ scope غامض؟ نادي `ask_owner` مبكّراً — أحسن من 10 reads حدسية:\n"
    "- لو وصف الفيتشر فيه أفعال نية (مواءمة/تحسين/تنسيق/ضبط) بدون مرجع "
    "ملف ملموس → نادي `ask_owner` قبل أي read، اسألي 'وضّحلي بالضبط شو "
    "الـ output المتوقع'.\n"
    "- لو بعد 3 reads ما اتضحت الصورة → `ask_owner` فوراً بدل ما تخمّني.\n"
    "- ask_owner مش ضعف — هو أسرع طريقة تخلصي مهمة غامضة.\n"
)


def _build_agent_system_prompt(project_context: str) -> str:
    """Build the full system prompt — base rules + repo context."""
    parts = [_AGENT_SYSTEM_BASE]
    if project_context:
        parts.append("\n## سياق الـ repo\n" + project_context[:4000])
    return "\n".join(parts)


def _build_agent_user_message(
    *,
    feature: Dict[str, Any],
    plan: Dict[str, Any],
    description: str,
) -> str:
    name = feature.get("name", "?")
    desc = feature.get("description", "")
    intent = feature.get("intent", "create_only")
    wiring = feature.get("wiring_required", False)
    intent_label = {
        "create_only": "ملفات جديدة فقط",
        "modify_existing": "تعديل على كود موجود",
        "mixed": "ملفات جديدة + تعديل على موجود",
    }.get(intent, intent)
    msg = (
        f"## الطلب الأصلي من الأونر\n{description[:1500]}\n\n"
        f"## ملخّص الخطة\n{plan.get('title', '')} — {plan.get('summary', '')[:400]}\n\n"
        f"## الفيتشر الحالي اللي بدك تنفّذيه\n"
        f"الاسم: {name}\n"
        f"الوصف: {desc}\n"
        f"النوع: {intent_label}\n"
        f"محتاج wiring: {'نعم' if wiring else 'لا'}\n\n"
    )
    # M12: if the feature itself reads as vague, push the agent to call
    # ask_owner before burning reads.
    if _is_feature_concrete(feature):
        msg += (
            "⚠️ **مهم:** الـ feature هاي قرأتها غامضة (إما فعل نية بدون مسار ملف "
            "أو وصف قصير). **نادي `ask_owner` كأول tool**، اسألي الأونر "
            "بالضبط شو الـ output اللي بدّه يشوفه (مثال محدد، ملف، سلوك). "
            "لا تبدئي reads على افتراضات.\n\n"
        )
    msg += "ابدئي شغلك. نادي `done` لما تخلّصي الفيتشر بالكامل."
    return msg


def _make_tool_call_notifier(task_id: str, feature_name: str):
    """Build an on_tool_call callback that emits user-facing notifications
    for the meaningful tool calls (writes / patches / questions / done) and
    just logs the rest. Search/read get aggregated into a periodic "بتفكر"
    pulse so the owner knows the agent's alive without being spammed.
    """
    counter = {"n_quiet": 0}
    prefix = f"🤖 `{task_id}` ({feature_name})"

    def _on_call(name: str, args: Dict[str, Any], result: str) -> None:
        try:
            if name == "write_new_file":
                path = (args.get("path") or "").strip()
                notifier.notify_owner(f"{prefix}\n📝 ملف جديد: `{path}`")
            elif name == "apply_patch":
                path = (args.get("path") or "").strip()
                s = args.get("start_line")
                e = args.get("end_line")
                notifier.notify_owner(
                    f"{prefix}\n✏️ تعديل: `{path}` (السطور {s}-{e})"
                )
            elif name == "ask_owner":
                # The question itself is surfaced by the suspend handler — no
                # need to double-notify here.
                pass
            elif name == "done":
                # `done` summary is surfaced by the higher-level feature flow.
                pass
            elif name in ("search_code", "read_file", "list_tree", "get_branch_diff"):
                counter["n_quiet"] += 1
                if counter["n_quiet"] % 5 == 0:
                    notifier.notify_owner(
                        f"{prefix}\n🔍 بتستكشف الكود... ({counter['n_quiet']} عملية بحث/قراءة لحد الآن)"
                    )
        except Exception as exc:
            logger.debug("[SA8] notifier callback failed: %s", exc)

    return _on_call


def _run_agent_for_feature(
    *,
    feature: Dict[str, Any],
    plan: Dict[str, Any],
    description: str,
    project_context: str,
    branch: str,
    base_branch: str,
    repo: Optional[str],
    task_id: str,
    resume_state: Optional[Dict[str, Any]] = None,
    on_tool_call=None,
) -> Dict[str, Any]:
    """Run one agent loop for a single feature.

    Returns one of:
        Done      → {ok: True, applied: [...], summary, usage}
        Suspended → {ok: True, suspended: True, question, resume_state,
                     applied: [...] (so-far)}
        Failure   → {ok: False, error, applied: [...] (so-far)}
    """
    ctx = coding_agent_tools.AgentContext(
        branch=branch,
        base_branch=base_branch or "main",
        repo=repo,
        task_id=task_id,
    )
    system_prompt = _build_agent_system_prompt(project_context)
    user_msg = _build_agent_user_message(
        feature=feature, plan=plan, description=description
    )

    result = coding_agent.run_agent(
        task_description=user_msg,
        ctx=ctx,
        system_prompt=system_prompt,
        on_tool_call=on_tool_call,
        resume_state=resume_state,
    )

    applied = list(ctx.files_created) + list(ctx.files_patched)
    if result.get("suspended"):
        return {
            "ok": True,
            "suspended": True,
            "question": result.get("question", ""),
            "resume_state": result.get("resume_state") or {},
            "applied": applied,
        }
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error"), "applied": applied}
    return {
        "ok": True,
        "applied": applied,
        "summary": result.get("summary", ""),
        "usage": result.get("usage") or {},
    }


# Phase C: wait for CI, then open the PR.
_SELF_REVIEW_SYSTEM = (
    "أنت ساندي في جولة مراجعة ذاتية بعد بناء الـ project. شغلك الوحيد: "
    "إصلاح الملاحظات المرفقة فقط. لا تضيفي features، لا تعيدي توليد ملفات، "
    "لا تنادي ask_owner (هاي مراجعة تلقائية، الأونر مش جاهز يرد). "
    "استخدمي read_file → apply_patch / write_new_file → done."
)


def _run_self_review_pass(
    *,
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
    applied: List[str],
) -> Dict[str, Any]:
    """M10: scan the branch, optionally run one correction round, return
    the post-review snapshot.

    Returns:
        {
            "applied": List[str],              # original + any correction files
            "remaining_issues": List[Dict],    # what we couldn't auto-fix
            "skipped": bool,                   # review disabled / no scope
        }
    """
    repo = task.get("repo") or None
    if not repo:
        return {"applied": applied, "remaining_issues": [], "skipped": True}

    review = self_review.run_review(
        repo=repo, branch=branch, plan=plan, applied_files=applied,
    )
    if review.get("skipped") or not review.get("issues"):
        return {"applied": applied, "remaining_issues": [], "skipped": review.get("skipped", False)}

    issues = review["issues"]
    logger.info(
        "[SA8] self-review task=%s found %d issues — starting correction round",
        task_id, len(issues),
    )
    notifier.notify_owner(
        f"🔍 مراجعة ذاتية لقت {len(issues)} ملاحظة على `{branch}` — "
        f"بحاول أصلّحها قبل الـ PR..."
    )

    ctx = coding_agent_tools.AgentContext(
        branch=branch,
        base_branch="main",
        repo=repo,
        task_id=task_id,
    )
    # Pre-approve every file already touched so apply_patch doesn't re-ask.
    for path in applied:
        if isinstance(path, str) and path:
            ctx.approved_paths.add(path)

    on_call = _make_tool_call_notifier(task_id, "مراجعة ذاتية")

    correction_task = self_review.format_correction_task(issues)
    result = coding_agent.run_agent(
        task_description=correction_task,
        ctx=ctx,
        system_prompt=_SELF_REVIEW_SYSTEM,
        on_tool_call=on_call,
        max_iterations=15,  # correction is narrow; cap small to bound cost
    )

    correction_applied = list(ctx.files_created) + list(ctx.files_patched)
    merged_applied = list(applied) + [
        p for p in correction_applied if p not in applied
    ]

    if not result.get("ok"):
        # Don't fail the whole task — surface the issues in the PR body
        logger.warning(
            "[SA8] self-review correction failed for task=%s: %s",
            task_id, result.get("error"),
        )
        return {
            "applied": merged_applied,
            "remaining_issues": issues,
            "skipped": False,
        }
    if result.get("suspended"):
        # The system prompt told it not to call ask_owner, but if it did
        # anyway, treat it as "couldn't auto-fix" and move on.
        logger.warning(
            "[SA8] self-review correction asked owner — ignoring (task=%s)",
            task_id,
        )
        return {
            "applied": merged_applied,
            "remaining_issues": issues,
            "skipped": False,
        }

    # Re-run once to see what's left
    review2 = self_review.run_review(
        repo=repo, branch=branch, plan=plan, applied_files=merged_applied,
    )
    remaining = review2.get("issues") or []
    if remaining:
        notifier.notify_owner(
            f"⚠️ المراجعة الذاتية بعد التصحيح لسا فيها {len(remaining)} "
            f"ملاحظة — رح أضيفها في وصف الـ PR للمراجعة البشرية."
        )
    else:
        notifier.notify_owner("✅ المراجعة الذاتية نظيفة بعد التصحيح.")
    return {
        "applied": merged_applied,
        "remaining_issues": remaining,
        "skipped": False,
    }


def _phase_c_placeholder(
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
    applied: List[str],
) -> Dict[str, Any]:
    """All groups written. Wait for CI on last commit, then open PR."""
    # M10: self-review BEFORE we lock in the commit and start waiting for CI,
    # so any corrections land in the same CI cycle.
    review_outcome = _run_self_review_pass(
        task_id=task_id, task=task, plan=plan, branch=branch, applied=applied,
    )
    applied = review_outcome["applied"]
    remaining_review_issues = review_outcome.get("remaining_issues") or []

    # Refresh in case the correction round added commits
    last_commit_sha = (task_state.get_task(task_id) or {}).get("last_commit_sha", "")
    if not last_commit_sha:
        return _fail(task_id, task, "Phase C: ما عندي last_commit_sha — لا commit اتعمل")

    # Stash issues so the PR body picks them up later
    if remaining_review_issues:
        sa_redis.task_hset(
            task_id,
            {"self_review_issues": json.dumps(remaining_review_issues, ensure_ascii=False)},
        )

    # Tell the owner we moved on from Phase B
    notifier.notify_owner(
        f"✅ خلصت بناء كل الـ groups (task `{task_id}`)\n"
        f"📁 {len(applied)} ملف على `{branch}`\n"
        f"⏳ هلق ناطر CI..."
    )

    # Checkpoint BEFORE wait so SIGTERM mid-wait resumes the poll on next boot.
    task_state.save_patch_state(
        task_id,
        applied_files=applied,
        commit_sha=last_commit_sha,
    )

    ci = ci_status.wait_for_ci(
        last_commit_sha,
        branch=branch,
        repo=task.get("repo") or None,
    )
    if ci.get("state") == "shutdown":
        return _requeue_for_shutdown(task_id, task, where="waiting_ci")

    return _finalize_after_project_ci(
        task_id=task_id,
        task=task,
        plan=plan,
        branch=branch,
        applied=applied,
        ci_result=ci,
    )


def _continue_after_ci_wait(task_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Resume after SIGTERM during wait_for_ci. Files already on branch."""
    branch = task.get("branch") or ""
    commit_sha = task.get("last_commit_sha") or ""
    plan = task_state.get_project_plan(task_id) or {}
    if not branch or not commit_sha or not plan:
        return _fail(task_id, task, "resume waiting_ci: branch/commit/plan ناقصين")

    applied = task.get("patched_files") or []
    if isinstance(applied, str):
        try:
            applied = json.loads(applied) or []
        except Exception:
            applied = []

    task_state.set_status(task_id, task_state.STATUS_IN_PROGRESS)
    logger.info(
        "[SA8] task=%s resumed wait_for_ci (commit=%s, branch=%s)",
        task_id, commit_sha[:8], branch,
    )

    ci = ci_status.wait_for_ci(
        commit_sha,
        branch=branch,
        repo=task.get("repo") or None,
    )
    if ci.get("state") == "shutdown":
        return _requeue_for_shutdown(task_id, task, where="waiting_ci")

    return _finalize_after_project_ci(
        task_id=task_id,
        task=task,
        plan=plan,
        branch=branch,
        applied=list(applied),
        ci_result=ci,
    )


# M7: GitHub Pages auto-publish.
_STATIC_STACK_TOKENS = {"html", "css", "javascript", "js", "vanilla"}
_NON_STATIC_STACK_TOKENS = {
    "python", "node.js", "node", "ruby", "go", "rust", "java",
    "react", "vue", "svelte", "next.js", "next", "typescript", "ts",
    "vite", "webpack", "django", "flask", "fastapi", "express",
}


def _is_static_project(plan: Dict[str, Any]) -> bool:
    """A project is "static" (Pages-eligible) when its declared stack is
    plain HTML / CSS / JS only — no server runtime or build tool.

    Conservative: any non-static token in the stack disqualifies the
    whole project. False negatives are fine — a Pages deploy attempt
    on a Node/React project would fail or build nothing useful."""
    stack = plan.get("stack") or []
    if not stack:
        return False
    tokens = {str(s).strip().lower() for s in stack if s}
    if tokens & _NON_STATIC_STACK_TOKENS:
        return False
    return bool(tokens & _STATIC_STACK_TOKENS)


def _maybe_enable_pages(
    *,
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
) -> Optional[str]:
    """Enable GitHub Pages on the project's default branch when the
    stack qualifies. Returns the Pages URL if enabled (or already on),
    else None. Errors are swallowed — Pages publishing is a nice-to-have
    and must never fail the broader 'done' flow."""
    if not _is_static_project(plan):
        return None
    repo = task.get("repo") or ""
    if not repo:
        return None
    try:
        # Source = main: the URL goes live only after the owner merges
        # the PR. Picking the feature branch would expose the in-review
        # state publicly, which we don't want for private projects.
        res = github_api.enable_pages(repo=repo, source_branch="main", source_path="/")
    except Exception as exc:
        logger.debug("[SA8] Pages enable raised for task=%s: %s", task_id, exc)
        return None
    if not res.get("ok"):
        logger.info(
            "[SA8] Pages enable skipped for task=%s: status=%s err=%s",
            task_id, res.get("status"), res.get("error"),
        )
        return None
    url = res.get("url") or ""
    if not url:
        # Try a second fetch — POST sometimes returns 201 with an empty
        # html_url before the field is populated.
        try:
            follow = github_api.get_pages_url(repo)
            url = follow.get("url") or ""
        except Exception:
            url = ""
    if url:
        logger.info(
            "[SA8] Pages %s for task=%s → %s",
            "already enabled" if res.get("already_enabled") else "enabled",
            task_id, url,
        )
    return url or None


# Signature appended to the end of every new project's README, plus the repo
# topic that lets the website's Projects page auto-list it. Done as a
# deterministic git step (not left to the LLM) so it is guaranteed present.
_SANDY_SIGNATURE = "\n\n---\n🤖 Created by **Sandy** — AI by Nabeel Alsultan\n"
_SIGNATURE_MARKER = "Created by **Sandy**"
_SANDY_TOPIC = "sandy"


def _stamp_attribution(*, task_id: str, task: Dict[str, Any], branch: str) -> None:
    """Best-effort: append the Sandy signature to README.md on the project
    branch (so it lands in the PR the owner merges) and tag the repo with the
    `sandy` topic. Never fails the broader build flow."""
    repo = task.get("repo") or ""
    if not repo:
        return
    try:
        readme = github_api.get_file_contents("README.md", repo=repo, ref=branch)
        content = readme.get("content") or "" if readme.get("ok") else None
        if content is not None and _SIGNATURE_MARKER not in content:
            github_api.update_file(
                "README.md",
                new_content=content.rstrip() + _SANDY_SIGNATURE,
                sha=readme.get("sha") or "",
                branch=branch,
                message="docs: add Sandy signature",
                repo=repo,
            )
    except Exception as exc:
        logger.debug("[SA8] README signature stamp failed for task=%s: %s", task_id, exc)
    try:
        github_api.add_repo_topics(repo, [_SANDY_TOPIC])
    except Exception as exc:
        logger.debug("[SA8] topic tag failed for task=%s: %s", task_id, exc)


def _finalize_after_project_ci(
    *,
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
    applied: List[str],
    ci_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Open PR + notify based on CI outcome.

    Project Builder never auto-retries on CI failure — a scaffold that
    breaks CI needs human review of the generated code.
    """
    state = ci_result.get("state")
    timed_out = ci_result.get("timed_out")
    loc_est = int(plan.get("estimated_loc") or 0)

    # Stamp the Sandy signature + topic before opening the PR, so it's part of
    # what the owner reviews/merges regardless of the CI outcome.
    _stamp_attribution(task_id=task_id, task=task, branch=branch)

    if state == "success":
        pages_url = _maybe_enable_pages(
            task_id=task_id, task=task, plan=plan, branch=branch,
        )
        pr_note = "✅ CI أخضر — المشروع جاهز للـ merge."
        if pages_url:
            pr_note += (
                f"\n\n🌐 GitHub Pages مفعّل — بعد ما تعمل merge على "
                f"`main`، الموقع رح يصير live على:\n{pages_url}"
            )
        pr = _open_project_pr(
            task_id=task_id,
            task=task,
            plan=plan,
            branch=branch,
            applied=applied,
            draft=False,
            note=pr_note,
        )
        pr_url = pr.get("html_url") if pr.get("ok") else None
        task_state.set_status(
            task_id,
            task_state.STATUS_DONE,
            pr_url=pr_url or "",
        )
        task_state.clear_stage(task_id)
        notifier.notify_project_done(
            task_id=task_id, pr_url=pr_url,
            file_count=len(applied), loc=loc_est,
        )
        if pages_url:
            notifier.notify_owner(
                f"🌐 فعّلت GitHub Pages للمشروع (task `{task_id}`).\n"
                f"بعد ما تعمل merge للـ PR، الموقع رح يكون على:\n{pages_url}"
            )
        return {
            "ok": True,
            "final_status": "done",
            "pr_url": pr_url,
            "pages_url": pages_url,
        }

    if state == "no_runs":
        pr = _open_project_pr(
            task_id=task_id,
            task=task,
            plan=plan,
            branch=branch,
            applied=applied,
            draft=True,
            note="⚠️ ما لقيت CI workflows على الـ branch — راجع المشروع يدوياً قبل الـ merge.",
        )
        pr_url = pr.get("html_url") if pr.get("ok") else None
        task_state.set_status(
            task_id,
            task_state.STATUS_DONE,
            pr_url=pr_url or "",
            where_we_stopped="no CI runs",
        )
        task_state.clear_stage(task_id)
        notifier.notify_project_done(
            task_id=task_id, pr_url=pr_url,
            file_count=len(applied), loc=loc_est,
        )
        return {"ok": True, "final_status": "done_no_ci", "pr_url": pr_url}

    if timed_out:
        pr = _open_project_pr(
            task_id=task_id,
            task=task,
            plan=plan,
            branch=branch,
            applied=applied,
            draft=True,
            note="⌛ CI تعدّى الحد — راجع Actions يدوياً.",
        )
        pr_url = pr.get("html_url") if pr.get("ok") else None
        task_state.set_status(
            task_id,
            task_state.STATUS_FAILED,
            pr_url=pr_url or "",
            where_we_stopped="CI timeout",
        )
        task_state.clear_stage(task_id)
        notifier.notify_needs_human(
            task_id=task_id,
            reason="CI تعدّى 15 دقيقة على المشروع المبني",
            branch=branch,
            partial_files=applied,
        )
        return {"ok": False, "final_status": "ci_timeout", "pr_url": pr_url}

    # CI failure — no automatic retry; surface the broken PR for human review.
    pr = _open_project_pr(
        task_id=task_id,
        task=task,
        plan=plan,
        branch=branch,
        applied=applied,
        draft=True,
        note="❌ CI فشل على المشروع المبني — محتاج مراجعة بشرية للكود المولّد.",
    )
    pr_url = pr.get("html_url") if pr.get("ok") else None
    task_state.set_status(
        task_id,
        task_state.STATUS_FAILED,
        pr_url=pr_url or "",
        where_we_stopped="CI فشل بعد بناء المشروع",
    )
    task_state.clear_stage(task_id)
    notifier.notify_needs_human(
        task_id=task_id,
        reason="CI فشل على المشروع المبني — لا retry تلقائي لمشاريع البناء",
        branch=branch,
        partial_files=applied,
    )
    return {"ok": False, "final_status": "ci_failed", "pr_url": pr_url}


def _open_project_pr(
    *,
    task_id: str,
    task: Dict[str, Any],
    plan: Dict[str, Any],
    branch: str,
    applied: List[str],
    draft: bool,
    note: str,
) -> Dict[str, Any]:
    """Open a PR summarizing the generated project."""
    title_text = plan.get("title") or task.get("description", "")[:60] or "new project"
    title = f"sandy: {title_text} ({task_id})"
    if draft:
        title += " [draft — needs review]"

    # The plan schema uses "features" (groups was the legacy name).
    features_block = ""
    for ft in plan.get("features", []):
        n_files = ft.get("estimated_files") or "?"
        features_block += f"\n**{ft.get('name', '?')}** (~{n_files} ملف):\n"
        desc = (ft.get("description") or "").strip()
        if desc:
            features_block += f"- {desc[:200]}\n"

    summary = (plan.get("summary") or "")[:600]
    stack = ", ".join(str(s) for s in (plan.get("stack") or [])[:6])

    commit_sha = task.get("last_commit_sha") or ""

    body_parts = [
        f"### SA8 Project Builder — task `{task_id}`",
        "",
        note,
        "",
        f"**الوصف:** {task.get('description', '')[:400]}",
        "",
        f"**خلاصة:** {summary}",
    ]
    if stack:
        body_parts.append(f"**Stack:** {stack}")
    body_parts.append(f"**branch:** `{branch}`")
    if commit_sha:
        body_parts.append(f"**last commit:** `{commit_sha[:7]}`")
    body_parts.append(f"**عدد الملفات المبنية:** {len(applied)}")
    if features_block:
        body_parts.append("\n#### الميزات")
        body_parts.append(features_block)
    if applied:
        files_list = "\n".join(f"- `{p}`" for p in applied[:30])
        if len(applied) > 30:
            files_list += f"\n... و{len(applied) - 30} غيرها"
        body_parts.append("\n#### الملفات")
        body_parts.append(files_list)

    # M10: surface any self-review findings the correction round couldn't fix
    raw_review = task.get("self_review_issues") or ""
    if raw_review:
        try:
            review_issues = json.loads(raw_review)
        except (TypeError, ValueError):
            review_issues = []
        if review_issues:
            body_parts.append("\n#### ⚠️ ملاحظات المراجعة الذاتية (تحتاج عين بشرية)")
            body_parts.append(
                self_review.format_issues_for_owner(review_issues, limit=15)
            )

    pr = github_api.create_pull_request(
        head=branch,
        title=title,
        body="\n".join(body_parts),
        draft=draft,
        repo=task.get("repo") or None,
    )
    return pr


# Failure path that also reports the partially-applied files.
def _fail_partial(
    task_id: str,
    task: Dict[str, Any],
    *,
    reason: str,
    applied: List[str],
    stopped_at: str,
) -> Dict[str, Any]:
    """Same as _fail but includes the partial-applied file list in the notice."""
    logger.warning(
        "[SA8] task %s partial failure at %s — %d files applied: %s",
        task_id, stopped_at, len(applied), reason,
    )
    task_state.set_status(
        task_id,
        task_state.STATUS_FAILED,
        where_we_stopped=f"partial — stopped at {stopped_at}: {reason[:300]}",
    )
    notifier.notify_needs_human(
        task_id=task_id,
        reason=f"وقفت عند {stopped_at}: {reason}",
        branch=task.get("branch", ""),
        partial_files=applied or None,
    )
    return {
        "ok": False,
        "final_status": "failed_partial",
        "applied_files": applied,
        "error": reason,
    }


# External repo provisioning.
def _ensure_external_repo(task_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Provision the GitHub repo for the task (idempotent on resume).

    Resolution order:
    1. `task.repo` already set → reuse (retry/resume case).
    2. `repo_name` resolves to an existing repo under the authenticated user
       → reuse it (M9: edit-existing flow). Sets `repo_mode='existing'`.
    3. Otherwise → create a new private repo with auto_init=True so `main`
       exists for branching. Sets `repo_mode='new'`.
    """
    if task.get("repo"):
        logger.info(
            "[SA8] external task=%s already has repo=%s — skipping provision",
            task_id, task["repo"],
        )
        return {"ok": True, "repo": task["repo"], "repo_mode": task.get("repo_mode") or "existing"}

    repo_name = (task.get("repo_name") or "").strip()
    if not repo_name:
        return {"ok": False, "error": "external مفعّل بس repo_name مفقود"}

    # M9: check if a repo with this name already exists under the authenticated
    # user. If yes, reuse it instead of failing on duplicate-name create.
    auth = github_api.get_authenticated_login()
    if auth.get("ok") and auth.get("login"):
        candidate_full = f"{auth['login']}/{repo_name}"
        lookup = github_api.get_repo(candidate_full)
        if lookup.get("ok") and lookup.get("exists"):
            existing_full = lookup.get("full_name") or candidate_full
            existing_url = lookup.get("html_url") or ""
            sa_redis.task_hset(
                task_id,
                {
                    "repo": existing_full,
                    "repo_url": existing_url,
                    "repo_mode": "existing",
                    "last_active": task_state.now_iso(),
                },
            )
            notifier.notify_owner(
                f"📦 رح أبني على repo موجود: {existing_url} (task `{task_id}`)\n"
                f"هلق ببدأ PLAN..."
            )
            logger.info(
                "[SA8] task=%s reusing existing repo=%s", task_id, existing_full
            )
            return {
                "ok": True,
                "repo": existing_full,
                "repo_url": existing_url,
                "repo_mode": "existing",
            }

    description = (task.get("description") or "")[:300]
    api = github_api.create_repo(
        name=repo_name,
        description=description,
        private=True,
        auto_init=True,
    )
    if not api.get("ok"):
        return {
            "ok": False,
            "error": f"create_repo فشل: {api.get('error') or api.get('status')}",
        }

    new_repo = api.get("full_name") or ""
    new_url = api.get("html_url") or ""
    if not new_repo:
        return {"ok": False, "error": "GitHub رد بدون full_name للـ repo الجديد"}

    sa_redis.task_hset(
        task_id,
        {
            "repo": new_repo,
            "repo_url": new_url,
            "repo_mode": "new",
            "last_active": task_state.now_iso(),
        },
    )
    notifier.notify_owner(
        f"📦 أنشأت repo جديد: {new_url} (task `{task_id}`)\n"
        f"هلق ببدأ PLAN..."
    )
    logger.info("[SA8] task=%s created external repo=%s", task_id, new_repo)
    return {"ok": True, "repo": new_repo, "repo_url": new_url, "repo_mode": "new"}


# Shared failure helpers (mirrors orchestrator.py).
def _fail(task_id: str, task: Dict[str, Any], reason: str) -> Dict[str, Any]:
    logger.warning("[SA8] task %s failed: %s", task_id, reason)
    task_state.set_status(
        task_id,
        task_state.STATUS_FAILED,
        where_we_stopped=reason[:500],
    )
    notifier.notify_needs_human(
        task_id=task_id,
        reason=reason,
        branch=task.get("branch", ""),
        partial_files=task.get("patched_files") or None,
    )
    return {"ok": False, "final_status": "failed", "error": reason}


def _requeue_for_shutdown(
    task_id: str,
    task: Dict[str, Any],
    *,
    where: str,
) -> Dict[str, Any]:
    """Heroku is killing the dyno — push back to queue with checkpoint intact."""
    logger.info(
        "[SA8] task=%s shutdown during %s — re-enqueueing for next Worker boot",
        task_id, where,
    )
    try:
        sa_redis.queue_push({
            "task_id": task_id,
            "type": task.get("type"),
            "enqueued_at": task_state.now_iso(),
            "resume_from": where,
        })
    except Exception as exc:
        logger.exception("[SA8] re-enqueue failed for task=%s: %s", task_id, exc)
    return {"ok": False, "final_status": "shutdown_requeued"}
