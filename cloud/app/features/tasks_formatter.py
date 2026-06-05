"""Task display formatting — converts task dicts to human-readable Arabic strings.

Public API:
  build_task_display(mongo_db, tasks_file) -> (str, aliases_dict)
  build_completed_task_display(mongo_db, tasks_file) -> (str, aliases_dict)
  build_all_tasks_display(mongo_db, tasks_file) -> (str, active_aliases, completed_aliases)
  format_tasks_for_briefing(tasks) -> str
"""

from datetime import datetime
from typing import Any, Dict, List

from app.utils.time import USER_TZ


def _format_due_ar(due: str) -> str:
    if not due:
        return ""
    try:
        return datetime.fromisoformat(due[:10]).strftime("%d/%m/%Y")
    except Exception:
        return due[:10] if len(due) >= 10 else due


def _task_ordinal_ar(index: int) -> str:
    names = [
        "الأولى",
        "الثانية",
        "الثالثة",
        "الرابعة",
        "الخامسة",
        "السادسة",
        "السابعة",
        "الثامنة",
        "التاسعة",
        "العاشرة",
    ]
    if 1 <= index <= len(names):
        return names[index - 1]
    return str(index)


def _format_task_due_text(task: Dict[str, Any]) -> str:
    due_at = (task.get("due_at") or "").strip()
    due_raw = (task.get("due") or "").strip()

    if due_at:
        try:
            due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)
            return due_dt.strftime("%d/%m/%Y %I:%M %p")
        except Exception:
            return due_at

    if due_raw:
        try:
            return datetime.fromisoformat(due_raw[:10]).strftime("%d/%m/%Y")
        except Exception:
            return due_raw[:10] if len(due_raw) >= 10 else due_raw

    return "بدون موعد"


def format_tasks_for_briefing(
    tasks: List[Dict[str, Any]], *, max_lines: int = 28
) -> str:
    """Describe all active tasks for the morning briefing."""
    active_tasks = [t for t in (tasks or []) if not t.get("done", False)]
    if not active_tasks:
        return "لا توجد مهام نشطة على Google Tasks حالياً."

    lines: List[str] = []
    for i, task in enumerate(active_tasks[:max_lines], 1):
        text = (task.get("text") or "").strip()
        ordinal = _task_ordinal_ar(i)

        due_text = _format_task_due_text(task)

        lines.append(f"المهمة {ordinal}: {text or '(بدون نص)'} — الموعد: {due_text}")

    overflow = len(active_tasks) - max_lines
    if overflow > 0:
        lines.append(
            f"... و{overflow} مهمة نشطة أخرى (تحقّق من التطبيق للقائمة الكاملة)."
        )
    return "\n".join(lines)


def build_task_display(mongo_db=None, tasks_file=None):
    from app.features.google_tasks import load_tasks  # lazy import to avoid a circular import

    tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    active_tasks = [t for t in tasks if not t.get("done", False)]

    if not active_tasks:
        return "لا توجد مهام حالياً.", {}

    lines = []
    aliases = {}

    for i, task in enumerate(active_tasks, 1):
        alias = f"T{i}"
        text = (task.get("text") or "").strip()

        aliases[alias] = {"id": task.get("id", ""), "text": text}

        due_text = _format_task_due_text(task)
        lines.append(f"{i}. {text or '(بدون نص)'} — الموعد: {due_text}")

    return "\n".join(lines), aliases


def build_completed_task_display(mongo_db=None, tasks_file=None):
    from app.features.google_tasks import (
        load_completed_tasks,
    )  # lazy import to avoid a circular import

    completed_tasks = load_completed_tasks(mongo_db=mongo_db, tasks_file=tasks_file)

    if not completed_tasks:
        return "لا توجد مهام مكتملة حالياً.", {}

    lines = ["المهام المكتملة:"]
    aliases = {}

    for i, task in enumerate(completed_tasks, 1):
        alias = f"CT{i}"
        text = (task.get("text") or "").strip()
        completed_at = (task.get("completed_at") or "").strip()

        aliases[alias] = {"id": task.get("id", ""), "text": text}

        completed_text = ""
        if completed_at:
            try:
                completed_dt = datetime.fromisoformat(
                    completed_at.replace("Z", "+00:00")
                )
                if completed_dt.tzinfo is None:
                    completed_dt = completed_dt.replace(tzinfo=USER_TZ)
                else:
                    completed_dt = completed_dt.astimezone(USER_TZ)
                completed_text = completed_dt.strftime("%d/%m/%Y || %I:%M %p")
            except Exception:
                completed_text = completed_at

        ordinal = _task_ordinal_ar(i)
        if completed_text:
            lines.append(f"✅ المهمة {ordinal}: {text}")
            lines.append(f"   اكتملت: {completed_text}")
        else:
            lines.append(f"✅ المهمة {ordinal}: {text}")

    return "\n".join(lines), aliases


def build_all_tasks_display(mongo_db=None, tasks_file=None):
    active_text, active_aliases = build_task_display(
        mongo_db=mongo_db, tasks_file=tasks_file
    )
    completed_text, completed_aliases = build_completed_task_display(
        mongo_db=mongo_db, tasks_file=tasks_file
    )

    lines = ["المهام النشطة:"]

    if active_text.strip() == "لا توجد مهام حالياً.":
        lines.append("لا توجد مهام نشطة حالياً.")
    else:
        for line in active_text.splitlines():
            lines.append(f"🔲 {line}")

    lines.append("")
    lines.append("المهام المكتملة:")

    if completed_text.strip() == "لا توجد مهام مكتملة حالياً.":
        lines.append("لا توجد مهام مكتملة حالياً.")
    else:
        for line in completed_text.splitlines():
            if line.strip() == "المهام المكتملة:":
                continue
            lines.append(line)

    return "\n".join(lines), active_aliases, completed_aliases
