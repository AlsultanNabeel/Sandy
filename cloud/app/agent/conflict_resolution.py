from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
import os
import uuid

from app.utils.arabic_days import WEEKDAY_TO_AR_NAME
from app.utils.time import USER_TZ

# Active day window for free-slot search (overridable per user/deployment).
_DAY_START_HOUR = int(os.getenv("SANDY_DAY_START_HOUR", "8"))
_DAY_END_HOUR = int(os.getenv("SANDY_DAY_END_HOUR", "22"))

MONITORED_CATEGORIES = {"study", "deadline", "volunteer", "meeting"}

_CATEGORY_KEYWORDS = {
    "study": (
        "امتحان",
        "دراسة",
        "مذاكرة",
        "محاضرة",
        "اختبار",
        "study",
        "exam",
        "lecture",
    ),
    "deadline": (
        "تسليم",
        "deadline",
        "submission",
        "due",
        "موعد تسليم",
    ),
    "volunteer": (
        "تطوع",
        "تطوعي",
        "تطوعية",
        "volunteer",
        "volunteering",
        "جمعية",
    ),
    "meeting": (
        "اجتماع",
        "meeting",
        "call",
        "مقابلة",
        "جلسة",
    ),
}


def _parse_iso(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
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


def _detect_category(text: str, *, default: str = "") -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return default

    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(word in normalized for word in keywords):
            return category
    return default


def _normalize_task(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    due_at = _parse_iso(str(task.get("due_at", "") or ""))
    due_raw = str(task.get("due", "") or "")
    due_date = _parse_iso(due_raw) if not due_at else None

    if due_at is None and due_date is None:
        return None

    title = str(task.get("text", "") or "").strip()
    notes = str(task.get("notes", "") or "").strip()
    full_text = f"{title} {notes}".strip()
    category = _detect_category(full_text, default="deadline")

    start = due_at or due_date
    if start is None:
        return None
    end = start + timedelta(hours=1)

    return {
        "id": str(task.get("id", "") or ""),
        "title": title or "مهمة",
        "category": category,
        "start": start,
        "end": end,
        "source": "task",
    }


def _normalize_calendar_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    start_data = (event.get("start", {}) or {}).get("dateTime") or (
        event.get("start", {}) or {}
    ).get("date")
    end_data = (event.get("end", {}) or {}).get("dateTime") or (
        event.get("end", {}) or {}
    ).get("date")

    start = _parse_iso(str(start_data or ""))
    if start is None:
        return None

    end = _parse_iso(str(end_data or "")) or (start + timedelta(hours=1))
    summary = str(event.get("summary", "") or "").strip()
    description = str(event.get("description", "") or "").strip()
    full_text = f"{summary} {description}".strip()

    return {
        "id": str(event.get("id", "") or ""),
        "title": summary or "موعد",
        "category": _detect_category(full_text, default="meeting"),
        "start": start,
        "end": end,
        "source": "calendar",
    }


def _overlaps(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def _is_conflicting_pair(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if (
        a.get("category") not in MONITORED_CATEGORIES
        or b.get("category") not in MONITORED_CATEGORIES
    ):
        return False

    if _overlaps(a["start"], a["end"], b["start"], b["end"]):
        return True

    if a.get("category") == b.get("category"):
        return False

    pair = {a.get("category"), b.get("category")}
    if "study" in pair and ("meeting" in pair or "volunteer" in pair):
        return True
    if "deadline" in pair and ("meeting" in pair or "volunteer" in pair):
        return True
    return False


def _merge_intervals(
    intervals: List[Tuple[datetime, datetime]],
) -> List[Tuple[datetime, datetime]]:
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged: List[Tuple[datetime, datetime]] = [sorted_intervals[0]]

    for current_start, current_end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if current_start <= last_end:
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            merged.append((current_start, current_end))
    return merged


def _find_free_slots(
    day_entries: List[Dict[str, Any]],
    *,
    target_day,
    duration_minutes: int = 60,
    max_slots: int = 3,
) -> List[Tuple[datetime, datetime]]:
    day_start = datetime.combine(target_day, time(hour=_DAY_START_HOUR, minute=0), tzinfo=USER_TZ)
    day_end = datetime.combine(target_day, time(hour=_DAY_END_HOUR, minute=0), tzinfo=USER_TZ)
    needed = timedelta(minutes=max(30, int(duration_minutes or 60)))

    busy: List[Tuple[datetime, datetime]] = []
    for entry in day_entries:
        start = entry["start"]
        end = entry["end"]
        if end <= day_start or start >= day_end:
            continue
        busy.append((max(start, day_start), min(end, day_end)))

    merged = _merge_intervals(busy)

    free: List[Tuple[datetime, datetime]] = []
    cursor = day_start
    for b_start, b_end in merged:
        if b_start - cursor >= needed:
            free.append((cursor, cursor + needed))
            if len(free) >= max_slots:
                return free
        cursor = max(cursor, b_end)

    if day_end - cursor >= needed and len(free) < max_slots:
        free.append((cursor, cursor + needed))
    return free[:max_slots]


def _format_alert(
    *,
    owner_name: str,
    day_name: str,
    new_title: str,
    conflict_title: str,
    free_slots: List[Tuple[datetime, datetime]],
) -> str:
    base = (
        f"{owner_name}، عندك {conflict_title} {day_name} "
        f"ولقيت {new_title} نفس اليوم — بعدّل؟"
    )
    if not free_slots:
        return base

    lines = [base, "", "اقتراحات من الأوقات الفاضية:"]
    for start, end in free_slots:
        lines.append(f"• {start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}")
    return "\n".join(lines)


def check_conflicts(
    new_item: Dict[str, Any],
    *,
    tasks: List[Dict[str, Any]],
    calendar_events: List[Dict[str, Any]],
    exclude_task_id: str = "",
    exclude_event_id: str = "",
    owner_name: str = "نبيل",
) -> Dict[str, Any]:
    start = _parse_iso(str(new_item.get("start_iso", "") or ""))
    if start is None:
        return {
            "has_conflict": False,
            "alert_text": "",
            "conflicts": [],
            "suggestions": [],
        }

    end = _parse_iso(str(new_item.get("end_iso", "") or "")) or (
        start + timedelta(hours=1)
    )
    text_for_category = " ".join(
        [
            str(new_item.get("title", "") or ""),
            str(new_item.get("description", "") or ""),
            str(new_item.get("notes", "") or ""),
        ]
    ).strip()
    source = str(new_item.get("source", "") or "").strip().lower()
    default_category = "deadline" if source == "task" else "meeting"

    new_entry = {
        "id": str(new_item.get("id", "") or ""),
        "title": str(new_item.get("title", "") or "").strip()
        or ("مهمة" if source == "task" else "موعد"),
        "category": _detect_category(text_for_category, default=default_category),
        "start": start,
        "end": end,
        "source": source or "calendar",
    }

    existing: List[Dict[str, Any]] = []
    for task in tasks or []:
        if exclude_task_id and str(task.get("id", "") or "") == exclude_task_id:
            continue
        norm_task = _normalize_task(task)
        if norm_task:
            existing.append(norm_task)

    for event in calendar_events or []:
        if exclude_event_id and str(event.get("id", "") or "") == exclude_event_id:
            continue
        norm_event = _normalize_calendar_event(event)
        if norm_event:
            existing.append(norm_event)

    same_day = [e for e in existing if e["start"].date() == new_entry["start"].date()]

    conflicts = [entry for entry in same_day if _is_conflicting_pair(new_entry, entry)]
    if not conflicts:
        return {
            "has_conflict": False,
            "alert_text": "",
            "conflicts": [],
            "suggestions": [],
        }

    conflicts.sort(key=lambda item: item["start"])
    primary = conflicts[0]
    suggestions = _find_free_slots(same_day, target_day=new_entry["start"].date())
    day_name = WEEKDAY_TO_AR_NAME[new_entry["start"].weekday()]

    alert = _format_alert(
        owner_name=owner_name,
        day_name=day_name,
        new_title=new_entry["title"],
        conflict_title=primary["title"],
        free_slots=suggestions,
    )
    return {
        "has_conflict": True,
        "alert_text": alert,
        "conflicts": conflicts,
        "suggestions": suggestions,
    }


def _day_window(target: datetime) -> Tuple[str, str]:
    start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end = target.replace(hour=23, minute=59, second=59, microsecond=0)
    return start.isoformat(), end.isoformat()


def run_conflict_check_after_task_add(
    *,
    task_id: str,
    task_text: str,
    due_iso: str,
    notes: str = "",
    mongo_db=None,
    tasks_file=None,
) -> Dict[str, Any]:
    due_dt = _parse_iso(due_iso)
    if due_dt is None:
        return {"has_conflict": False, "alert_text": "", "suggestions": []}

    try:
        from app.features.tasks_store import load_tasks

        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        # Calendar events are gone (no external calendar) — conflicts are
        # task-vs-task on the same day now.
        result = check_conflicts(
            {
                "id": task_id,
                "source": "task",
                "title": task_text,
                "notes": notes,
                "start_iso": due_dt.isoformat(),
            },
            tasks=tasks,
            calendar_events=[],
            exclude_task_id=task_id,
        )
        suggestions = [
            {"start_iso": s[0].isoformat(), "end_iso": s[1].isoformat()}
            for s in (result.get("suggestions") or [])
        ]
        return {
            "has_conflict": bool(result.get("has_conflict")),
            "alert_text": str(result.get("alert_text", "") or ""),
            "suggestions": suggestions,
        }
    except Exception as exc:
        print(f"[ConflictResolution] task conflict check failed: {exc}")
        return {"has_conflict": False, "alert_text": "", "suggestions": []}
