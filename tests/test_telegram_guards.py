"""Tests for telegram_guards.py (message dedup on the Telegram hot path)."""
import threading
from unittest.mock import MagicMock

import app.api.telegram_guards as guards_module


class TestTelegramGuards:
    def setup_method(self):
        guards_module._recent_message_keys.clear()
        guards_module._recent_message_set.clear()

    def _make_message(self, chat_id=1, msg_id=1, user_id=1):
        chat = MagicMock()
        chat.id = chat_id
        user = MagicMock()
        user.id = user_id
        msg = MagicMock()
        msg.chat = chat
        msg.message_id = msg_id
        msg.from_user = user
        return msg

    def test_first_message_not_duplicate(self):
        msg = self._make_message(chat_id=1, msg_id=100)
        assert guards_module.is_duplicate_telegram_message(msg) is False

    def test_second_same_message_is_duplicate(self):
        msg = self._make_message(chat_id=1, msg_id=200)
        guards_module.is_duplicate_telegram_message(msg)
        assert guards_module.is_duplicate_telegram_message(msg) is True

    def test_different_message_not_duplicate(self):
        msg1 = self._make_message(chat_id=1, msg_id=1)
        msg2 = self._make_message(chat_id=1, msg_id=2)
        guards_module.is_duplicate_telegram_message(msg1)
        assert guards_module.is_duplicate_telegram_message(msg2) is False

    def test_thread_safety(self):
        results = []
        msg = self._make_message(chat_id=42, msg_id=42)
        def worker():
            results.append(guards_module.is_duplicate_telegram_message(msg))
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # First call should be False, rest True
        assert results.count(False) == 1
        assert results.count(True) == 19
