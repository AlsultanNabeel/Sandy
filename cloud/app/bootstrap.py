"""One-time startup initialization for Sandy.

Call bootstrap() once at the top of main() before anything else runs.
All functions here are idempotent — safe to call multiple times.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Third-party loggers that are noisy by default
_QUIET_LOGGERS = (
    "pymongo",
    "pymongo.topology",
    "pymongo.serverSelection",
    "pymongo.connection",
    "pymongo.command",
    "httpx",
    "httpcore",
    "urllib3",
    "apscheduler",
    "telebot",
    "googleapiclient",
    "googleapiclient.discovery",
    "openai",
    "openai._base_client",
    "anthropic",
    "boto3",
    "botocore",
    "s3transfer",
    "google",
    "google.auth",
    "google.api_core",
    "asyncio",
    "redis",
    "redis.connection",
    "redis.client",
    "app.agent.self_coding._redis",
    "bedrock",
    "bedrock-runtime",
)


def configure_logging(log_level: str = "INFO") -> None:
    """Set up root logger. Safe to call multiple times."""
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Redis timeouts are expected (empty queue polling) — suppress to ERROR only
    logging.getLogger("app.agent.self_coding._redis").setLevel(logging.ERROR)
    logger.debug("[Bootstrap] Logging configured at %s level", log_level)


def write_google_credentials() -> None:
    """Write GOOGLE_CREDENTIALS_JSON env var to a key file on disk.

    Heroku can't store key files, so the JSON is stored as an env var and
    written to disk on startup so Google SDK can find it via
    GOOGLE_APPLICATION_CREDENTIALS.
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if not creds_json:
        return
    key_path = "sandy-gcloud-key.json"
    try:
        with open(key_path, "w") as f:
            f.write(creds_json)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(key_path)
        logger.debug("[Bootstrap] Google credentials written to %s", key_path)
    except Exception as exc:
        logger.warning("[Bootstrap] Failed to write Google credentials: %s", exc)


def ensure_data_dirs() -> None:
    """Create runtime data directories if they don't exist."""
    from app.config import MEMORY_DIR, TASKS_DIR

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug("[Bootstrap] Data directories ready")


def bootstrap(app_env: str = "prod", app=None) -> None:
    """Run all one-time startup tasks.

    Call this once at the top of main(), before starting the bot runtime.

    Args:
        app_env: Runtime environment name ('dev' | 'prod').
        app:     Flask app instance, forwarded to Sentry for FlaskIntegration.
    """
    from app.config import LOG_LEVEL

    configure_logging(LOG_LEVEL)
    write_google_credentials()
    ensure_data_dirs()

    try:
        from app.integrations.sentry_config import init_sentry

        init_sentry(app_env=app_env, app=app)
    except Exception as exc:
        logger.warning("[Bootstrap] Sentry init failed: %s", exc)

    try:
        from app.agent.tools.setup import register_all_tools

        register_all_tools()
    except Exception as exc:
        logger.warning("[Bootstrap] Tools registration failed: %s", exc)

    try:
        from app.agent.facade.agent import mongo_db
        from app.agent.health_monitor import ensure_ttl_index
        try:
            ensure_ttl_index(mongo_db)
        except Exception as exc:
            logger.warning("[Bootstrap] ensure_ttl_index failed: %s", exc)
        if mongo_db is not None:
            # Each index is created independently so one failure doesn't silently
            # skip the rest. (label, callable) pairs keep the logging cheap.
            index_jobs = [
                ("web_chat_history.expire_at", lambda: mongo_db.web_chat_history.create_index(
                    "expire_at", expireAfterSeconds=0, background=True
                )),
                ("sandy_session_state.chat_id", lambda: mongo_db.sandy_session_state.create_index(
                    "chat_id", unique=True, background=True
                )),
                ("sandy_evals.chat_id+created_at", lambda: mongo_db.sandy_evals.create_index(
                    [("chat_id", 1), ("created_at", -1)], background=True
                )),
                ("guest_usage.jti+chat_type", lambda: mongo_db.guest_usage.create_index(
                    [("jti", 1), ("chat_type", 1)], unique=True, background=True
                )),
                ("guest_usage.last_request_at", lambda: mongo_db.guest_usage.create_index(
                    "last_request_at", background=True
                )),
                ("guest_usage.created_at_ttl", lambda: mongo_db.guest_usage.create_index(
                    "created_at", expireAfterSeconds=60 * 60 * 24 * 90, background=True
                )),
            ]
            for label, job in index_jobs:
                try:
                    job()
                except Exception as exc:
                    logger.warning("[Bootstrap] create_index %s failed: %s", label, exc)
    except Exception as exc:
        logger.warning("[Bootstrap] Mongo index setup failed: %s", exc)

    # Pre-warm the Redis STM singleton so the first voice turn doesn't pay
    # connection latency on the hot path. Best-effort: never abort bootstrap.
    try:
        from app.utils.redis_stm import get_redis_stm_client

        get_redis_stm_client()
    except Exception as exc:
        logger.warning("[Bootstrap] Redis STM pre-warm failed: %s", exc)

    logger.debug("[Bootstrap] Startup complete (env=%s)", app_env)
