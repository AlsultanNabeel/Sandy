"""Tests for Task 42 — Proactive Heroku Health Alerts."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import app.api.telegram_runtime as runtime


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scheduler_and_bot():
    scheduler = MagicMock()
    bot = MagicMock()
    agent = MagicMock()
    agent._build_morning_briefing.return_value = "briefing"
    return scheduler, bot, agent


def _configure(extra_patches=None):
    """Call configure_sandy_scheduler and return the check_heroku_health job fn."""
    scheduler, bot, agent = _make_scheduler_and_bot()
    reminder_fn = MagicMock()

    with patch("app.utils.user_profiles.OWNER_CHAT_ID", "123"):
        runtime.configure_sandy_scheduler(
            scheduler=scheduler,
            agent=agent,
            telegram_bot=bot,
            sandy_user_chat_id="123",
            check_reminders_fn=reminder_fn,
        )

    # extract the check_heroku_health job (last add_job call — 5th)
    calls = scheduler.add_job.call_args_list
    assert len(calls) == 5, "Expected 5 scheduler jobs"
    health_fn = calls[-1][0][0]   # positional arg 0 of last call (check_heroku_health)
    interval   = calls[-1][1].get("minutes") or calls[-1][0][2] if len(calls[-1][0]) > 2 else None
    return health_fn, bot, interval


# ── Registration tests ────────────────────────────────────────────────────────

class TestSchedulerRegistration:

    def test_three_jobs_registered(self):
        scheduler, bot, agent = _make_scheduler_and_bot()
        with patch("app.utils.user_profiles.OWNER_CHAT_ID", "123"):
            runtime.configure_sandy_scheduler(
                scheduler=scheduler, agent=agent, telegram_bot=bot,
                sandy_user_chat_id="123", check_reminders_fn=MagicMock(),
            )
        assert scheduler.add_job.call_count == 8

    def test_heroku_health_interval_5_minutes(self):
        scheduler, bot, agent = _make_scheduler_and_bot()
        with patch("app.utils.user_profiles.OWNER_CHAT_ID", "123"):
            runtime.configure_sandy_scheduler(
                scheduler=scheduler, agent=agent, telegram_bot=bot,
                sandy_user_chat_id="123", check_reminders_fn=MagicMock(),
            )
        last_call = scheduler.add_job.call_args_list[-1]
        assert last_call[1].get("minutes") == 5

    def test_heroku_health_uses_interval_trigger(self):
        scheduler, bot, agent = _make_scheduler_and_bot()
        with patch("app.utils.user_profiles.OWNER_CHAT_ID", "123"):
            runtime.configure_sandy_scheduler(
                scheduler=scheduler, agent=agent, telegram_bot=bot,
                sandy_user_chat_id="123", check_reminders_fn=MagicMock(),
            )
        last_call = scheduler.add_job.call_args_list[-1]
        assert last_call[0][1] == "interval"


# ── Health check behaviour tests ──────────────────────────────────────────────

class TestCheckHerokuHealth:

    def setup_method(self):
        runtime._heroku_alert_state.clear()

    def _get_health_fn(self, owner_chat_id="123"):
        scheduler, bot, agent = _make_scheduler_and_bot()
        with patch("app.utils.user_profiles.OWNER_CHAT_ID", owner_chat_id):
            runtime.configure_sandy_scheduler(
                scheduler=scheduler, agent=agent, telegram_bot=bot,
                sandy_user_chat_id=owner_chat_id,
                check_reminders_fn=MagicMock(),
            )
        fn = scheduler.add_job.call_args_list[-1][0][0]
        return fn, bot

    def test_no_alert_when_api_key_missing(self):
        fn, bot = self._get_health_fn()
        with patch("app.tools.heroku_tool._API_KEY", return_value=""):
            fn()
        bot.send_message.assert_not_called()

    def test_no_alert_when_all_healthy(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="INFO ok\nINFO ready"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=[]):
            fn()
        bot.send_message.assert_not_called()

    def test_alert_sent_on_h10_crash(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="H10 App crashed"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=["🔴 H10 — App Crashed"]):
            fn()
        bot.send_message.assert_called_once()
        msg = bot.send_message.call_args[0][1]
        assert "H10" in msg
        assert "خليني أصلح" in msg

    def test_alert_sent_on_crashed_dyno(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": False, "crashed": ["web.1"], "dynos": []}
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="INFO ok"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=[]):
            fn()
        bot.send_message.assert_called_once()
        msg = bot.send_message.call_args[0][1]
        assert "web.1" in msg

    def test_alert_includes_last_5_log_lines(self):
        fn, bot = self._get_health_fn()
        log = "\n".join(f"line {i}" for i in range(20)) + "\nH10 App crashed"
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value=log), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=["🔴 H10 — App Crashed"]):
            fn()
        msg = bot.send_message.call_args[0][1]
        assert "line 19" in msg or "H10 App crashed" in msg  # last lines present

    def test_cooldown_prevents_duplicate_alert(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}
        issues = ["🔴 H10 — App Crashed"]

        # Pre-set state as if alert was sent 5 min ago. The cooldown now keys
        # off a sorted-set of labels (M14a) so we mirror that shape.
        runtime._heroku_alert_state["labels"] = sorted(set(issues))
        runtime._heroku_alert_state["time"] = datetime.now(runtime.USER_TZ)

        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="H10 App crashed"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=issues):
            fn()
        bot.send_message.assert_not_called()

    def test_cooldown_resets_after_30_minutes(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}
        issues = ["🔴 H10 — App Crashed"]

        # Pre-set state as if alert was sent 31 min ago
        old_time = datetime.now(runtime.USER_TZ) - timedelta(minutes=31)
        runtime._heroku_alert_state["labels"] = sorted(set(issues))
        runtime._heroku_alert_state["time"] = old_time

        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="H10 App crashed"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=issues):
            fn()
        bot.send_message.assert_called_once()

    def test_different_issues_bypass_cooldown(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}

        # Previous alert was for H10
        runtime._heroku_alert_state["labels"] = ["🔴 H10 — App Crashed"]
        runtime._heroku_alert_state["time"] = datetime.now(runtime.USER_TZ)

        # Now we have R14 instead
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="R14 Memory quota"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=["🟠 R14 — Memory Exceeded"]):
            fn()
        bot.send_message.assert_called_once()

    # NOTE: the generic-ERROR keyword counter was removed in M14a — Sentry
    # owns Python-level exceptions now via /webhook/sentry. The old test
    # `test_repeated_error_3_times_triggers_alert` was tied to that loop.

    def test_no_alert_when_owner_chat_id_missing(self):
        fn, bot = self._get_health_fn(owner_chat_id="")
        fn()
        bot.send_message.assert_not_called()

    def test_alert_contains_tanbih_header(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="H10 App crashed"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=["🔴 H10 — App Crashed"]):
            fn()
        msg = bot.send_message.call_args[0][1]
        assert "تنبيه Sandy" in msg

    def test_clear_state_resets_when_healthy(self):
        fn, bot = self._get_health_fn()
        mock_dyno = {"all_up": True, "crashed": [], "dynos": []}

        runtime._heroku_alert_state["labels"] = ["🔴 H10"]
        runtime._heroku_alert_state["time"] = datetime.now(runtime.USER_TZ)

        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", return_value=mock_dyno), \
             patch("app.tools.heroku_tool.get_logs", return_value="INFO all good"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=[]):
            fn()

        assert runtime._heroku_alert_state == {}
        bot.send_message.assert_not_called()

    def test_dyno_error_logs_heroku_alert(self, capsys):
        fn, bot = self._get_health_fn()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool.get_dyno_status", side_effect=Exception("API down")), \
             patch("app.tools.heroku_tool.get_logs", return_value="INFO ok"), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=[]):
            fn()
        bot.send_message.assert_not_called()
        captured = capsys.readouterr()
        assert "dyno check failed" in captured.out
