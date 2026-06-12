"""SA9: Task state machine + queue API for Project Builder tasks.

A task is a single unit of Project Builder work — typically one CI failure fix
or one manual feature request. State is stored entirely in Redis:

    sandy_sa:task:<id>     Hash    full task record
    sandy_sa:queue         List    pending work
    sandy_sa:processing    List    in-flight (BRPOPLPUSH atomic move)
    sandy_sa:webhook:<id>  String  webhook dedup
    sandy_sa:task:<id>:resume      String  set by web → worker reads it

All state-changing functions write IMMEDIATELY (no local variables) — so a
crash mid-task never loses progress.

States:
    queued         — in sandy_sa:queue
    in_progress    — worker picked it up
    waiting_user   — orchestrator asked owner a question
    done           — successfully merged PR
    failed         — gave up after attempts/timeout
    expired        — waiting_user > 24h
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.agent.project_builder import _redis as sa_redis
from app.utils import metrics as metrics

logger = logging.getLogger(__name__)

# Task type constants
TYPE_PROJECT_BUILDER = "project_builder"

# Status constants
STATUS_QUEUED = "queued"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING_USER = "waiting_user"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

MAX_ATTEMPTS = 3
MAX_QUEUE_SIZE = int(os.getenv("SANDY_SA_MAX_QUEUE", "10"))
WAITING_USER_MAX_SECS = 24 * 3600


# Task creation.
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_task_id(task_type: str = "task") -> str:
    short = uuid.uuid4().hex[:8]
    return f"sa_{task_type}_{short}"


def build_task_payload(
    *,
    task_type: str,
    chat_id: Optional[str] = None,
    description: str = "",
    failed_run_id: Optional[int] = None,
    failed_branch: Optional[str] = None,
    failed_commit_sha: Optional[str] = None,
    initial_logs: str = "",
    repo: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a fresh task payload — does NOT write to Redis."""
    task_id = new_task_id(task_type.replace("_", ""))
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "type": task_type,
        "status": STATUS_QUEUED,
        "attempts": 0,
        "chat_id": chat_id or "",
        "description": (description or "").strip()[:1000],
        "failed_run_id": failed_run_id,
        "failed_branch": failed_branch or "",
        "failed_commit_sha": failed_commit_sha or "",
        "initial_logs": (initial_logs or "")[:8000],
        "repo": repo or "",
        "branch": "",
        "last_commit_sha": "",
        "patched_files": [],
        "where_we_stopped": "",
        "agreed_solution": "",
        "context_summary": "",
        "enqueued_at": now_iso(),
        "last_active": now_iso(),
    }
    if extra:
        payload.update({k: v for k, v in extra.items() if k not in payload})
    return payload


# Enqueue.
def enqueue(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Add a task to the queue + write its hash. Refuses if queue is full.

    Returns: {ok, task_id, queue_size, error?}
    """
    if not sa_redis.is_available():
        return {"ok": False, "error": "Redis غير متاح — ما نقدر نطابور task"}

    qsize = sa_redis.queue_size()
    if qsize >= MAX_QUEUE_SIZE:
        return {
            "ok": False,
            "error": f"task queue ممتلئ ({qsize}/{MAX_QUEUE_SIZE}) — جرّب بعدين",
            "queue_size": qsize,
        }

    task_id = payload.get("task_id") or new_task_id("task")
    payload["task_id"] = task_id
    payload.setdefault("status", STATUS_QUEUED)
    payload.setdefault("enqueued_at", now_iso())

    # Write the hash first so it's queryable even if push fails
    sa_redis.task_hset(task_id, _flatten_for_hash(payload))

    # Push minimal payload onto queue (full data lives in the hash)
    pushed = sa_redis.queue_push(
        {
            "task_id": task_id,
            "type": payload.get("type"),
            "enqueued_at": payload.get("enqueued_at"),
        }
    )
    if not pushed:
        return {"ok": False, "error": "queue_push فشل", "task_id": task_id}

    logger.info(
        "[SA9] task enqueued id=%s type=%s queue_size=%d",
        task_id,
        payload.get("type"),
        qsize + 1,
    )
    return {"ok": True, "task_id": task_id, "queue_size": qsize + 1}


def _flatten_for_hash(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare values for HSET (lists/dicts → JSON strings)."""
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, (list, dict)):
            out[k] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            out[k] = ""
        else:
            out[k] = v
    return out


# State updates (atomic).
_TERMINAL_STATUSES = {"done", "failed", "expired", "failed_partial"}


def set_status(task_id: str, status: str, **extra_fields: Any) -> None:
    update: Dict[str, Any] = {"status": status, "last_active": now_iso()}
    update.update(extra_fields)
    sa_redis.task_hset(task_id, _flatten_for_hash(update))

    if status in _TERMINAL_STATUSES:
        # One-time emission on the terminal transition. Pull the task type
        # and original created_at to compute duration. If anything is missing,
        # skip silently — metrics are best-effort.
        try:
            task_type = sa_redis.task_hget(task_id, "type") or "unknown"
            metrics.inc_project_builder_task(task_type, status)
            started_iso = sa_redis.task_hget(task_id, "created_at") or ""
            if started_iso:
                started_dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()
                if elapsed > 0:
                    metrics.observe_project_builder_duration(elapsed)
        except Exception:
            pass


def increment_attempts(task_id: str) -> int:
    """Atomic +1. Returns the new count."""
    n = sa_redis.task_hincrby(task_id, "attempts", 1)
    sa_redis.task_hset(task_id, {"last_active": now_iso()})
    return n


def record_branch(task_id: str, branch: str) -> None:
    sa_redis.task_hset(task_id, {"branch": branch, "last_active": now_iso()})


def record_commit(task_id: str, commit_sha: str) -> None:
    sa_redis.task_hset(task_id, {"last_commit_sha": commit_sha, "last_active": now_iso()})


# Resume checkpoints.
# `stage` lets the worker pick up where it left off after a Worker
# restart (Heroku redeploy / SIGTERM). Set right before each blocking wait.
STAGE_WAITING_USER = "waiting_user"
STAGE_WAITING_CI = "waiting_ci"
# Project Builder stages
STAGE_PROJECT_PLAN_REVIEW = "project_plan_review"
STAGE_PROJECT_BUILDING = "project_building"
STAGE_PROJECT_GROUP_REVIEW = "project_group_review"
STAGE_PROJECT_AGENT_QUESTION = "project_agent_question"  # agent asked owner


def save_patch_state(
    task_id: str,
    *,
    applied_files: list,
    commit_sha: str,
) -> None:
    """Persist patch results so we can resume `wait_for_ci` after a shutdown
    without re-running the fixer."""
    sa_redis.task_hset(
        task_id,
        {
            "patched_files": json.dumps(applied_files or [], ensure_ascii=False),
            "last_commit_sha": commit_sha or "",
            "stage": STAGE_WAITING_CI,
            "last_active": now_iso(),
        },
    )


def clear_stage(task_id: str) -> None:
    sa_redis.task_hset(task_id, {"stage": "", "last_active": now_iso()})


def save_project_plan(task_id: str, plan: Dict[str, Any]) -> None:
    """Persist the SA8 PLAN (structured JSON) so a resume after shutdown can
    skip plan regeneration. Stage is set to PROJECT_PLAN_REVIEW."""
    sa_redis.task_hset(
        task_id,
        {
            "plan_json": json.dumps(plan, ensure_ascii=False),
            "stage": STAGE_PROJECT_PLAN_REVIEW,
            "last_active": now_iso(),
        },
    )


def save_plan_revision_request(task_id: str, revision_text: str) -> bool:
    """Owner replied to a pending PROJECT_PLAN_REVIEW with revision text
    instead of a plain agree/cancel. Store the text so the worker can
    re-plan once `signal_resume` unblocks `wait_for_resume`. Caller is
    expected to call `signal_resume` separately."""
    return sa_redis.task_hset(
        task_id,
        {
            "plan_revision_text": (revision_text or "")[:2000],
            "last_active": now_iso(),
        },
    )


def pop_plan_revision_text(task_id: str) -> str:
    """Read the revision text and clear it in one shot. Returns '' if none.

    The worker calls this right after `wait_for_resume` returns True — if
    the text is non-empty the resume was triggered by a revision request
    (regenerate the plan); empty means a plain approval (proceed to build).
    """
    text = sa_redis.task_hget(task_id, "plan_revision_text") or ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="ignore")
    if text:
        # Wipe it so a subsequent loop iteration doesn't re-trigger.
        sa_redis.task_hset(
            task_id,
            {"plan_revision_text": "", "last_active": now_iso()},
        )
    return text


def get_project_plan(task_id: str) -> Optional[Dict[str, Any]]:
    """Reverse of save_project_plan — returns the plan dict or None."""
    raw = sa_redis.task_hget(task_id, "plan_json")
    if not raw:
        return None
    try:
        plan = json.loads(raw)
        return plan if isinstance(plan, dict) else None
    except Exception:
        return None


def save_project_group_progress(task_id: str, *, current_group_index: int) -> None:
    """Phase B checkpoint (Step 4) — remembers which group/feature we're on."""
    sa_redis.task_hset(
        task_id,
        {
            "current_group_index": str(int(current_group_index)),
            "stage": STAGE_PROJECT_GROUP_REVIEW,
            "last_active": now_iso(),
        },
    )


def save_agent_resume_state(
    task_id: str,
    *,
    resume_state: Dict[str, Any],
    current_feature_index: int,
    chat_id: Optional[Any] = None,
) -> None:
    """M8 — Persist mid-agent state when `ask_owner` suspends the loop.

    The resume_state holds the full messages list, the pending tool_use id,
    and token totals so a later resume can hand the answer back to Claude
    without re-running prior tool calls.
    """
    # Attempt an atomic write: HSET the task hash and SET the chat->task index
    # inside a Redis MULTI/EXEC pipeline so a crash between steps won't leave
    # the system in an inconsistent state. If Redis isn't available or the
    # pipeline fails, fall back to the simple HSET path (fail-open but safe).
    client = sa_redis.get_client()
    payload = {
        "agent_resume_state": json.dumps(resume_state, ensure_ascii=False),
        "current_group_index": str(int(current_feature_index)),
        "stage": STAGE_PROJECT_AGENT_QUESTION,
        "last_active": now_iso(),
    }
    if chat_id is not None:
        payload["chat_id"] = str(chat_id)
    if client is None:
        sa_redis.task_hset(task_id, payload)
        try:
            metrics.inc_agent_resume_saved()
        except Exception:
            pass
        return

    try:
        # Use pipeline to group HSET + optional set(k_waiting_user)
        pipe = client.pipeline()
        # HSET (redis-py: hset with mapping)
        # Use same key as sa_redis.task_hset => k_task(task_id)
        pipe.hset(sa_redis.k_task(task_id), mapping=payload)
        # Ensure task hash has a TTL similar to other task writes
        pipe.expire(sa_redis.k_task(task_id), 86400 * 7)
        if chat_id is not None:
            # set chat->task index with TTL (so find_waiting_task_for_chat works)
            pipe.set(sa_redis.k_waiting_user(chat_id), task_id, ex=WAITING_USER_MAX_SECS)
        pipe.execute()
        try:
            metrics.inc_agent_resume_saved()
        except Exception:
            pass
    except Exception:
        # Pipeline failed — best-effort fallback
        try:
            sa_redis.task_hset(task_id, payload)
            if chat_id is not None:
                try:
                    client.set(sa_redis.k_waiting_user(chat_id), task_id, ex=WAITING_USER_MAX_SECS)
                except Exception:
                    pass
            try:
                metrics.inc_agent_resume_saved()
            except Exception:
                pass
        except Exception:
            # Give up silently — higher-level logic will still notify owner and
            # wait_for_resume will eventually fail if resume isn't signalled.
            pass


def get_agent_resume_state(task_id: str) -> Optional[Dict[str, Any]]:
    raw = sa_redis.task_hget(task_id, "agent_resume_state")
    if not raw:
        return None
    try:
        rs = json.loads(raw)
        return rs if isinstance(rs, dict) else None
    except Exception:
        return None


def clear_agent_resume_state(task_id: str) -> None:
    sa_redis.task_hset(task_id, {"agent_resume_state": "", "last_active": now_iso()})


def append_patched_file(task_id: str, file_path: str) -> None:
    """Add a file to the patched_files list (idempotent)."""
    raw = sa_redis.task_hget(task_id, "patched_files") or "[]"
    try:
        existing = json.loads(raw) if raw else []
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    if file_path not in existing:
        existing.append(file_path)
    sa_redis.task_hset(
        task_id,
        {
            "patched_files": json.dumps(existing, ensure_ascii=False),
            "last_active": now_iso(),
        },
    )


# Read.
def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Read full task record + parse JSON-stored fields."""
    raw = sa_redis.task_hgetall(task_id)
    if not raw:
        return None
    parsed: Dict[str, Any] = {}
    for k, v in raw.items():
        parsed[k] = _maybe_parse_json_field(k, v)
    # Coerce ints
    for int_field in ("attempts", "failed_run_id"):
        if int_field in parsed and isinstance(parsed[int_field], str):
            try:
                parsed[int_field] = int(parsed[int_field]) if parsed[int_field] else 0
            except ValueError:
                pass
    return parsed


def _maybe_parse_json_field(field: str, value: str) -> Any:
    if field in {"patched_files"} and value:
        try:
            return json.loads(value)
        except Exception:
            return []
    return value


# Resume mechanism (web-to-worker handshake).
def mark_waiting_user(task_id: str, *, where_we_stopped: str, chat_id: Optional[str] = None) -> None:
    """Worker calls this when it asks the owner a question."""
    fields: Dict[str, Any] = {
        "status": STATUS_WAITING_USER,
        "where_we_stopped": where_we_stopped[:1000],
        "last_active": now_iso(),
    }
    if chat_id:
        fields["chat_id"] = str(chat_id)
        # Index: this chat_id has an active waiting task
        client = sa_redis.get_client()
        if client is not None:
            try:
                client.set(
                    sa_redis.k_waiting_user(chat_id),
                    task_id,
                    ex=WAITING_USER_MAX_SECS,
                )
            except Exception as exc:
                logger.debug("[SA9] waiting_user index set failed: %s", exc)
    sa_redis.task_hset(task_id, _flatten_for_hash(fields))


def find_waiting_task_for_chat(chat_id: Any) -> Optional[str]:
    """Web side: 'does this chat have a Project Builder task awaiting reply?'.

    Returns the task_id or None. The task is still `waiting_user` after this —
    only `signal_resume()` actually unblocks the worker.
    """
    client = sa_redis.get_client()
    if client is None:
        return None
    try:
        val = client.get(sa_redis.k_waiting_user(chat_id))
        if val:
            return val
    except Exception:
        pass
    return None


def signal_resume(task_id: str, *, agreed_solution: str = "") -> bool:
    """Web side: 'owner agreed, worker may continue'.

    Writes the resume key with a 5-minute TTL — worker reads it within the
    polling loop, deletes it, and proceeds.
    """
    if agreed_solution:
        sa_redis.task_hset(
            task_id,
            {
                "agreed_solution": agreed_solution[:2000],
                "last_active": now_iso(),
            },
        )
    client = sa_redis.get_client()
    if client is None:
        return False
    try:
        client.set(sa_redis.k_task_resume(task_id), "yes", ex=300)
        try:
            metrics.inc_agent_resume_signal()
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.warning("[SA9] signal_resume failed: %s", exc)
        return False


def wait_for_resume(task_id: str, *, timeout: int = WAITING_USER_MAX_SECS, poll_interval: int = 5) -> bool:
    """Worker side: block until resume key is set OR timeout OR shutdown.

    Returns True if resumed by user, False otherwise. On natural timeout, marks
    the task as `expired`. On Worker shutdown (SIGTERM during a Heroku
    redeploy), returns False but leaves the task in `waiting_user` so the next
    Worker boot can resume the wait in-place. Callers should consult
    `shutdown.is_shutdown_requested()` to disambiguate.
    """
    # Local import to avoid an import cycle (shutdown module is tiny and pure).
    from app.agent.project_builder import shutdown as sa_shutdown

    client = sa_redis.get_client()
    if client is None:
        # Can't wait → treat as not resumed
        return False

    started = time.perf_counter()
    outcome = "timeout"
    deadline = time.monotonic() + max(60, timeout)
    try:
        while time.monotonic() < deadline:
            if sa_shutdown.is_shutdown_requested():
                outcome = "shutdown"
                return False
            try:
                val = client.get(sa_redis.k_task_resume(task_id))
                if val:
                    client.delete(sa_redis.k_task_resume(task_id))
                    # Clear chat-level waiting index
                    chat_id = sa_redis.task_hget(task_id, "chat_id")
                    if chat_id:
                        try:
                            client.delete(sa_redis.k_waiting_user(chat_id))
                        except Exception:
                            pass
                    outcome = "resumed"
                    return True

                # M2: لو الـ task انكنسل/فشل/انتهت مدته من مكان تاني → اخرج فوراً
                current_status = sa_redis.task_hget(task_id, "status")
                if current_status in (STATUS_FAILED, STATUS_EXPIRED):
                    logger.info(
                        "[SA9] wait_for_resume exiting: task=%s status=%s",
                        task_id, current_status,
                    )
                    outcome = "shutdown"
                    return False
            except Exception as exc:
                logger.debug("[SA9] resume poll error: %s", exc)
            if not sa_shutdown.interruptible_sleep(poll_interval):
                outcome = "shutdown"
                return False

        # Natural timeout — expired
        set_status(task_id, STATUS_EXPIRED, where_we_stopped="انتهت مدة الانتظار (24h)")
        chat_id = sa_redis.task_hget(task_id, "chat_id")
        if chat_id and client is not None:
            try:
                client.delete(sa_redis.k_waiting_user(chat_id))
            except Exception:
                pass
        outcome = "timeout"
        return False
    finally:
        try:
            elapsed = time.perf_counter() - started
            metrics.observe_resume_wait(elapsed)
            if outcome == "resumed":
                metrics.inc_resume_wait_resumed()
            elif outcome == "shutdown":
                metrics.inc_resume_wait_shutdown()
            else:
                metrics.inc_resume_wait_timeout()
        except Exception:
            pass
