"""Redis-backed Short-Term Memory (STM) for Sandy conversations.

Preserves last 10 messages across all platforms (Telegram, web, voice) with 30-day TTL.
Used by router/specialist agents and response_node in LangGraph workflow.
"""

import logging
import os
from typing import Optional

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)

STM_TTL = 60 * 60 * 24 * 30  # 30 يوم
MAX_STM_MESSAGES = 10


class RedisSTMClient:
    """Redis client for STM persistence."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.environ.get("REDIS_URL")
        self.client = None
        self.enabled = False

        if not REDIS_AVAILABLE:
            logger.warning("redis-sdk not installed. STM will use in-memory fallback.")
            return

        if not self.redis_url:
            logger.warning("REDIS_URL not set. STM will use in-memory fallback.")
            return

        try:
            self.client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self.client.ping()
            self.enabled = True
            logger.info("Redis STM initialized successfully")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}. Using in-memory fallback.")
            self.client = None
            self.enabled = False

# Singleton instance
_stm_client: Optional[RedisSTMClient] = None


def get_redis_stm_client() -> RedisSTMClient:
    """Get or create Redis STM client (singleton)."""
    global _stm_client
    if _stm_client is None:
        _stm_client = RedisSTMClient()
    return _stm_client
