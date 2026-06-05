"""
Tests for Task 41 — Build & Deploy Monitoring.

Tests GitHub Actions build detection, log analysis, and Telegram alerts.
"""

import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock


from app.tools.heroku_tool import (
    get_latest_build,
    analyze_build_logs,
    format_build_alert,
)
from app.api.webhook import create_telegram_webhook_app


# ── Tests: analyze_build_logs ─────────────────────────────────────────────────

class TestBuildLogAnalysis:
    """Test error detection in build logs."""

    def test_detect_missing_import(self):
        """Should detect missing Python imports."""
        log = "ModuleNotFoundError: No module named 'requests'"
        result = analyze_build_logs(log)
        assert result["has_errors"]
        assert any(e["code"] == "MISSING_IMPORT" for e in result["errors"])

    def test_detect_syntax_error(self):
        """Should detect syntax errors in code."""
        log = "SyntaxError: invalid syntax in sandy.py line 42"
        result = analyze_build_logs(log)
        assert result["has_errors"]
        assert any(e["code"] == "SYNTAX_ERROR" for e in result["errors"])

    def test_detect_python_version_mismatch(self):
        """Should detect Python version incompatibilities."""
        log = "error: requires python 3.9 or higher"
        result = analyze_build_logs(log)
        assert result["has_errors"]
        assert any(e["code"] == "PYTHON_VERSION" for e in result["errors"])

    def test_detect_dependency_conflict(self):
        """Should detect dependency conflicts."""
        log = "ERROR: pip's dependency resolver does not currently resolve this combination"
        result = analyze_build_logs(log)
        assert result["has_errors"]
        assert any(e["code"] == "DEPENDENCY_CONFLICT" for e in result["errors"])

    def test_no_errors_in_clean_log(self):
        """Should return no errors for clean logs."""
        log = "✅ All tests passed\n✅ Build succeeded"
        result = analyze_build_logs(log)
        assert not result["has_errors"]
        assert len(result["errors"]) == 0

    def test_case_insensitive_error_detection(self):
        """Should detect errors case-insensitively."""
        log = "SYNTAXERROR on line 10"  # uppercase
        result = analyze_build_logs(log)
        assert result["has_errors"]


# ── Tests: format_build_alert ─────────────────────────────────────────────────

class TestBuildAlertFormatting:
    """Test alert message formatting for Telegram."""

    def test_failure_alert_with_errors(self):
        """Should format failure alert with detected errors."""
        build = {
            "status": "failure",
            "conclusion": "failure",
            "logs_url": "https://github.com/...",
            "created_at": "2026-05-06T10:00:00Z",
            "branch": "main",
            "commit": "abc123",
        }
        analysis = {
            "has_errors": True,
            "errors": [
                {
                    "code": "MISSING_IMPORT",
                    "label": "📦 مكتبة ناقصة",
                    "detail": "أضفها في requirements.txt",
                }
            ],
        }
        alert = format_build_alert(build, analysis)
        assert "🔴" in alert
        assert "Build فشل" in alert
        assert "مكتبة ناقصة" in alert
        assert "requirements.txt" in alert

    def test_success_alert(self):
        """Should format success alert."""
        build = {
            "status": "success",
            "conclusion": "success",
            "branch": "main",
            "commit": "def456",
        }
        analysis = {"has_errors": False, "errors": []}
        alert = format_build_alert(build, analysis)
        assert "✅" in alert
        assert "البناء نجح" in alert

    def test_alert_includes_commit_info(self):
        """Should include commit hash and branch in alert."""
        build = {
            "conclusion": "failure",
            "branch": "develop",
            "commit": "xyz789",
            "logs_url": "https://example.com",
        }
        analysis = {"has_errors": False, "errors": []}
        alert = format_build_alert(build, analysis)
        assert "develop" in alert
        assert "xyz789" in alert


# ── Tests: get_latest_build (mocked) ──────────────────────────────────────────

class TestGetLatestBuild:
    """Test GitHub Actions build fetching."""

    @patch.dict(
        "os.environ",
        {
            "GITHUB_TOKEN": "test_token",
            "GITHUB_REPO": "user/repo",
        },
    )
    @patch("app.tools.heroku_tool.requests.get")
    def test_fetch_successful_build(self, mock_get):
        """Should fetch latest successful build."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "workflow_runs": [
                {
                    "id": 12345,
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                    "head_sha": "abc123def456",
                    "name": "Build & Test",
                    "created_at": "2026-05-06T10:00:00Z",
                    "updated_at": "2026-05-06T10:15:00Z",
                    "html_url": "https://github.com/user/repo/actions/runs/12345",
                }
            ]
        }
        mock_get.return_value = mock_response

        result = get_latest_build()
        assert result["status"] == "success"
        assert result["conclusion"] == "success"
        assert result["branch"] == "main"
        assert result["commit"] == "abc123d"

    @patch.dict(
        "os.environ",
        {
            "GITHUB_TOKEN": "",
            "GITHUB_REPO": "",
        },
    )
    def test_missing_github_credentials(self):
        """Should return error when GitHub credentials are missing."""
        result = get_latest_build()
        assert result["status"] == "unknown"
        assert "GITHUB_TOKEN" in result.get("error", "")

    @patch.dict(
        "os.environ",
        {
            "GITHUB_TOKEN": "test_token",
            "GITHUB_REPO": "user/repo",
        },
    )
    @patch("app.tools.heroku_tool.requests.get")
    def test_api_request_failure(self, mock_get):
        """Should handle API request failures gracefully."""
        mock_get.side_effect = Exception("Network error")
        result = get_latest_build()
        assert result["status"] == "error"
        assert "Network error" in result.get("error", "")


# ── Tests: GitHub Webhook Integration ─────────────────────────────────────────

class TestGitHubBuildWebhook:
    """Test GitHub Actions webhook endpoint."""

    def test_webhook_endpoint_exists(self):
        """Should have GitHub build webhook endpoint."""
        telegram_bot = MagicMock()
        app_obj = create_telegram_webhook_app(
            telegram_bot=telegram_bot,
            webhook_path="/webhook",
        )
        assert "/webhook/github-build" in [r.rule for r in app_obj.url_map.iter_rules()]

    @patch.dict(
        "os.environ",
        {"GITHUB_WEBHOOK_SECRET": "test_secret", "OWNER_CHAT_ID": "123456"},
    )
    def test_webhook_signature_verification(self):
        """Should verify GitHub webhook signature."""
        telegram_bot = MagicMock()
        mongo_db = MagicMock()

        app_obj = create_telegram_webhook_app(
            telegram_bot=telegram_bot,
            webhook_path="/webhook",
            mongo_db=mongo_db,
        )

        with app_obj.test_client() as client:
            payload = json.dumps({
                "action": "completed",
                "workflow_run": {
                    "conclusion": "failure",
                    "name": "Build",
                    "html_url": "https://github.com/...",
                    "created_at": "2026-05-06T10:00:00Z",
                    "head_branch": "main",
                    "head_commit": {"id": "abc123", "message": "Test commit"},
                },
            })

            # Correct signature
            signature = "sha256=" + hmac.new(
                b"test_secret",
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()

            response = client.post(
                "/webhook/github-build",
                data=payload,
                headers={
                    "X-Hub-Signature-256": signature,
                    "Content-Type": "application/json",
                },
            )
            assert response.status_code == 200

            # Wrong signature
            response = client.post(
                "/webhook/github-build",
                data=payload,
                headers={
                    "X-Hub-Signature-256": "sha256=wrongsignature",
                    "Content-Type": "application/json",
                },
            )
            assert response.status_code == 403

    @patch.dict(
        "os.environ",
        {"OWNER_CHAT_ID": "123456"},
    )
    def test_webhook_sends_alert_on_failure(self):
        """Should send Telegram alert when build fails."""
        telegram_bot = MagicMock()
        mongo_db = MagicMock()

        app_obj = create_telegram_webhook_app(
            telegram_bot=telegram_bot,
            webhook_path="/webhook",
            mongo_db=mongo_db,
        )

        with app_obj.test_client() as client:
            payload = json.dumps({
                "action": "completed",
                "workflow_run": {
                    "id": 12345,
                    "conclusion": "failure",
                    "name": "Build",
                    "html_url": "https://github.com/user/repo/actions/runs/12345",
                    "created_at": "2026-05-06T10:00:00Z",
                    "head_branch": "main",
                    "head_commit": {
                        "id": "abc123def456",
                        "message": "ModuleNotFoundError: requests",
                    },
                },
            })

            response = client.post(
                "/webhook/github-build",
                data=payload,
                content_type="application/json",
            )

            assert response.status_code == 200
            # Should have called send_message
            telegram_bot.send_message.assert_called()

    @patch.dict(
        "os.environ",
        {"OWNER_CHAT_ID": "123456"},
    )
    def test_webhook_ignores_non_completed_events(self):
        """Should ignore webhook events that aren't 'completed'."""
        telegram_bot = MagicMock()

        app_obj = create_telegram_webhook_app(
            telegram_bot=telegram_bot,
            webhook_path="/webhook",
        )

        with app_obj.test_client() as client:
            payload = json.dumps({
                "action": "requested",  # not "completed"
                "workflow_run": {
                    "conclusion": "failure",
                    "name": "Build",
                },
            })

            response = client.post(
                "/webhook/github-build",
                data=payload,
                content_type="application/json",
            )

            assert response.status_code == 200
            # Should NOT send alert
            telegram_bot.send_message.assert_not_called()

    def test_webhook_handles_empty_payload(self):
        """Should handle empty webhook payload gracefully."""
        telegram_bot = MagicMock()

        app_obj = create_telegram_webhook_app(
            telegram_bot=telegram_bot,
            webhook_path="/webhook",
        )

        with app_obj.test_client() as client:
            response = client.post(
                "/webhook/github-build",
                data="",
                content_type="application/json",
            )

            assert response.status_code == 200

    @patch.dict(
        "os.environ",
        {"OWNER_CHAT_ID": "123456"},
    )
    def test_webhook_logs_to_mongodb(self):
        """Should log build events to MongoDB."""
        telegram_bot = MagicMock()
        mongo_db = MagicMock()

        app_obj = create_telegram_webhook_app(
            telegram_bot=telegram_bot,
            webhook_path="/webhook",
            mongo_db=mongo_db,
        )

        with app_obj.test_client() as client:
            payload = json.dumps({
                "action": "completed",
                "workflow_run": {
                    "id": 12345,
                    "conclusion": "failure",
                    "name": "Build",
                    "html_url": "https://github.com/...",
                    "created_at": "2026-05-06T10:00:00Z",
                    "head_branch": "main",
                    "head_commit": {"id": "abc123", "message": "Test"},
                },
            })

            response = client.post(
                "/webhook/github-build",
                data=payload,
                content_type="application/json",
            )

            assert response.status_code == 200
            # Should have logged to MongoDB
            mongo_db.github_builds.insert_one.assert_called()
            call_args = mongo_db.github_builds.insert_one.call_args
            assert call_args is not None
            doc = call_args[0][0]
            assert doc["run_id"] == 12345
            assert doc["conclusion"] == "failure"


# ── Integration Tests ─────────────────────────────────────────────────────────

class TestBuildMonitoringIntegration:
    """Integration tests for build monitoring workflow."""

    def test_full_failure_workflow(self):
        """Should detect and format failure with analysis."""
        # Simulate build failure log
        log = "ERROR: pip's dependency resolver conflict detected"

        # Analyze
        analysis = analyze_build_logs(log)
        assert analysis["has_errors"]

        # Build status
        build_status = {
            "status": "failure",
            "conclusion": "failure",
            "logs_url": "https://github.com/...",
            "created_at": "2026-05-06T10:00:00Z",
            "branch": "main",
            "commit": "abc123",
        }

        # Format alert
        alert = format_build_alert(build_status, analysis)

        # Verify structure
        assert "🔴" in alert
        assert "Build فشل" in alert
        assert "تضارب في التبعيات" in alert

    def test_full_success_workflow(self):
        """Should handle successful builds."""
        log = "✅ Build completed successfully"
        analysis = analyze_build_logs(log)
        assert not analysis["has_errors"]

        build_status = {
            "status": "success",
            "conclusion": "success",
            "branch": "main",
            "commit": "def456",
        }

        alert = format_build_alert(build_status, analysis)
        assert "✅" in alert
        assert "البناء نجح" in alert
