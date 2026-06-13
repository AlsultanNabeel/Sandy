"""Native task store — MongoDB, no external provider.

Drop-in replacement for the old Google Tasks feature module: every function
keeps the exact signature and return contract of its google_tasks twin, so the
executor handlers, formatters, matchers and the web API work unchanged. The
storage quirks Google forced on us are gone though — the exact due time lives
in a real field instead of a [SANDY_DUE_AT:...] marker buried in the notes.

Collection: sandy_tasks
  {_id, text, notes, done, created_at, completed_at,
   due_date ("YYYY-MM-DD" or ""), due_at (datetime or None — exact time),
   priority ("" | "high" | "normal" | "low"), project ("")}

Normalized dict (what every consumer sees — same shape google_tasks emitted):
  {id, text, done, created_at, completed_at, due, notes, due_at,
   priority, project, raw}

Wired at boot via init_tasks_store(mongo_db) — same pattern as brainstorm.
Without Mongo every function fails soft (empty list / "" / False), same as the
old module did on network errors.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils.time import USER_TZ
from app.utils.user_profiles import active_profile_allows_privileged_access

_COLL = "sandy_tasks"
_mongo_db = None


def init_tasks_store(mongo_db) -> None:
    """يُستدعى مرّة عند الإقلاع."""
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index([("done", 1), ("created_at", 1)], background=True)
        mongo_db[_COLL].create_index([("done", 1), ("completed_at", -1)], background=True)
        print("[TasksStore] ready")
    except Exception as e:  # noqa: BLE001
        print(f"[TasksStore] index skipped: {e}")


def is_available() -> bool:
    return _mongo_db is not None


def _db(mongo_db=None):
    return mongo_db if mongo_db is not None else _mongo_db


def _coll(mongo_db=None):
    db = _db(mongo_db)
    return db[_COLL] if db is not None else None


def _require_owner() -> None:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")


def _iso(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(USER_TZ).isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    """ISO string → aware datetime in USER_TZ. None on garbage."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=USER_TZ) if dt.tzinfo is None else dt.astimezone(USER_TZ)
    except Exception:
        return None


def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo doc → the exact dict shape google_tasks used to emit."""
    due_date = doc.get("due_date") or ""
    return {
        "id": doc.get("_id", ""),
        "text": doc.get("text", "") or "",
        "done": bool(doc.get("done")),
        "created_at": _iso(doc.get("created_at")),
        "completed_at": _iso(doc.get("completed_at")) or None,
        # Google stored the due DATE here in this exact shape; keep it.
        "due": f"{due_date}T00:00:00.000Z" if due_date else None,
        "notes": doc.get("notes", "") or "",
        "due_at": _iso(doc.get("due_at")),
        "priority": doc.get("priority", "") or "",
        "project": doc.get("project", "") or "",
        "raw": {k: v for k, v in doc.items() if k != "_id"},
    }


# ─── Reads ────────────────────────────────────────────────────────────────────

def load_tasks(mongo_db=None, tasks_file=None) -> List[Dict[str, Any]]:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return []
        docs = list(coll.find({"done": False}).sort("created_at", 1))
        return [_normalize(d) for d in docs]
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] load failed: {e}")
        return []


def load_completed_tasks(mongo_db=None, tasks_file=None) -> List[Dict[str, Any]]:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return []
        docs = list(coll.find({"done": True}).sort("completed_at", -1).limit(100))
        return [_normalize(d) for d in docs]
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] load completed failed: {e}")
        return []


def load_overdue_tasks(mongo_db=None, tasks_file=None) -> List[Dict[str, Any]]:
    """Active tasks whose due moment has passed (exact time, else end of due day)."""
    try:
        now = datetime.now(USER_TZ)
        today = now.date().isoformat()
        overdue: List[Dict[str, Any]] = []
        for task in load_tasks(mongo_db=mongo_db, tasks_file=tasks_file):
            due_at = _parse_iso(task.get("due_at") or "")
            if due_at:
                if due_at < now:
                    overdue.append(task)
                continue
            due_date = (task.get("due") or "")[:10]
            if due_date and due_date < today:
                overdue.append(task)
        return overdue
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] load overdue failed: {e}")
        return []


# ─── Writes ───────────────────────────────────────────────────────────────────

def add_task(
    task_text: str,
    due_iso: str = "",
    notes: str = "",
    mongo_db=None,
    tasks_file=None,
    priority: str = "",
    project: str = "",
) -> str:
    """Returns the new task id, or "" on failure (incl. past due) — same as before."""
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return ""

        doc: Dict[str, Any] = {
            "_id": uuid.uuid4().hex,
            "text": str(task_text or "").strip(),
            "notes": str(notes or "").strip(),
            "done": False,
            "created_at": datetime.now(timezone.utc),
            "completed_at": None,
            "due_date": "",
            "due_at": None,
            "priority": str(priority or "").strip(),
            "project": str(project or "").strip(),
        }
        if not doc["text"]:
            return ""

        if due_iso:
            due_dt = _parse_iso(due_iso)
            if due_dt is None:
                return ""
            if due_dt <= datetime.now(USER_TZ):
                raise ValueError("Task due time is in the past")
            doc["due_date"] = due_dt.date().isoformat()
            doc["due_at"] = due_dt

        coll.insert_one(doc)
        print(f"[TasksStore] task created: {doc['text']}")
        return doc["_id"]
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] create failed: {e}")
        return ""


def complete_task(task_id: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None or not task_id:
            return False
        r = coll.update_one(
            {"_id": task_id},
            {"$set": {"done": True, "completed_at": datetime.now(timezone.utc)}},
        )
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] complete failed: {e}")
        return False


def uncomplete_task(task_id: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None or not task_id:
            return False
        r = coll.update_one(
            {"_id": task_id},
            {"$set": {"done": False, "completed_at": None}},
        )
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] uncomplete failed: {e}")
        return False


def update_task_due_date(
    task_id: str, due_iso: str, mongo_db=None, tasks_file=None
) -> dict:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        task_id = str(task_id or "").strip()
        due_iso = str(due_iso or "").strip()
        if coll is None or not task_id or not due_iso:
            return {"ok": False, "reason": "missing"}

        due_dt = _parse_iso(due_iso)
        if due_dt is None:
            return {"ok": False, "reason": "error"}
        if due_dt.date() < datetime.now(USER_TZ).date():
            return {"ok": False, "reason": "past"}

        current = coll.find_one({"_id": task_id})
        if not current:
            return {"ok": False, "reason": "missing"}
        # A task with an exact due TIME needs update_task_due_time, not this.
        if current.get("due_at"):
            return {"ok": False, "reason": "has_time"}

        coll.update_one(
            {"_id": task_id}, {"$set": {"due_date": due_dt.date().isoformat()}}
        )
        return {"ok": True, "due_date": due_dt.date().isoformat()}
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] update due date failed: {e}")
        return {"ok": False, "reason": "error"}


def update_task_due_time(
    task_id: str, due_iso: str, mongo_db=None, tasks_file=None
) -> dict:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        task_id = str(task_id or "").strip()
        due_iso = str(due_iso or "").strip()
        if coll is None or not task_id or not due_iso:
            return {"ok": False, "reason": "missing"}

        due_dt = _parse_iso(due_iso)
        if due_dt is None:
            return {"ok": False, "reason": "error"}
        if due_dt <= datetime.now(USER_TZ):
            return {"ok": False, "reason": "past"}

        r = coll.update_one(
            {"_id": task_id},
            {"$set": {"due_date": due_dt.date().isoformat(), "due_at": due_dt}},
        )
        if r.matched_count == 0:
            return {"ok": False, "reason": "missing"}
        return {"ok": True, "due_at": due_dt.isoformat()}
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] update due time failed: {e}")
        return {"ok": False, "reason": "error"}


def delete_task(task_id: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None or not task_id:
            return False
        r = coll.delete_one({"_id": task_id})
        return r.deleted_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] delete failed: {e}")
        return False


def rename_task(task_id: str, new_title: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        task_id = str(task_id or "").strip()
        new_title = str(new_title or "").strip()
        if coll is None or not task_id or not new_title:
            return False
        r = coll.update_one({"_id": task_id}, {"$set": {"text": new_title}})
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] rename failed: {e}")
        return False


def append_task_note(
    task_id: str, note_text: str, mongo_db=None, tasks_file=None
) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        task_id = str(task_id or "").strip()
        note_text = str(note_text or "").strip()
        if coll is None or not task_id or not note_text:
            return False
        current = coll.find_one({"_id": task_id})
        if not current:
            return False
        old = str(current.get("notes", "") or "").strip()
        new_notes = "\n".join(part for part in [old, note_text] if part).strip()
        coll.update_one({"_id": task_id}, {"$set": {"notes": new_notes}})
        return True
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] append note failed: {e}")
        return False


def replace_task_note(
    task_id: str, note_text: str, mongo_db=None, tasks_file=None
) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        task_id = str(task_id or "").strip()
        if coll is None or not task_id:
            return False
        r = coll.update_one(
            {"_id": task_id}, {"$set": {"notes": str(note_text or "").strip()}}
        )
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] replace note failed: {e}")
        return False


def set_task_priority(task_id: str, priority: str, mongo_db=None) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None or not task_id:
            return False
        clean = str(priority or "").strip().lower()
        if clean not in {"", "high", "normal", "low"}:
            return False
        r = coll.update_one({"_id": task_id}, {"$set": {"priority": clean}})
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] set priority failed: {e}")
        return False


def set_task_project(task_id: str, project: str, mongo_db=None) -> bool:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None or not task_id:
            return False
        r = coll.update_one(
            {"_id": task_id}, {"$set": {"project": str(project or "").strip()}}
        )
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] set project failed: {e}")
        return False


# ─── Bulk operations ─────────────────────────────────────────────────────────

def complete_all_tasks(mongo_db=None, tasks_file=None) -> int:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return 0
        r = coll.update_many(
            {"done": False},
            {"$set": {"done": True, "completed_at": datetime.now(timezone.utc)}},
        )
        print(f"[TasksStore] completed {r.modified_count} tasks")
        return r.modified_count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] complete_all failed: {e}")
        return 0


def delete_active_tasks(mongo_db=None, tasks_file=None) -> int:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return 0
        r = coll.delete_many({"done": False})
        print(f"[TasksStore] deleted {r.deleted_count} active tasks")
        return r.deleted_count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] delete active failed: {e}")
        return 0


def delete_completed_tasks(mongo_db=None, tasks_file=None) -> int:
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return 0
        r = coll.delete_many({"done": True})
        print(f"[TasksStore] deleted {r.deleted_count} completed tasks")
        return r.deleted_count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] delete completed failed: {e}")
        return 0


def clear_all_tasks(mongo_db=None) -> int:
    """Delete every task. The explicit operation that the google-era
    save_tasks([]) wipe used to hide. Returns how many were deleted."""
    try:
        _require_owner()
        coll = _coll(mongo_db)
        if coll is None:
            return 0
        r = coll.delete_many({})
        print(f"[TasksStore] cleared all tasks: {r.deleted_count}")
        return r.deleted_count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[TasksStore] clear-all failed: {e}")
        return 0


def save_tasks(tasks: List[Dict[str, Any]], mongo_db=None, tasks_file=None):
    """Compatibility shim from the Google era. Only the clear-all case (an empty
    list) is supported, and it just delegates to clear_all_tasks(); a non-empty
    list is ignored (partial sync was never supported)."""
    if tasks != []:
        print("[TasksStore] save_tasks ignored (partial sync unsupported)")
        return
    clear_all_tasks(mongo_db)


# Re-exports — same convenience surface google_tasks offered.
from app.features.tasks_matcher import (  # noqa: E402, F401
    resolve_task_reference_for_write,
    resolve_task_references_for_write,
    resolve_completed_task_reference_for_write,
    resolve_completed_task_references_for_write,
)
from app.features.tasks_formatter import (  # noqa: E402, F401
    build_task_display,
    build_completed_task_display,
    build_all_tasks_display,
    format_tasks_for_briefing,
)
