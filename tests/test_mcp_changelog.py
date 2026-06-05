"""Tests for generate_changelog tool and _build_changelog helper."""

import json
import unittest
from unittest.mock import MagicMock, patch


def _make_commit(sha: str, msg: str, date: str = "2026-05-16") -> dict:
    return {
        "sha": sha,
        "commit": {
            "message": msg,
            "author": {"name": "Test", "date": f"{date}T10:00:00Z"},
        },
    }


class TestBuildChangelog(unittest.TestCase):

    def setUp(self):
        from app.agent.tools.schemas.mcp_tools import _build_changelog
        self._build = _build_changelog

    def test_feat_goes_to_features_group(self):
        data = [_make_commit("abc1234", "feat: add voice commands")]
        result = self._build(json.dumps(data))
        self.assertIn("✨ ميزات جديدة", result)
        self.assertIn("add voice commands", result)

    def test_fix_goes_to_fixes_group(self):
        data = [_make_commit("def5678", "fix(tts): reduce padding sentences")]
        result = self._build(json.dumps(data))
        self.assertIn("🐛 إصلاحات", result)
        self.assertIn("reduce padding sentences", result)

    def test_refactor_goes_to_improvements_group(self):
        data = [_make_commit("aaa0000", "refactor: simplify router logic")]
        result = self._build(json.dumps(data))
        self.assertIn("♻️ تحسينات", result)

    def test_unknown_type_goes_to_other_group(self):
        data = [_make_commit("bbb1111", "update readme with new badges")]
        result = self._build(json.dumps(data))
        self.assertIn("📌 أخرى", result)
        self.assertIn("update readme", result)

    def test_header_present(self):
        data = [_make_commit("abcdef1234567", "feat: test sha length")]
        result = self._build(json.dumps(data))
        self.assertIn("آخر التغييرات", result)

    def test_empty_list_returns_no_commits_message(self):
        result = self._build(json.dumps([]))
        self.assertIn("لا يوجد commits", result)

    def test_invalid_json_returns_raw(self):
        result = self._build("not json")
        self.assertEqual(result, "not json")

    def test_date_included_in_output(self):
        data = [_make_commit("ccc2222", "fix: crash on startup", date="2026-04-01")]
        result = self._build(json.dumps(data))
        self.assertIn("2026-04-01", result)

    def test_multiple_types_produce_multiple_sections(self):
        data = [
            _make_commit("aaa0001", "feat: new dashboard"),
            _make_commit("aaa0002", "fix: null pointer"),
            _make_commit("aaa0003", "docs: update api reference"),
        ]
        result = self._build(json.dumps(data))
        self.assertIn("✨ ميزات جديدة", result)
        self.assertIn("🐛 إصلاحات", result)
        self.assertIn("📚 توثيق", result)


class TestGenerateChangelog(unittest.TestCase):

    def _ctx(self):
        ctx = MagicMock()
        ctx.state = {"chat_id": "c1"}
        ctx.mongo_db = None
        return ctx

    def _mock_gh(self, commits: list):
        return {"handled": True, "reply": json.dumps(commits)}

    def test_returns_changelog_on_success(self):
        commits = [_make_commit("abc1234", "feat: voice tone control")]
        with patch("app.agent.tools.schemas.mcp_tools._gh_repo", return_value=("owner", "repo", "")):
            with patch("app.agent.tools.schemas.mcp_tools._gh", return_value=self._mock_gh(commits)):
                from app.agent.tools.schemas.mcp_tools import generate_changelog
                result = generate_changelog({}, self._ctx())
        self.assertTrue(result["handled"])
        self.assertIn("آخر التغييرات", result["reply"])
        self.assertIn("✨ ميزات جديدة", result["reply"])

    def test_returns_error_when_repo_not_configured(self):
        with patch("app.agent.tools.schemas.mcp_tools._gh_repo", return_value=("", "", "GITHUB_DEFAULT_REPO غير مضبوط")):
            from app.agent.tools.schemas.mcp_tools import generate_changelog
            result = generate_changelog({}, self._ctx())
        self.assertIn("غير مضبوط", result["reply"])

    def test_empty_reply_from_github_returns_graceful_error(self):
        with patch("app.agent.tools.schemas.mcp_tools._gh_repo", return_value=("owner", "repo", "")):
            with patch("app.agent.tools.schemas.mcp_tools._gh", return_value={"handled": True, "reply": ""}):
                from app.agent.tools.schemas.mcp_tools import generate_changelog
                result = generate_changelog({}, self._ctx())
        self.assertIn("ما قدرت", result["reply"])

    def test_count_capped_at_50(self):
        captured = {}
        def mock_gh(tool, args):
            captured.update(args)
            return {"handled": True, "reply": json.dumps([])}

        with patch("app.agent.tools.schemas.mcp_tools._gh_repo", return_value=("owner", "repo", "")):
            with patch("app.agent.tools.schemas.mcp_tools._gh", side_effect=mock_gh):
                from app.agent.tools.schemas.mcp_tools import generate_changelog
                generate_changelog({"count": 200}, self._ctx())
        self.assertEqual(captured.get("perPage"), 50)

    def test_default_count_is_20(self):
        captured = {}
        def mock_gh(tool, args):
            captured.update(args)
            return {"handled": True, "reply": json.dumps([])}

        with patch("app.agent.tools.schemas.mcp_tools._gh_repo", return_value=("owner", "repo", "")):
            with patch("app.agent.tools.schemas.mcp_tools._gh", side_effect=mock_gh):
                from app.agent.tools.schemas.mcp_tools import generate_changelog
                generate_changelog({}, self._ctx())
        self.assertEqual(captured.get("perPage"), 20)


if __name__ == "__main__":
    unittest.main()
