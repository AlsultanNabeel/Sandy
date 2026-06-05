"""Telegram message guards: in-memory dedup of Telegram retries.

The real cross-process / webhook dedup lives in `webhook.py` (Redis
`webhook_seen_setnx` by update_id). This module is just the per-process safety
net, and it's used in polling too. There used to be a Redis round-trip here as
well, but it duplicated webhook.py and never even looked at its own result, so
it added hot-path latency for nothing. Removed (B12).
"""

import threading
from collections import deque

_recent_message_keys = deque(maxlen=500)
_recent_message_set: set = set()
_recent_message_lock = threading.Lock()


def is_duplicate_telegram_message(message) -> bool:
    key = f"{message.chat.id}:{message.message_id}"
    with _recent_message_lock:
        if key in _recent_message_set:
            return True
        if len(_recent_message_keys) == _recent_message_keys.maxlen:
            old = _recent_message_keys.popleft()
            _recent_message_set.discard(old)
        _recent_message_keys.append(key)
        _recent_message_set.add(key)
    return False
