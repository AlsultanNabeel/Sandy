"""Internal Redis access for Self-Coding Agent.

Reuses the project's existing Redis singleton (RedisSTMClient) for connection
pooling, but exposes the raw client for advanced ops (LPUSH, BRPOPLPUSH, HSET).

All Self-Coding keys are namespaced under `sandy_sa:*` to avoid collision with STM.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Namespaces
NS = "sandy_sa"  # prefix for all Self-Coding keys

# Key builders
def k_file_cache(path: str, sha: str) -> str:
    return f"{NS}:file:{sha}:{path}"


def k_task(task_id: str) -> str:
    return f"{NS}:task:{task_id}"


def k_task_resume(task_id: str) -> str:
    return f"{NS}:task:{task_id}:resume"


def k_webhook_seen(run_id: Any) -> str:
    return f"{NS}:webhook:{run_id}"


def k_task_queue() -> str:
    return f"{NS}:queue"


def k_task_processing() -> str:
    return f"{NS}:processing"


def k_waiting_user(chat_id: Any) -> str:
    return f"{NS}:waiting_user:{chat_id}"


def k_file_lock(repo: Any, path: str) -> str:
    repo_part = str(repo or "default").replace("/", "_")
    return f"{NS}:lock:{repo_part}:{path}"


# Client access.
def get_client():
    """Return raw redis client or None if unavailable."""
    try:
        from app.utils.redis_stm import get_redis_stm_client
        c = get_redis_stm_client()
        if not c.enabled or c.client is None:
            return None
        return c.client
    except Exception as exc:
        logger.debug("[sa._redis] unavailable: %s", exc)
        return None


def is_available() -> bool:
    return get_client() is not None


# JSON helpers.
def set_json(key: str, value: Any, ex: Optional[int] = None) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.set(key, json.dumps(value, ensure_ascii=False), ex=ex)
        return True
    except Exception as exc:
        logger.warning("[sa._redis] set_json failed key=%s: %s", key, exc)
        return False


def get_json(key: str) -> Optional[Any]:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("[sa._redis] get_json failed key=%s: %s", key, exc)
        return None


def delete(*keys: str) -> int:
    client = get_client()
    if client is None or not keys:
        return 0
    try:
        return client.delete(*keys)
    except Exception:
        return 0


def file_lock_acquire(repo: Any, path: str, owner: str, ttl: int = 1800) -> bool:
    client = get_client()
    if client is None:
        return True
    try:
        return bool(client.set(k_file_lock(repo, path), owner, nx=True, ex=ttl))
    except Exception:
        return False


def file_lock_release(repo: Any, path: str, owner: str) -> bool:
    client = get_client()
    if client is None:
        return True
    key = k_file_lock(repo, path)
    try:
        current = client.get(key)
        if isinstance(current, bytes):
            current = current.decode("utf-8", errors="ignore")
        if current is not None and current == owner:
            client.delete(key)
        return True
    except Exception:
        return False


# Task state helpers (HSET-based, so updates are atomic).
def task_hset(task_id: str, mapping: Dict[str, Any]) -> bool:
    """Atomically set fields in task hash."""
    client = get_client()
    if client is None:
        return False
    try:
        # Stringify non-string values
        encoded = {
            str(k): (json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) else str(v))
            for k, v in mapping.items()
        }
        client.hset(k_task(task_id), mapping=encoded)
        client.expire(k_task(task_id), 86400 * 7)  # 7 days
        return True
    except Exception as exc:
        logger.warning("[sa._redis] task_hset failed task=%s: %s", task_id, exc)
        return False


def task_hget(task_id: str, field: str) -> Optional[str]:
    client = get_client()
    if client is None:
        return None
    try:
        val = client.hget(k_task(task_id), field)
        return val
    except Exception:
        return None


def task_hgetall(task_id: str) -> Dict[str, str]:
    client = get_client()
    if client is None:
        return {}
    try:
        return client.hgetall(k_task(task_id)) or {}
    except Exception:
        return {}


def task_hincrby(task_id: str, field: str, amount: int = 1) -> int:
    client = get_client()
    if client is None:
        return 0
    try:
        return int(client.hincrby(k_task(task_id), field, amount))
    except Exception as exc:
        logger.warning("[sa._redis] hincrby failed: %s", exc)
        return 0


# Webhook dedup.
def webhook_seen_setnx(run_id: Any, ttl: int = 3600) -> bool:
    """Returns True if this run_id is new (and reserves it), False if already seen.

    Uses SET NX EX for atomic check-and-set.
    """
    client = get_client()
    if client is None:
        return True  # fail-open — fall through; webhook handler can dedup elsewhere
    try:
        was_set = client.set(k_webhook_seen(run_id), "1", nx=True, ex=ttl)
        return bool(was_set)
    except Exception:
        return True


# Task queue. Atomic, with crash recovery.
def queue_push(task_payload: Dict[str, Any]) -> bool:
    """LPUSH a task onto the queue."""
    client = get_client()
    if client is None:
        return False
    try:
        client.lpush(k_task_queue(), json.dumps(task_payload, ensure_ascii=False))
        return True
    except Exception as exc:
        logger.warning("[sa._redis] queue_push failed: %s", exc)
        return False


def queue_pop_to_processing(timeout: int = 5) -> Optional[Dict[str, Any]]:
    """BRPOPLPUSH from queue to processing — atomic. None on timeout.

    Tasks remain in processing until explicitly removed (crash recovery).
    """
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.brpoplpush(k_task_queue(), k_task_processing(), timeout=timeout)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            payload["_raw"] = raw  # preserve original for LREM later
            return payload
        except Exception:
            # Bad payload — remove from processing
            client.lrem(k_task_processing(), 1, raw)
            return None
    except Exception as exc:
        logger.warning("[sa._redis] queue_pop failed: %s", exc)
        return None


def processing_complete(raw_payload: str) -> bool:
    """Remove a completed task from the processing list."""
    client = get_client()
    if client is None:
        return False
    try:
        client.lrem(k_task_processing(), 1, raw_payload)
        return True
    except Exception:
        return False


def queue_size() -> int:
    client = get_client()
    if client is None:
        return 0
    try:
        return int(client.llen(k_task_queue()))
    except Exception:
        return 0


def processing_size() -> int:
    client = get_client()
    if client is None:
        return 0
    try:
        return int(client.llen(k_task_processing()))
    except Exception:
        return 0


def recover_stale_processing(max_age_seconds: int = 1800) -> int:
    """Move tasks stuck in processing > max_age back to queue. Returns count moved.

    Each task payload carries `enqueued_at` — we check that.
    """
    client = get_client()
    if client is None:
        return 0
    moved = 0
    try:
        items = client.lrange(k_task_processing(), 0, -1) or []
        now = time.time()
        for raw in items:
            try:
                payload = json.loads(raw)
                started = payload.get("processing_started_at") or payload.get("enqueued_at", now)
                if isinstance(started, str):
                    # Best-effort ISO parse
                    from datetime import datetime
                    try:
                        started = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        started = now
                if now - float(started) > max_age_seconds:
                    # M3: لو الـ task فشل/انكنسل/خلص — احذفه بدل ما ترجعه للـ queue
                    task_id = payload.get("task_id")
                    if task_id:
                        try:
                            status = task_hget(task_id, "status")
                        except Exception:
                            status = None
                        if status in ("failed", "expired", "done"):
                            client.lrem(k_task_processing(), 1, raw)
                            continue
                    client.lrem(k_task_processing(), 1, raw)
                    client.lpush(k_task_queue(), raw)
                    moved += 1
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[sa._redis] recover failed: %s", exc)
    return moved
