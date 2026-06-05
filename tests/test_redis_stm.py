"""Tests for Redis-backed Short-Term Memory (STM)."""

import unittest
from unittest.mock import patch, MagicMock
import os


class TestRedisSTM(unittest.TestCase):
    """Test Redis STM client."""

    def setUp(self):
        """Set up test fixtures."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_redis_stm_disabled_without_redis_url(self):
        """Should initialize disabled when REDIS_URL missing."""
        os.environ.pop("REDIS_URL", None)

        from app.utils.redis_stm import RedisSTMClient

        client = RedisSTMClient(redis_url=None)
        self.assertFalse(client.enabled)

    @patch("redis.from_url")
    def test_redis_stm_connection_success(self, mock_redis):
        """Should connect successfully with valid REDIS_URL."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_redis.return_value = mock_client

        from app.utils.redis_stm import RedisSTMClient

        client = RedisSTMClient(redis_url="redis://localhost:6379")
        self.assertTrue(client.enabled)
        mock_client.ping.assert_called_once()

    @patch("redis.from_url")
    def test_redis_stm_connection_failure(self, mock_redis):
        """Should handle connection errors gracefully."""
        mock_redis.side_effect = ConnectionError("Connection failed")

        from app.utils.redis_stm import RedisSTMClient

        client = RedisSTMClient(redis_url="redis://invalid:6379")
        self.assertFalse(client.enabled)

    def test_get_redis_stm_client_singleton(self):
        """Should return same instance on multiple calls."""
        from app.utils.redis_stm import get_redis_stm_client

        client1 = get_redis_stm_client()
        client2 = get_redis_stm_client()
        self.assertIs(client1, client2)


if __name__ == "__main__":
    unittest.main()
