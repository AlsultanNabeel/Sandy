import unittest
from types import SimpleNamespace
from unittest.mock import patch


class MetricsWiringTests(unittest.TestCase):
    def _fake_llm_client(self):
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: {"ok": True})
            )
        )

    def test_create_chat_completion_records_success_metrics(self):
        from app.integrations.openai_client import create_chat_completion

        with patch("app.integrations.openai_client._cb.call", return_value={"ok": True}) as mock_call, \
             patch("app.integrations.openai_client.metrics.observe_llm_completion") as mock_observe, \
             patch("app.integrations.openai_client.metrics.inc_llm_completion_success") as mock_success, \
             patch("app.integrations.openai_client.metrics.inc_llm_completion_failure") as mock_failure:
            result = create_chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                openai_client=self._fake_llm_client(),
                openai_model="gpt-test",
                prefer_azure=False,
            )

        self.assertEqual(result, {"ok": True})
        mock_call.assert_called_once()
        mock_observe.assert_called_once()
        mock_success.assert_called_once()
        mock_failure.assert_not_called()

    def test_create_chat_completion_records_failure_metrics(self):
        from app.integrations.openai_client import create_chat_completion

        with patch("app.integrations.openai_client._cb.call", side_effect=RuntimeError("boom")) as mock_call, \
             patch("app.integrations.openai_client.metrics.observe_llm_completion") as mock_observe, \
             patch("app.integrations.openai_client.metrics.inc_llm_completion_success") as mock_success, \
             patch("app.integrations.openai_client.metrics.inc_llm_completion_failure") as mock_failure:
            with self.assertRaises(RuntimeError):
                create_chat_completion(
                    messages=[{"role": "user", "content": "hi"}],
                    openai_client=self._fake_llm_client(),
                    openai_model="gpt-test",
                    prefer_azure=False,
                )

        mock_call.assert_called_once()
        mock_observe.assert_called_once()
        mock_success.assert_not_called()
        mock_failure.assert_called_once()

    def test_error_tracking_records_success_metric(self):
        from app.utils.error_tracking import log_unhandled_exception

        class FakeCollection:
            def __init__(self):
                self.docs = []

            def insert_one(self, doc):
                self.docs.append(doc)

        class FakeMongo:
            def __init__(self):
                self.collections = {"sandy_error_logs": FakeCollection()}

            def __getitem__(self, name):
                return self.collections[name]

        fake_mongo = FakeMongo()

        with patch("app.utils.error_tracking.metrics.inc_error_log_success") as mock_success, \
             patch("app.utils.error_tracking.metrics.inc_error_log_failure") as mock_failure:
            ok = log_unhandled_exception(fake_mongo, RuntimeError("boom"), source="unit")

        self.assertTrue(ok)
        self.assertEqual(len(fake_mongo["sandy_error_logs"].docs), 1)
        mock_success.assert_called_once()
        mock_failure.assert_not_called()

    def test_wait_for_resume_shutdown_records_metric(self):
        from app.agent.project_builder import task_state
        from app.agent.project_builder import _redis as sa_redis
        from app.agent.project_builder import shutdown as sa_shutdown

        class FakeClient:
            def get(self, key):
                return None

            def delete(self, key):
                return None

            def hget(self, key, field):
                return None

        with patch.object(sa_redis, "get_client", return_value=FakeClient()), \
             patch.object(sa_shutdown, "is_shutdown_requested", return_value=True), \
             patch.object(sa_shutdown, "interruptible_sleep", return_value=True), \
             patch("app.agent.project_builder.task_state.metrics.observe_resume_wait") as mock_observe, \
             patch("app.agent.project_builder.task_state.metrics.inc_resume_wait_shutdown") as mock_shutdown, \
             patch("app.agent.project_builder.task_state.metrics.inc_resume_wait_resumed") as mock_resumed, \
             patch("app.agent.project_builder.task_state.metrics.inc_resume_wait_timeout") as mock_timeout:
            ok = task_state.wait_for_resume("sa_test_123", timeout=5, poll_interval=0)

        self.assertFalse(ok)
        mock_observe.assert_called_once()
        mock_shutdown.assert_called_once()
        mock_resumed.assert_not_called()
        mock_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main()