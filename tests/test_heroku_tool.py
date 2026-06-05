"""Tests for Heroku Tool (Tasks 39 + 40)."""

import pytest
from unittest.mock import patch, MagicMock

from app.tools.heroku_tool import (
    diagnose_logs,
    format_heroku_report,
    get_dyno_hours_used,
    get_dyno_status,
    get_logs,
    restart_dyno,
    _DYNO_HOURS_WARNING_PCT,
)


# ── Task 39: Logs & Diagnosis ─────────────────────────────────────────────────

class TestDiagnoseLogs:

    def test_detects_h10_crashed(self):
        log = "2026-01-01 app[web.1]: Error H10 (App crashed)"
        issues = diagnose_logs(log)
        assert any("H10" in i for i in issues)

    def test_detects_r14_memory(self):
        log = "app[web.1]: Error R14 (Memory quota exceeded)"
        issues = diagnose_logs(log)
        assert any("R14" in i for i in issues)

    def test_detects_503(self):
        log = "heroku[router]: 503 Service Unavailable"
        issues = diagnose_logs(log)
        assert any("503" in i for i in issues)

    def test_detects_h12_timeout(self):
        log = "heroku[router]: H12 Request timeout"
        issues = diagnose_logs(log)
        assert any("H12" in i for i in issues)

    def test_no_issues_on_clean_log(self):
        log = "app[web.1]: INFO Starting Sandy...\napp[web.1]: INFO Ready."
        issues = diagnose_logs(log)
        assert issues == []

    def test_detects_multiple_issues(self):
        log = "H10 App crashed\nR14 Memory quota exceeded"
        issues = diagnose_logs(log)
        assert len(issues) >= 2

    def test_case_insensitive(self):
        log = "error h10 app crashed"
        issues = diagnose_logs(log)
        assert any("H10" in i for i in issues)

    def test_empty_log(self):
        assert diagnose_logs("") == []


class TestGetLogs:

    def _mock_response(self, text):
        mock = MagicMock()
        mock.json.return_value = {"logplex_url": "https://logplex.example.com/sessions/abc"}
        mock.raise_for_status = MagicMock()
        log_mock = MagicMock()
        log_mock.text = text
        log_mock.raise_for_status = MagicMock()
        return mock, log_mock

    def test_returns_log_text(self):
        post_mock, get_mock = self._mock_response("line1\nline2\nline3")
        with patch("app.tools.heroku_tool._API_KEY", return_value="test-key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="sandy-robot"), \
             patch("requests.post", return_value=post_mock), \
             patch("requests.get", return_value=get_mock):
            result = get_logs(lines=3)
        assert "line1" in result
        assert "line3" in result

    def test_raises_on_missing_api_key(self):
        with patch("app.tools.heroku_tool._API_KEY", return_value=""):
            with pytest.raises(EnvironmentError, match="HEROKU_API_KEY"):
                get_logs()

    def test_raises_on_missing_logplex_url(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="app"), \
             patch("requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError):
                get_logs()


# ── Task 40: Dyno Management ──────────────────────────────────────────────────

class TestGetDynoStatus:

    def _mock_dynos(self, states):
        dynos = [{"name": f"web.{i+1}", "state": s, "updated_at": "2026-01-01T00:00:00Z"}
                 for i, s in enumerate(states)]
        mock = MagicMock()
        mock.json.return_value = dynos
        mock.raise_for_status = MagicMock()
        return mock

    def test_all_up(self):
        mock = self._mock_dynos(["up"])
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="app"), \
             patch("requests.get", return_value=mock):
            result = get_dyno_status()
        assert result["all_up"] is True
        assert result["crashed"] == []

    def test_crashed_dyno(self):
        mock = self._mock_dynos(["crashed"])
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="app"), \
             patch("requests.get", return_value=mock):
            result = get_dyno_status()
        assert result["all_up"] is False
        assert "web.1" in result["crashed"]

    def test_mixed_dynos(self):
        mock = self._mock_dynos(["up", "crashed", "up"])
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="app"), \
             patch("requests.get", return_value=mock):
            result = get_dyno_status()
        assert result["all_up"] is False
        assert len(result["dynos"]) == 3

    def test_raises_on_missing_key(self):
        with patch("app.tools.heroku_tool._API_KEY", return_value=""):
            with pytest.raises(EnvironmentError):
                get_dyno_status()


class TestRestartDyno:

    def test_restart_all(self):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="app"), \
             patch("requests.delete", return_value=mock) as mock_delete:
            result = restart_dyno()
        assert "✅" in result
        # Should hit /apps/app/dynos (no specific dyno)
        called_url = mock_delete.call_args[0][0]
        assert called_url.endswith("/dynos")

    def test_restart_specific_dyno(self):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="app"), \
             patch("requests.delete", return_value=mock) as mock_delete:
            result = restart_dyno(dyno_name="web.1")
        assert "✅" in result
        called_url = mock_delete.call_args[0][0]
        assert "web.1" in called_url


class TestGetDynoHoursUsed:

    def test_returns_usage_for_app(self):
        usage = [{"app": {"name": "sandy-robot"}, "dyno_hours": {"web": 300}}]
        mock = MagicMock()
        mock.json.return_value = usage
        mock.raise_for_status = MagicMock()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="sandy-robot"), \
             patch("requests.get", return_value=mock):
            result = get_dyno_hours_used()
        assert result["hours_used"] == 300
        assert result["hours_quota"] == 550

    def test_warning_at_high_usage(self):
        hours_near_limit = 460  # > 80% of 550
        usage = [{"app": {"name": "sandy-robot"}, "dyno_hours": {"web": hours_near_limit}}]
        mock = MagicMock()
        mock.json.return_value = usage
        mock.raise_for_status = MagicMock()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="sandy-robot"), \
             patch("requests.get", return_value=mock):
            result = get_dyno_hours_used()
        assert result["warning"] is True
        assert result["pct_used"] >= _DYNO_HOURS_WARNING_PCT

    def test_no_warning_at_low_usage(self):
        usage = [{"app": {"name": "sandy-robot"}, "dyno_hours": {"web": 100}}]
        mock = MagicMock()
        mock.json.return_value = usage
        mock.raise_for_status = MagicMock()
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("app.tools.heroku_tool._APP_NAME", return_value="sandy-robot"), \
             patch("requests.get", return_value=mock):
            result = get_dyno_hours_used()
        assert result["warning"] is False

    def test_returns_empty_on_error(self):
        with patch("app.tools.heroku_tool._API_KEY", return_value="key"), \
             patch("requests.get", side_effect=Exception("network error")):
            result = get_dyno_hours_used()
        assert result == {}


# ── Formatting ────────────────────────────────────────────────────────────────

class TestFormatHerokuReport:

    def test_all_up_shows_checkmark(self):
        status = {"all_up": True, "crashed": [], "dynos": [{"name": "web.1", "state": "up", "updated_at": ""}]}
        result = format_heroku_report(dyno_status=status)
        assert "✅" in result
        assert "web.1" in result

    def test_crashed_shows_warning(self):
        status = {"all_up": False, "crashed": ["web.1"], "dynos": [{"name": "web.1", "state": "crashed", "updated_at": ""}]}
        result = format_heroku_report(dyno_status=status)
        assert "🔴" in result
        assert "web.1" in result

    def test_hours_warning_shown(self):
        hours = {"hours_used": 460, "hours_quota": 550, "pct_used": 83.6, "warning": True}
        result = format_heroku_report(hours=hours)
        assert "⚠️" in result
        assert "460" in result

    def test_issues_listed(self):
        issues = ["🔴 H10 — App Crashed", "🟠 R14 — Memory Exceeded"]
        result = format_heroku_report(issues=issues)
        assert "H10" in result
        assert "R14" in result

    def test_log_tail_shown(self):
        logs = "\n".join(f"line {i}" for i in range(20))
        result = format_heroku_report(logs=logs, issues=[])
        assert "line 19" in result  # last line visible
        assert "```" in result

    def test_clean_log_shows_no_errors(self):
        result = format_heroku_report(logs="INFO all good", issues=[])
        assert "لا توجد أخطاء" in result


# ── Dispatcher integration ────────────────────────────────────────────────────

class TestDispatchHerokuAction:

    def test_logs_action_dispatched(self):
        from app.agent.executor.dispatch import execute_operational_action

        mock_logs = "log line 1\nlog line 2"
        with patch("app.tools.heroku_tool.get_logs", return_value=mock_logs), \
             patch("app.tools.heroku_tool.diagnose_logs", return_value=[]), \
             patch("app.tools.heroku_tool.format_heroku_report", return_value="report"):
            result = execute_operational_action(
                "heroku", {"action": "logs"},
                user_message="وريني الـ logs",
                normalized_user_message="وريني logs",
                session={}, session_file=None, mongo_db=None,
                tasks_file=None, create_chat_completion_fn=None, save_session_fn=None,
            )
        assert result["handled"] is True

    def test_missing_api_key_returns_handled_error(self):
        from app.agent.executor.dispatch import execute_operational_action

        with patch("app.tools.heroku_tool._API_KEY", return_value=""):
            result = execute_operational_action(
                "heroku", {"action": "logs"},
                user_message="logs", normalized_user_message="logs",
                session={}, session_file=None, mongo_db=None,
                tasks_file=None, create_chat_completion_fn=None, save_session_fn=None,
            )
        assert result["handled"] is True
        assert "HEROKU_API_KEY" in result["reply"]
