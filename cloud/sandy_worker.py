"""Sandy worker dyno: background processor for project-builder tasks.

This is the entry point for the `worker:` dyno. It loops forever and:
    1. Recovers tasks stuck in the processing list (ages them out, requeues).
    2. Moves the next task from queue to processing (BRPOPLPUSH, atomic).
    3. Runs the Project Builder handler for project tasks.
    4. Removes the finished task from the processing list.

Use a Heroku Basic dyno or higher. Eco dynos sleep and stop polling.

Environment:
    REDIS_URL                   required (no Redis means the worker exits cleanly)
    TELEGRAM_BOT_TOKEN          required for owner notifications
    OWNER_CHAT_ID               required
    GITHUB_TOKEN                required (or GITHUB_PERSONAL_ACCESS_TOKEN)
    GITHUB_DEFAULT_REPO         required (owner/repo)
    GOOGLE_CLOUD_PROJECT        optional (some model integrations)
    VERTEX_REGION               defaults to us-east5

Importing this file is safe. The loop only runs under __main__.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

# Let `app.*` import whether we run from the repo root or from cloud/.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Load .env if there is one (same as sandy_agent.py).
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(_HERE.parent / ".env", override=False)
    load_dotenv(_HERE / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger("sandy_worker")

# configure_logging (in bootstrap) owns the logging setup: level, format,
# datefmt, and the quiet loggers. Calling logging.basicConfig here would win
# first and silence those, so we let bootstrap do it and only fall back if the
# import fails.
try:
    from app.bootstrap import configure_logging
    configure_logging(os.getenv("LOG_LEVEL", "INFO").upper())
except Exception:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _handle_signal(signum, frame):
    # Late import: app.* isn't on sys.path until run() runs.
    try:
        from app.agent.project_builder import shutdown as sa_shutdown
        sa_shutdown.request_shutdown()
    except Exception:
        pass
    logger.info(
        "[worker] got signal %s, asked for graceful shutdown; "
        "blocking waits break out within about 1s",
        signum,
    )


def _setup_signals():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


# Main loop
_RECOVERY_INTERVAL_SECS = 300  # check for stuck processing every 5 min
_RECOVERY_MAX_AGE_SECS = 1800  # a task stuck over 30 min counts as crashed
# Blocking pop timeout. This wakes instantly when a task is enqueued, so a long
# timeout adds no latency — it only controls how often we hit Redis while idle.
# At 5s that was ~518k commands/month (24/7), right at Upstash's free 500k cap;
# 30s cuts idle Redis traffic ~6x with no downside.
_QUEUE_POLL_TIMEOUT_SECS = 30
_LOOP_BACKOFF_MAX_SECS = 30  # cap on the exponential backoff after repeated loop errors


def run() -> None:
    from app.agent.project_builder import _redis as sa_redis
    from app.agent.project_builder import builder as orchestrator, shutdown as sa_shutdown, task_state
    from app.integrations import github_api

    if not sa_redis.is_available():
        logger.error("[worker] REDIS_URL not set or Redis unreachable, exiting")
        return

    if not github_api.is_configured():
        logger.warning(
            "[worker] GITHUB_TOKEN or GITHUB_DEFAULT_REPO missing, some features stay limited until set"
        )

    last_recovery = 0.0
    backoff = 2  # seconds; grows on repeated loop errors, resets on a clean pass
    logger.info(
        "[worker] ready, queue=%d processing=%d",
        sa_redis.queue_size(),
        sa_redis.processing_size(),
    )

    while not sa_shutdown.is_shutdown_requested():
        try:
            # Recovery sweep, every so often.
            now = time.time()
            if now - last_recovery > _RECOVERY_INTERVAL_SECS:
                moved = sa_redis.recover_stale_processing(_RECOVERY_MAX_AGE_SECS)
                if moved:
                    logger.info("[worker] recovered %d stale processing tasks", moved)
                last_recovery = now

            # Atomic pop: queue to processing.
            popped = sa_redis.queue_pop_to_processing(timeout=_QUEUE_POLL_TIMEOUT_SECS)
            backoff = 2  # Redis is reachable this pass; reset the backoff
            if popped is None:
                continue

            raw_payload = popped.get("_raw") or ""
            task_id = popped.get("task_id") or ""
            if not task_id:
                logger.warning("[worker] payload missing task_id: %r", popped)
                sa_redis.processing_complete(raw_payload)
                continue

            # Stamp the start time so recovery can age the task.
            task_state.set_status(
                task_id,
                task_state.STATUS_IN_PROGRESS,
                processing_started_at=task_state.now_iso(),
            )
            logger.info("[worker] picked up task=%s", task_id)

            try:
                task_type = popped.get("type") or ""
                if task_type != task_state.TYPE_PROJECT_BUILDER:
                    # project_builder is the only type now. Anything else is
                    # leftover state from an old schema, so fail it loudly and
                    # we catch it fast.
                    task_state.set_status(
                        task_id,
                        task_state.STATUS_FAILED,
                        where_we_stopped=f"unknown task type: {task_type!r}",
                    )
                    try:
                        from app.agent.project_builder import notifier
                        notifier.notify_owner(
                            f"Task `{task_id}` rejected: unknown task type "
                            f"`{task_type}` (المتوقع: project_builder)."
                        )
                    except Exception:
                        pass
                else:
                    result = orchestrator.process_project_task(task_id)
                    logger.info(
                        "[worker] project task=%s finished: %s",
                        task_id,
                        result.get("final_status"),
                    )
            except Exception as exc:
                logger.exception("[worker] project-builder handler crashed for task=%s: %s", task_id, exc)
                task_state.set_status(
                    task_id,
                    task_state.STATUS_FAILED,
                    where_we_stopped=f"orchestrator exception: {exc}",
                )
                try:
                    from app.agent.project_builder import notifier
                    notifier.notify_needs_human(
                        task_id=task_id,
                        reason=f"orchestrator crash: {exc}",
                        branch="",
                    )
                except Exception:
                    pass
            finally:
                try:
                    sa_redis.processing_complete(raw_payload)
                except Exception as exc:
                    logger.warning("[worker] processing_complete failed for task=%s: %s", task_id, exc)

        except Exception as exc:
            logger.exception("[worker] loop error: %s", exc)
            time.sleep(backoff)
            backoff = min(backoff * 2, _LOOP_BACKOFF_MAX_SECS)

    logger.info("[worker] shutdown complete")


if __name__ == "__main__":
    _setup_signals()
    try:
        run()
    except Exception as exc:
        logger.exception("[worker] fatal: %s", exc)
        sys.exit(1)
