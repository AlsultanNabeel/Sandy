"""Urgent and overdue task hints for the system prompt. No auto-actions."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.time import USER_TZ

_LTM_COLLECTION = "sandy_memories"
_GOALS_COLLECTION = "sandy_goals"
_PROACTIVE_LABELS = {
    "habit",
    "habits",
    "work_pattern",
    "work_patterns",
    "goal",
    "goals",
}
_PATTERN_FIELDS = (
    "pattern",
    "text",
    "content",
    "description",
    "habit",
    "work_pattern",
    "goal",
)


def _parse_task_due(task: Dict[str, Any]) -> Optional[datetime]:
    """Best-effort due datetime in USER_TZ."""
    due_at = (task.get("due_at") or "").strip()
    due = (task.get("due") or "").strip()
    raw = due_at or due
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=USER_TZ)
        else:
            dt = dt.astimezone(USER_TZ)
        return dt
    except Exception:
        return None


def _normalize_pattern_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _extract_pattern_text(doc: Dict[str, Any]) -> Optional[str]:
    for field in _PATTERN_FIELDS:
        raw = doc.get(field)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _collect_repeated_patterns(
    docs: Iterable[Dict[str, Any]],
    *,
    min_count: int,
) -> List[Tuple[str, int]]:
    counts: Counter[str] = Counter()
    display: Dict[str, str] = {}

    for doc in docs:
        text = _extract_pattern_text(doc)
        if not text:
            continue
        key = _normalize_pattern_text(text)
        if not key:
            continue
        counts[key] += 1
        display.setdefault(key, text)

    repeated = [
        (display[key], count)
        for key, count in counts.items()
        if count >= min_count
    ]
    repeated.sort(key=lambda item: (-item[1], item[0]))
    return repeated


def build_urgent_tasks_hint_for_prompt(
    mongo_db=None,
    tasks_file=None,
    *,
    horizon_hours: int = 24,
) -> Optional[str]:
    """One system-prompt line if any active task is overdue or due within
    horizon_hours, else None."""
    try:
        from app.features.tasks_store import load_tasks
    except Exception:
        return None

    try:
        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    except Exception:
        return None

    active = [t for t in tasks if not t.get("done", False)]
    if not active:
        return None

    now = datetime.now(USER_TZ)
    cutoff = now + timedelta(hours=horizon_hours)
    urgent: List[str] = []

    for t in active:
        dt = _parse_task_due(t)
        text = (t.get("text") or "").strip() or "مهمة"
        if dt is None:
            continue
        if dt < now:
            urgent.append(
                f"'{text[:60]}' متأخرة (كان موعدها {dt.strftime('%d/%m %H:%M')})."
            )
        elif dt <= cutoff:
            urgent.append(
                f"'{text[:60]}' مستحقة خلال {horizon_hours}س ({dt.strftime('%d/%m %H:%M')})."
            )

    if not urgent:
        return None

    # بروتوكول الصمت: لو في اجتماع أو ضمن quiet hours، ما نزعجه
    try:
        from app.agent.silence_protocol import should_stay_silent
        if should_stay_silent():
            return None
    except Exception:
        pass

    # Cap noise for the model
    lines = urgent[:3]
    extra = len(urgent) - len(lines)
    tail = f" (+{extra} أخرى)" if extra > 0 else ""
    return "مهام تستحق الانتباه فوراً: " + " ".join(lines) + tail


def build_proactive_need_hint(
    chat_id: str,
    user_id: str,
    mongo_db=None,
    *,
    min_count: int = 3,
) -> Optional[str]:
    """يرجّع احتياج متوقّع بس لو مدعوم بتكرار موثّق في LTM."""
    if mongo_db is None:
        return None

    candidates: List[Tuple[str, int, str]] = []

    try:
        docs = list(mongo_db[_LTM_COLLECTION].find(
            {
                "chat_id": str(chat_id),
                "label": {"$in": sorted(_PROACTIVE_LABELS)},
            },
            {"_id": 0, **{f: 1 for f in _PATTERN_FIELDS}, "label": 1},
            sort=[("created_at", -1)],
            limit=200,
        ))
        for item, count in _collect_repeated_patterns(docs, min_count=min_count):
            candidates.append((item, count, "ltm"))
    except Exception:
        pass

    try:
        goal_docs = list(mongo_db[_GOALS_COLLECTION].find(
            {"chat_id": str(chat_id)},
            {"_id": 0, "text": 1, "deadline": 1, "status": 1},
            sort=[("updated_at", -1)],
            limit=200,
        ))
        goal_counter: Counter[str] = Counter()
        goal_display: Dict[str, str] = {}
        for doc in goal_docs:
            text = str(doc.get("text") or "").strip()
            if not text:
                continue
            key = _normalize_pattern_text(text)
            if not key:
                continue
            goal_counter[key] += 1
            goal_display.setdefault(key, text)
        for key, count in goal_counter.items():
            if count >= min_count:
                candidates.append((goal_display[key], count, "goal"))
    except Exception:
        pass

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[1], item[0]))
    text, count, source = candidates[0]
    if source == "goal":
        return f"[هدف متكرر: {text} — ظهر {count} مرات]"
    return f"[نمط متكرر: {text} — ظهر {count} مرات]"
