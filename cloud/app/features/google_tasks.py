import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.integrations.google_oauth_env import (
    user_oauth_client_json_raw,
    user_oauth_token_json_raw,
    UNIFIED_SCOPES,
)
from app.utils.google_oauth_errors import (
    GoogleOAuthReconnectNeeded,
    maybe_raise_reconnect,
)
from app.utils.time import USER_TZ
from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.utils.user_profiles import active_profile_allows_privileged_access

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

_tasks_cb = CircuitBreaker(
    name="google_tasks", failure_threshold=5, recovery_timeout=60.0
)


SCOPES = UNIFIED_SCOPES
TASKLIST_ID = os.getenv("GOOGLE_TASKS_LIST_ID", "@default")
OAUTH_CLIENT_FILE = os.getenv(
    "GOOGLE_TASKS_OAUTH_FILE", "cloud/google-tasks-oauth.json"
)
OAUTH_TOKEN_FILE = os.getenv("GOOGLE_TASKS_TOKEN_FILE", "cloud/google-tasks-token.json")
_TASKS_SERVICE = None
_TASKS_LOCK = threading.Lock()


def _reset_tasks_service() -> None:
    """Force service re-initialisation on next call (e.g. after token expiry)."""
    global _TASKS_SERVICE
    with _TASKS_LOCK:
        _TASKS_SERVICE = None


def _get_tasks_service():
    global _TASKS_SERVICE

    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    if _TASKS_SERVICE is not None:
        return _TASKS_SERVICE

    with _TASKS_LOCK:
        if _TASKS_SERVICE is not None:
            return _TASKS_SERVICE

        creds = None
        using_env_token = False
        using_env_client = False

        token_raw = user_oauth_token_json_raw()
        if token_raw:
            creds = Credentials.from_authorized_user_info(
                json.loads(token_raw),
                SCOPES,
            )
            using_env_token = True
        elif os.path.exists(OAUTH_TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(OAUTH_TOKEN_FILE, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as refresh_err:
                    maybe_raise_reconnect(refresh_err)
                    raise
            else:
                client_raw = user_oauth_client_json_raw()
                if client_raw:
                    client_config = json.loads(client_raw)
                    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                    using_env_client = True
                elif os.path.exists(OAUTH_CLIENT_FILE):
                    flow = InstalledAppFlow.from_client_secrets_file(
                        OAUTH_CLIENT_FILE,
                        SCOPES,
                    )
                else:
                    raise RuntimeError(
                        "Google Tasks OAuth credentials are missing. "
                        "Set GOOGLE_USER_TOKEN_JSON (or GOOGLE_TASKS_TOKEN_JSON) and "
                        "GOOGLE_USER_OAUTH_JSON (or GOOGLE_TASKS_OAUTH_JSON) on the server, "
                        "or keep the local OAuth files for local development."
                    )

                if (
                    os.getenv("RAILWAY_ENVIRONMENT")
                    or os.getenv("RAILWAY_PROJECT_ID")
                    or os.getenv("DYNO")  # Heroku
                ):
                    raise RuntimeError(
                        "Google Tasks tried to start local OAuth flow on a managed platform. "
                        "Set GOOGLE_USER_TOKEN_JSON and GOOGLE_USER_OAUTH_JSON as env vars."
                    )

                creds = flow.run_local_server(port=0)

            if not using_env_token and not using_env_client:
                with open(OAUTH_TOKEN_FILE, "w", encoding="utf-8") as token_file:
                    token_file.write(creds.to_json())

        _TASKS_SERVICE = build("tasks", "v1", credentials=creds, cache_discovery=False)
        return _TASKS_SERVICE


def _execute_google_request_with_retry(request, label: str, attempts: int = 3) -> Any:
    """Execute a Google API request with exponential backoff and circuit breaker."""
    import google.auth.exceptions

    last_error: Exception = Exception("unknown")

    for attempt in range(1, attempts + 1):
        try:
            return _tasks_cb.call(request.execute, num_retries=2)
        except CircuitOpenError as e:
            print(f"[GoogleTasks] circuit open for {label}")
            raise RuntimeError(f"Google Tasks unavailable (circuit open): {e}") from e
        except google.auth.exceptions.RefreshError as e:
            print(f"[GoogleTasks] token refresh failed, resetting service: {e}")
            _reset_tasks_service()
            maybe_raise_reconnect(e)
            raise
        except Exception as e:
            last_error = e
            print(f"[GoogleTasks] {label} attempt {attempt}/{attempts} failed: {e}")
            if attempt < attempts:
                delay = min(2**attempt * 0.5, 8.0)
                time.sleep(delay)

    raise last_error


def _normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    status = task.get("status", "needsAction")
    notes = task.get("notes", "") or ""
    due_at = ""

    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.startswith("[SANDY_DUE_AT:") and stripped.endswith("]"):
            due_at = stripped[len("[SANDY_DUE_AT:") : -1].strip()
            break

    return {
        "id": task.get("id", ""),
        "text": task.get("title", "") or "",
        "done": status == "completed",
        "created_at": task.get("updated") or task.get("due") or "",
        "completed_at": task.get("completed"),
        "due": task.get("due"),
        "notes": notes,
        "due_at": due_at,
        "raw": task,
    }


def load_tasks(mongo_db=None, tasks_file=None) -> List[Dict[str, Any]]:
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")

        service = _get_tasks_service()
        items: List[Dict[str, Any]] = []
        page_token = None

        while True:
            result = _execute_google_request_with_retry(
                service.tasks().list(
                    tasklist=TASKLIST_ID,
                    showCompleted=False,
                    showHidden=False,
                    maxResults=100,
                    pageToken=page_token,
                ),
                "load_tasks",
            )

            for item in result.get("items", []):
                items.append(_normalize_task(item))

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        print(f"[GoogleTasks] loaded {len(items)} tasks")
        return items

    except GoogleOAuthReconnectNeeded:
        raise
    except PermissionError:
        raise
    except Exception as e:
        print(f"[GoogleTasks] load failed: {e}")
        return []


def save_tasks(tasks: List[Dict[str, Any]], mongo_db=None, tasks_file=None):
    """
    Compatibility only.
    Supports clear-all when tasks == [].
    """
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")

        if tasks != []:
            print(
                "[GoogleTasks] save_tasks ignored because partial sync is not supported"
            )
            return

        service = _get_tasks_service()
        page_token = None
        deleted_count = 0

        while True:
            result = (
                service.tasks()
                .list(
                    tasklist=TASKLIST_ID,
                    showCompleted=True,
                    showHidden=True,
                    maxResults=100,
                    pageToken=page_token,
                )
                .execute()
            )

            for item in result.get("items", []):
                task_id = item.get("id")
                if task_id:
                    service.tasks().delete(
                        tasklist=TASKLIST_ID,
                        task=task_id,
                    ).execute()
                    deleted_count += 1

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        print(f"[GoogleTasks] cleared all tasks: {deleted_count}")

    except PermissionError:
        raise
    except Exception as e:
        print(f"[GoogleTasks] clear-all failed: {e}")


def delete_active_tasks(mongo_db=None, tasks_file=None) -> int:
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")

        service = _get_tasks_service()
        page_token = None
        deleted_count = 0

        while True:
            result = (
                service.tasks()
                .list(
                    tasklist=TASKLIST_ID,
                    showCompleted=True,
                    showHidden=True,
                    maxResults=100,
                    pageToken=page_token,
                )
                .execute()
            )

            for item in result.get("items", []):
                if item.get("status") == "completed":
                    continue

                task_id = item.get("id")
                if task_id:
                    service.tasks().delete(
                        tasklist=TASKLIST_ID,
                        task=task_id,
                    ).execute()
                    deleted_count += 1

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        print(f"[GoogleTasks] deleted active tasks only: {deleted_count}")
        return deleted_count

    except PermissionError:
        raise
    except Exception as e:
        print(f"[GoogleTasks] delete active tasks failed: {e}")
        return 0


def add_task(
    task_text: str,
    due_iso: str = "",
    notes: str = "",
    mongo_db=None,
    tasks_file=None,
) -> str:
    try:
        service = _get_tasks_service()
        body = {
            "title": task_text.strip(),
        }
        clean_notes = "\n".join(
            line
            for line in notes.splitlines()
            if not line.strip().startswith("[SANDY_DUE_AT:")
        ).strip()

        if due_iso:
            due_dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=USER_TZ)
            else:
                due_dt = due_dt.astimezone(USER_TZ)

            if due_dt <= datetime.now(USER_TZ):
                raise ValueError("Task due time is in the past")

            body["due"] = f"{due_dt.date().isoformat()}T00:00:00.000Z"
            clean_notes = "\n".join(
                part
                for part in [clean_notes, f"[SANDY_DUE_AT:{due_dt.isoformat()}]"]
                if part
            )

        if clean_notes:
            body["notes"] = clean_notes

        result = service.tasks().insert(tasklist=TASKLIST_ID, body=body).execute()
        task_id = result.get("id", "")
        print(f"[GoogleTasks] task created: {task_text}")
        return task_id

    except Exception as e:
        print(f"[GoogleTasks] create failed: {e}")
        return ""


def complete_task(task_id: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        service = _get_tasks_service()
        now_iso = datetime.now(timezone.utc).isoformat()

        body = {
            "status": "completed",
            "completed": now_iso,
        }

        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body=body,
        ).execute()

        print(f"[GoogleTasks] task completed: {task_id}")
        return True

    except Exception as e:
        print(f"[GoogleTasks] complete failed: {e}")
        return False


def uncomplete_task(task_id: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        if not task_id:
            return False

        service = _get_tasks_service()

        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body={"status": "needsAction"},
        ).execute()

        print(f"[GoogleTasks] task uncompleted: {task_id}")
        return True

    except Exception as e:
        print(f"[GoogleTasks] uncomplete failed: {e}")
        return False


def update_task_due_date(
    task_id: str, due_iso: str, mongo_db=None, tasks_file=None
) -> dict:
    try:
        task_id = str(task_id or "").strip()
        due_iso = str(due_iso or "").strip()

        if not task_id or not due_iso:
            return {"ok": False, "reason": "missing"}

        due_dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=USER_TZ)
        else:
            due_dt = due_dt.astimezone(USER_TZ)

        if due_dt.date() < datetime.now(USER_TZ).date():
            return {"ok": False, "reason": "past"}

        service = _get_tasks_service()

        current = (
            service.tasks()
            .get(
                tasklist=TASKLIST_ID,
                task=task_id,
            )
            .execute()
        )

        notes = current.get("notes", "") or ""
        if "[SANDY_DUE_AT:" in notes:
            return {"ok": False, "reason": "has_time"}

        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body={"due": f"{due_dt.date().isoformat()}T00:00:00.000Z"},
        ).execute()

        print(f"[GoogleTasks] task due date updated: {task_id}")
        return {"ok": True, "due_date": due_dt.date().isoformat()}

    except Exception as e:
        print(f"[GoogleTasks] update due date failed: {e}")
        return {"ok": False, "reason": "error"}


def update_task_due_time(
    task_id: str, due_iso: str, mongo_db=None, tasks_file=None
) -> dict:
    try:
        task_id = str(task_id or "").strip()
        due_iso = str(due_iso or "").strip()

        if not task_id or not due_iso:
            return {"ok": False, "reason": "missing"}

        due_dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=USER_TZ)
        else:
            due_dt = due_dt.astimezone(USER_TZ)

        if due_dt <= datetime.now(USER_TZ):
            return {"ok": False, "reason": "past"}

        service = _get_tasks_service()

        current = (
            service.tasks()
            .get(
                tasklist=TASKLIST_ID,
                task=task_id,
            )
            .execute()
        )

        old_notes = current.get("notes", "") or ""
        clean_notes = "\n".join(
            line
            for line in old_notes.splitlines()
            if not line.strip().startswith("[SANDY_DUE_AT:")
        ).strip()

        new_notes = "\n".join(
            part
            for part in [clean_notes, f"[SANDY_DUE_AT:{due_dt.isoformat()}]"]
            if part
        )

        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body={
                "due": f"{due_dt.date().isoformat()}T00:00:00.000Z",
                "notes": new_notes,
            },
        ).execute()

        print(f"[GoogleTasks] task due time updated: {task_id}")
        return {"ok": True, "due_at": due_dt.isoformat()}

    except Exception as e:
        print(f"[GoogleTasks] update due time failed: {e}")
        return {"ok": False, "reason": "error"}


def delete_task(task_id: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        if not task_id:
            return False

        service = _get_tasks_service()
        service.tasks().delete(
            tasklist=TASKLIST_ID,
            task=task_id,
        ).execute()

        print(f"[GoogleTasks] task deleted: {task_id}")
        return True

    except Exception as e:
        print(f"[GoogleTasks] delete failed: {e}")
        return False


def rename_task(task_id: str, new_title: str, mongo_db=None, tasks_file=None) -> bool:
    try:
        task_id = str(task_id or "").strip()
        new_title = str(new_title or "").strip()

        if not task_id or not new_title:
            return False

        service = _get_tasks_service()
        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body={"title": new_title},
        ).execute()

        print(f"[GoogleTasks] task renamed: {task_id}")
        return True

    except Exception as e:
        print(f"[GoogleTasks] rename failed: {e}")
        return False


def append_task_note(
    task_id: str, note_text: str, mongo_db=None, tasks_file=None
) -> bool:
    try:
        task_id = str(task_id or "").strip()
        note_text = str(note_text or "").strip()

        if not task_id or not note_text:
            return False

        service = _get_tasks_service()
        current = (
            service.tasks()
            .get(
                tasklist=TASKLIST_ID,
                task=task_id,
            )
            .execute()
        )

        old_notes = current.get("notes", "") or ""
        new_notes = "\n".join(
            part for part in [old_notes.strip(), note_text] if part
        ).strip()

        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body={"notes": new_notes},
        ).execute()

        print(f"[GoogleTasks] task note appended: {task_id}")
        return True

    except Exception as e:
        print(f"[GoogleTasks] append note failed: {e}")
        return False


def replace_task_note(
    task_id: str, note_text: str, mongo_db=None, tasks_file=None
) -> bool:
    try:
        task_id = str(task_id or "").strip()
        note_text = str(note_text or "").strip()

        if not task_id or not note_text:
            return False

        service = _get_tasks_service()
        current = (
            service.tasks()
            .get(
                tasklist=TASKLIST_ID,
                task=task_id,
            )
            .execute()
        )

        old_notes = current.get("notes", "") or ""

        metadata_lines = []
        for line in old_notes.splitlines():
            clean_line = line.strip()
            if clean_line.startswith("[SANDY_") and clean_line.endswith("]"):
                metadata_lines.append(clean_line)

        new_notes = "\n".join(
            part for part in [note_text, *metadata_lines] if part
        ).strip()

        service.tasks().patch(
            tasklist=TASKLIST_ID,
            task=task_id,
            body={"notes": new_notes},
        ).execute()

        print(f"[GoogleTasks] task note replaced: {task_id}")
        return True

    except Exception as e:
        print(f"[GoogleTasks] replace note failed: {e}")
        return False


def load_completed_tasks(mongo_db=None, tasks_file=None) -> List[Dict[str, Any]]:
    try:
        service = _get_tasks_service()
        items: List[Dict[str, Any]] = []
        page_token = None

        while True:
            result = _execute_google_request_with_retry(
                service.tasks().list(
                    tasklist=TASKLIST_ID,
                    showCompleted=True,
                    showHidden=True,
                    maxResults=100,
                    pageToken=page_token,
                ),
                "load_completed_tasks",
            )

            for item in result.get("items", []):
                if item.get("status") == "completed":
                    items.append(_normalize_task(item))

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        print(f"[GoogleTasks] loaded {len(items)} completed tasks")
        return items

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[GoogleTasks] load completed failed: {e}")
        return []


def delete_completed_tasks(mongo_db=None, tasks_file=None) -> int:
    """Delete all completed tasks permanently via Google Tasks API."""
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")
        completed = load_completed_tasks()
        if not completed:
            return 0
        service = _get_tasks_service()
        deleted = 0
        for task in completed:
            task_id = task.get("id") or task.get("task_id", "")
            if not task_id:
                continue
            try:
                _execute_google_request_with_retry(
                    service.tasks().delete(tasklist=TASKLIST_ID, task=task_id),
                    "delete_completed_task",
                )
                deleted += 1
            except Exception as e:
                print(f"[GoogleTasks] could not delete task {task_id}: {e}")
        print(f"[GoogleTasks] deleted {deleted}/{len(completed)} completed tasks")
        return deleted
    except PermissionError:
        raise
    except Exception as e:
        print(f"[GoogleTasks] delete_completed_tasks failed: {e}")
        return 0


def load_overdue_tasks(mongo_db=None, tasks_file=None) -> List[Dict[str, Any]]:
    """Return active tasks whose due date is strictly before today."""
    try:
        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        today = datetime.now(USER_TZ).date()
        overdue = []
        for t in tasks:
            due = str(t.get("due_iso") or t.get("due") or "").strip()
            if not due:
                continue
            try:
                due_date = datetime.fromisoformat(due.replace("Z", "+00:00")).date()
                if due_date < today:
                    overdue.append(t)
            except Exception:
                continue
        return overdue
    except Exception as e:
        print(f"[GoogleTasks] load_overdue_tasks failed: {e}")
        return []


def complete_all_tasks(mongo_db=None, tasks_file=None) -> int:
    """Mark all active tasks as completed. Returns count of tasks completed."""
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")
        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        count = 0
        for t in tasks:
            task_id = t.get("id", "")
            if task_id and complete_task(task_id):
                count += 1
        print(f"[GoogleTasks] completed {count} tasks")
        return count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[GoogleTasks] complete_all_tasks failed: {e}")
        return 0


# Re-exports for backward compatibility
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
