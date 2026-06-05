"""Task reference resolution — maps user-facing labels (T1, ordinals, fuzzy text) to task IDs.

Public API:
  resolve_task_reference_for_write(ref, ...) -> dict
  resolve_task_references_for_write(refs, ...) -> dict
  resolve_completed_task_reference_for_write(ref, ...) -> dict
  resolve_completed_task_references_for_write(refs, ...) -> dict
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List

# Text normalisation helpers


def _task_match_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    text = re.sub(r"[ًٌٍَُِّْـ]", "", text)
    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
        .replace("ى", "ي")
    )
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[^\w؀-ۿ]+", " ", text)

    tokens = []
    for token in text.split():
        if token in {"مهمه", "المهمه", "تاسك", "task"}:
            continue
        if token.startswith("ال") and len(token) > 3:
            token = token[2:]
        if token:
            tokens.append(token)

    return " ".join(tokens).strip()


def _task_match_score(a: str, b: str) -> float:
    return SequenceMatcher(None, _task_match_key(a), _task_match_key(b)).ratio()


# Ordinal maps

_ORDINAL_MAP = {
    "الأولى": 1,
    "الاولى": 1,
    "اولى": 1,
    "الأول": 1,
    "الاول": 1,
    "اول": 1,
    "الثانية": 2,
    "التانية": 2,
    "ثانية": 2,
    "تانية": 2,
    "الثالثة": 3,
    "التالتة": 3,
    "ثالثة": 3,
    "تالتة": 3,
    "الرابعة": 4,
    "رابعة": 4,
    "الخامسة": 5,
    "خامسة": 5,
}

_ENGLISH_ORDINAL_MAP = {
    "ONE": "1",
    "FIRST": "1",
    "TWO": "2",
    "SECOND": "2",
    "THREE": "3",
    "THIRD": "3",
    "FOUR": "4",
    "FOURTH": "4",
    "FIVE": "5",
    "FIFTH": "5",
}


# Active task resolver


def resolve_task_reference_for_write(
    task_reference: str,
    mongo_db=None,
    tasks_file=None,
    aliases=None,
) -> Dict[str, Any]:
    from app.features.google_tasks import load_tasks  # lazy import to avoid a circular import

    tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    active = [t for t in tasks if not t.get("done", False)]

    if not active:
        return {"status": "empty", "task": None, "matches": []}

    ref = (task_reference or "").strip()
    if not ref:
        return {"status": "missing", "task": None, "matches": []}

    ref_upper = ref.upper()

    if ref_upper.startswith("ID:"):
        wanted_id = ref[3:].strip()
        if wanted_id:
            for task in active:
                if str(task.get("id", "")).strip() == wanted_id:
                    return {"status": "matched", "task": task, "matches": [task]}

    if ref in _ORDINAL_MAP:
        index = _ORDINAL_MAP[ref]
        if 1 <= index <= len(active):
            return {
                "status": "matched",
                "task": active[index - 1],
                "matches": [active[index - 1]],
            }

    if isinstance(aliases, dict) and ref_upper in aliases:
        alias_id = aliases[ref_upper].get("id", "")
        for task in active:
            if task.get("id") == alias_id:
                return {"status": "matched", "task": task, "matches": [task]}

    if ref_upper.startswith("T") and ref_upper[1:].isdigit():
        index = int(ref_upper[1:])
        if 1 <= index <= len(active):
            return {
                "status": "matched",
                "task": active[index - 1],
                "matches": [active[index - 1]],
            }

    if ref.isdigit():
        index = int(ref)
        if 1 <= index <= len(active):
            return {
                "status": "matched",
                "task": active[index - 1],
                "matches": [active[index - 1]],
            }

    ref_norm = _task_match_key(ref)

    exact_matches = [
        t for t in active if _task_match_key(t.get("text", "")) == ref_norm
    ]
    if len(exact_matches) == 1:
        return {"status": "matched", "task": exact_matches[0], "matches": exact_matches}
    if len(exact_matches) > 1:
        return {"status": "ambiguous", "task": None, "matches": exact_matches}

    partial_matches = [
        t for t in active if ref_norm in _task_match_key(t.get("text", ""))
    ]
    if len(partial_matches) == 1:
        return {
            "status": "matched",
            "task": partial_matches[0],
            "matches": partial_matches,
        }
    if len(partial_matches) > 1:
        return {"status": "ambiguous", "task": None, "matches": partial_matches}

    fuzzy_matches = [
        t for t in active if _task_match_score(ref_norm, t.get("text", "")) >= 0.72
    ]
    if len(fuzzy_matches) == 1:
        return {"status": "matched", "task": fuzzy_matches[0], "matches": fuzzy_matches}
    if len(fuzzy_matches) > 1:
        return {"status": "ambiguous", "task": None, "matches": fuzzy_matches}

    return {"status": "not_found", "task": None, "matches": []}


def resolve_task_references_for_write(
    task_references,
    mongo_db=None,
    tasks_file=None,
    aliases=None,
) -> Dict[str, Any]:
    raw_reference = (
        " ".join(task_references)
        if isinstance(task_references, list)
        else str(task_references or "")
    )

    token_pattern = (
        r"T\d+|\d+|الأولى|الاولى|اولى|الأول|الاول|اول|"  # nosec B105
        r"الثانية|التانية|ثانية|تانية|الثالثة|التالتة|ثالثة|تالتة|"
        r"الرابعة|رابعة|الخامسة|خامسة"
    )
    refs = re.findall(token_pattern, raw_reference, flags=re.IGNORECASE)

    if not refs:
        refs = [
            part.strip()
            for part in re.split(r"\s*(?:،|,|/|وو| و | and )\s*", raw_reference)
            if part.strip()
        ]

    if not refs:
        return {"status": "missing", "tasks": [], "matches": []}

    matched_tasks: List[Dict] = []
    seen_ids: set = set()
    missing_refs: List[str] = []

    for ref in refs:
        result = resolve_task_reference_for_write(
            ref, mongo_db=mongo_db, tasks_file=tasks_file, aliases=aliases
        )
        status = result.get("status")

        if status == "ambiguous":
            return {
                "status": "ambiguous",
                "reference": ref,
                "tasks": [],
                "matches": result.get("matches", []),
            }

        if status != "matched":
            missing_refs.append(ref)
            continue

        task = result.get("task") or {}
        task_id = task.get("id", "")
        if task_id and task_id not in seen_ids:
            matched_tasks.append(task)
            seen_ids.add(task_id)

    if matched_tasks and missing_refs:
        return {
            "status": "partial",
            "tasks": matched_tasks,
            "missing_references": missing_refs,
            "matches": matched_tasks,
        }
    if missing_refs and not matched_tasks:
        return {
            "status": "not_found",
            "reference": "، ".join(missing_refs),
            "tasks": [],
            "matches": [],
        }
    if len(matched_tasks) < 2:
        return {"status": "single", "tasks": matched_tasks, "matches": matched_tasks}
    return {"status": "matched", "tasks": matched_tasks, "matches": matched_tasks}


# Completed task resolver


def resolve_completed_task_reference_for_write(
    task_reference: str,
    mongo_db=None,
    tasks_file=None,
    aliases=None,
) -> Dict[str, Any]:
    from app.features.google_tasks import (
        load_completed_tasks,
    )  # lazy import to avoid a circular import

    completed = load_completed_tasks(mongo_db=mongo_db, tasks_file=tasks_file)

    if not completed:
        return {"status": "empty", "task": None, "matches": []}

    ref = (task_reference or "").strip()
    if not ref:
        return {"status": "missing", "task": None, "matches": []}

    ref = ref.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    ref_upper = ref.upper()
    ref = _ENGLISH_ORDINAL_MAP.get(ref_upper, ref)
    ref_upper = ref.upper()

    if ref_upper.startswith("ID:"):
        wanted_id = ref[3:].strip()
        if wanted_id:
            for task in completed:
                if str(task.get("id", "")).strip() == wanted_id:
                    return {"status": "matched", "task": task, "matches": [task]}

    if isinstance(aliases, dict) and ref_upper in aliases:
        alias_id = aliases[ref_upper].get("id", "")
        for task in completed:
            if task.get("id") == alias_id:
                return {"status": "matched", "task": task, "matches": [task]}

    if ref_upper.startswith("CT") and ref_upper[2:].isdigit():
        index = int(ref_upper[2:])
        if 1 <= index <= len(completed):
            return {
                "status": "matched",
                "task": completed[index - 1],
                "matches": [completed[index - 1]],
            }

    if ref in _ORDINAL_MAP:
        index = _ORDINAL_MAP[ref]
        if 1 <= index <= len(completed):
            return {
                "status": "matched",
                "task": completed[index - 1],
                "matches": [completed[index - 1]],
            }

    if ref.isdigit():
        index = int(ref)
        if 1 <= index <= len(completed):
            return {
                "status": "matched",
                "task": completed[index - 1],
                "matches": [completed[index - 1]],
            }

    ref_norm = _task_match_key(ref)

    exact_matches = [
        t for t in completed if _task_match_key(t.get("text", "")) == ref_norm
    ]
    if len(exact_matches) == 1:
        return {"status": "matched", "task": exact_matches[0], "matches": exact_matches}
    if len(exact_matches) > 1:
        return {"status": "ambiguous", "task": None, "matches": exact_matches}

    partial_matches = [
        t for t in completed if ref_norm in _task_match_key(t.get("text", ""))
    ]
    if len(partial_matches) == 1:
        return {
            "status": "matched",
            "task": partial_matches[0],
            "matches": partial_matches,
        }
    if len(partial_matches) > 1:
        return {"status": "ambiguous", "task": None, "matches": partial_matches}

    return {"status": "not_found", "task": None, "matches": []}


def resolve_completed_task_references_for_write(
    task_references,
    mongo_db=None,
    tasks_file=None,
    aliases=None,
) -> Dict[str, Any]:
    raw_reference = (
        " ".join(task_references)
        if isinstance(task_references, list)
        else str(task_references or "")
    )

    token_pattern = (
        r"CT[0-9٠-٩۰-۹]+|[0-9٠-٩۰-۹]+|"  # nosec B105
        r"one|two|three|four|five|"
        r"first|second|third|fourth|fifth|"
        r"الأولى|الاولى|اولى|الأول|الاول|اول|"
        r"الثانية|التانية|ثانية|تانية|"
        r"الثالثة|التالتة|ثالثة|تالتة|"
        r"الرابعة|رابعة|الخامسة|خامسة"
    )
    refs = re.findall(token_pattern, raw_reference, flags=re.IGNORECASE)

    if not refs:
        refs = [
            part.strip()
            for part in re.split(r"\s*(?:،|,|/|وو| و | and )\s*", raw_reference)
            if part.strip()
        ]

    if not refs:
        return {"status": "missing", "tasks": [], "matches": []}

    matched_tasks: List[Dict] = []
    seen_ids: set = set()
    missing_refs: List[str] = []

    for ref in refs:
        result = resolve_completed_task_reference_for_write(
            ref, mongo_db=mongo_db, tasks_file=tasks_file, aliases=aliases
        )
        status = result.get("status")

        if status == "ambiguous":
            return {
                "status": "ambiguous",
                "reference": ref,
                "tasks": [],
                "matches": result.get("matches", []),
            }

        if status != "matched":
            missing_refs.append(ref)
            continue

        task = result.get("task") or {}
        task_id = task.get("id", "")
        if task_id and task_id not in seen_ids:
            matched_tasks.append(task)
            seen_ids.add(task_id)

    if matched_tasks and missing_refs:
        return {
            "status": "partial",
            "tasks": matched_tasks,
            "missing_references": missing_refs,
            "matches": matched_tasks,
        }
    if missing_refs and not matched_tasks:
        return {
            "status": "not_found",
            "reference": "، ".join(missing_refs),
            "tasks": [],
            "matches": [],
        }
    if len(matched_tasks) < 2:
        return {"status": "single", "tasks": matched_tasks, "matches": matched_tasks}
    return {"status": "matched", "tasks": matched_tasks, "matches": matched_tasks}
