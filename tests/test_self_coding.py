"""Tests for Self-Coding Agent (SA1-SA9).

Focus on the pure-logic parts that don't need network:
- Line-ending detection + preservation (SA2/SA3)
- Splice math (SA3)
- Patch validation (SA3)
- Task state machine (SA9)
- Resume hook intent classification

Network-bound helpers (SA1 grep, SA4 branch creation, CI polling) are tested
via mocked github_api responses elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add cloud/ to sys.path so `app.*` imports resolve
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLOUD = _REPO_ROOT / "cloud"
if str(_CLOUD) not in sys.path:
    sys.path.insert(0, str(_CLOUD))


# ── SA2: Line ending detection ───────────────────────────────────────────────-
class TestLineEndingDetection:
    def test_lf_only(self):
        from app.agent.self_coding.repo_view import detect_line_ending
        assert detect_line_ending("a\nb\nc\n") == "\n"

    def test_crlf(self):
        from app.agent.self_coding.repo_view import detect_line_ending
        assert detect_line_ending("a\r\nb\r\nc\r\n") == "\r\n"

    def test_mixed_dominant_crlf(self):
        from app.agent.self_coding.repo_view import detect_line_ending
        # Any CRLF presence wins (Windows behavior)
        assert detect_line_ending("a\r\nb\nc\n") == "\r\n"

    def test_old_mac_cr(self):
        from app.agent.self_coding.repo_view import detect_line_ending
        # Pure CR (no LF)
        assert detect_line_ending("a\rb\rc\r") == "\r"

    def test_empty_string_defaults_to_lf(self):
        from app.agent.self_coding.repo_view import detect_line_ending
        assert detect_line_ending("") == "\n"


class TestSplitJoinPreserve:
    def test_lf_roundtrip(self):
        from app.agent.self_coding.repo_view import (
            split_lines_preserve,
            join_lines_with_ending,
        )
        text = "alpha\nbeta\ngamma\n"
        lines = split_lines_preserve(text)
        assert lines == ["alpha", "beta", "gamma", ""]
        assert join_lines_with_ending(lines, "\n") == text

    def test_crlf_roundtrip(self):
        from app.agent.self_coding.repo_view import (
            split_lines_preserve,
            join_lines_with_ending,
        )
        text = "alpha\r\nbeta\r\ngamma\r\n"
        lines = split_lines_preserve(text)
        assert lines == ["alpha", "beta", "gamma", ""]
        assert join_lines_with_ending(lines, "\r\n") == text

    def test_no_trailing_newline_roundtrip(self):
        from app.agent.self_coding.repo_view import (
            split_lines_preserve,
            join_lines_with_ending,
        )
        text = "alpha\nbeta\ngamma"
        lines = split_lines_preserve(text)
        assert lines == ["alpha", "beta", "gamma"]
        assert join_lines_with_ending(lines, "\n") == text

    def test_single_line(self):
        from app.agent.self_coding.repo_view import (
            split_lines_preserve,
            join_lines_with_ending,
        )
        assert split_lines_preserve("only") == ["only"]
        assert join_lines_with_ending(["only"], "\n") == "only"


# ── SA3: Splice math ──────────────────────────────────────────────────────────
class TestApplySplice:
    def test_replace_middle(self):
        from app.agent.self_coding.repo_patch import _apply_splice
        # Replace line 2 with two new lines
        result = _apply_splice(["a", "b", "c"], 2, 2, ["B1", "B2"])
        assert result == ["a", "B1", "B2", "c"]

    def test_replace_first_line(self):
        from app.agent.self_coding.repo_patch import _apply_splice
        result = _apply_splice(["a", "b", "c"], 1, 1, ["X"])
        assert result == ["X", "b", "c"]

    def test_replace_last_line(self):
        from app.agent.self_coding.repo_patch import _apply_splice
        result = _apply_splice(["a", "b", "c"], 3, 3, ["Z"])
        assert result == ["a", "b", "Z"]

    def test_replace_range_with_fewer(self):
        from app.agent.self_coding.repo_patch import _apply_splice
        # Replace lines 2-4 with one line
        result = _apply_splice(["a", "b", "c", "d", "e"], 2, 4, ["NEW"])
        assert result == ["a", "NEW", "e"]

    def test_replace_range_with_more(self):
        from app.agent.self_coding.repo_patch import _apply_splice
        result = _apply_splice(["a", "b", "c"], 2, 2, ["X", "Y", "Z"])
        assert result == ["a", "X", "Y", "Z", "c"]

    def test_delete_lines_via_empty_new(self):
        from app.agent.self_coding.repo_patch import _apply_splice
        # Replace lines 2-3 with empty list = delete them
        result = _apply_splice(["a", "b", "c", "d"], 2, 3, [])
        assert result == ["a", "d"]


class TestNormalizeNewLines:
    def test_strips_trailing_lf(self):
        from app.agent.self_coding.repo_patch import _normalize_new_lines
        assert _normalize_new_lines(["a\n", "b\n"]) == ["a", "b"]

    def test_strips_trailing_crlf(self):
        from app.agent.self_coding.repo_patch import _normalize_new_lines
        assert _normalize_new_lines(["a\r\n", "b\r\n"]) == ["a", "b"]

    def test_preserves_internal_whitespace(self):
        from app.agent.self_coding.repo_patch import _normalize_new_lines
        assert _normalize_new_lines(["  indented", "\ttabbed"]) == ["  indented", "\ttabbed"]

    def test_none_becomes_empty(self):
        from app.agent.self_coding.repo_patch import _normalize_new_lines
        assert _normalize_new_lines([None, "x"]) == ["", "x"]


class TestPatchValidation:
    def test_rejects_zero_start_line(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        err = _validate_patch_input("f.py", 0, 1, ["a"], 10)
        assert err is not None and "≥1" in err

    def test_rejects_reversed_range(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        err = _validate_patch_input("f.py", 5, 3, ["a"], 10)
        assert err is not None

    def test_rejects_start_past_end_of_file(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        err = _validate_patch_input("f.py", 100, 100, ["a"], 10)
        assert err is not None

    def test_rejects_end_past_end_of_file(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        err = _validate_patch_input("f.py", 1, 100, ["a"], 10)
        assert err is not None

    def test_rejects_internal_newline_in_new_line(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        err = _validate_patch_input("f.py", 1, 1, ["a\nb"], 10)
        assert err is not None and "newline" in err

    def test_accepts_valid(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        assert _validate_patch_input("f.py", 1, 3, ["a", "b"], 10) is None

    def test_rejects_too_long_line(self):
        from app.agent.self_coding.repo_patch import _validate_patch_input
        from app.agent.self_coding import repo_patch
        long_line = "x" * (repo_patch._MAX_NEW_LINE_LEN + 1)
        err = _validate_patch_input("f.py", 1, 1, [long_line], 10)
        assert err is not None and "أطول" in err


# ── SA4: Branch name sanitization ─────────────────────────────────────────────
class TestBranchNaming:
    def test_clean_id(self):
        from app.agent.self_coding.branch_ops import task_branch_name
        assert task_branch_name("abc123") == "sandy-task-abc123"

    def test_strips_unsafe_chars(self):
        from app.agent.self_coding.branch_ops import task_branch_name
        # Unsafe chars become -
        assert task_branch_name("a/b c$d") == "sandy-task-a-b-c-d"

    def test_truncates_long_id(self):
        from app.agent.self_coding.branch_ops import task_branch_name
        result = task_branch_name("x" * 200)
        assert len(result) <= len("sandy-task-") + 64


# ── SA5: CI conclusion priority ──────────────────────────────────────────────
class TestCIConclusionPriority:
    def test_failure_beats_success(self):
        from app.agent.self_coding.ci_status import _pick_worst_conclusion
        assert _pick_worst_conclusion(["success", "failure"]) == "failure"

    def test_timed_out_beats_cancelled(self):
        from app.agent.self_coding.ci_status import _pick_worst_conclusion
        assert _pick_worst_conclusion(["cancelled", "timed_out"]) == "timed_out"

    def test_all_success(self):
        from app.agent.self_coding.ci_status import _pick_worst_conclusion
        assert _pick_worst_conclusion(["success", "success"]) == "success"

    def test_empty_returns_none(self):
        from app.agent.self_coding.ci_status import _pick_worst_conclusion
        assert _pick_worst_conclusion([]) is None


# ── SA9: Task state machine ───────────────────────────────────────────────────
class TestTaskPayload:
    def test_build_minimal_payload(self):
        from app.agent.self_coding import task_state
        payload = task_state.build_task_payload(
            task_type=task_state.TYPE_PROJECT_BUILDER,
            description="test project",
        )
        assert payload["type"] == task_state.TYPE_PROJECT_BUILDER
        assert payload["status"] == task_state.STATUS_QUEUED
        assert payload["attempts"] == 0
        assert payload["task_id"].startswith("sa_")
        assert payload["description"] == "test project"
        assert payload["patched_files"] == []

    def test_task_id_uniqueness(self):
        from app.agent.self_coding.task_state import new_task_id
        ids = {new_task_id() for _ in range(100)}
        assert len(ids) == 100


# ── Resume hook ───────────────────────────────────────────────────────────────
class TestResumeIntentClassification:
    def test_agree_short(self):
        from app.agent.self_coding.resume_hook import _classify_intent
        assert _classify_intent("اه") == "agree"
        assert _classify_intent("yes") == "agree"
        assert _classify_intent("OK") == "agree"

    def test_cancel(self):
        from app.agent.self_coding.resume_hook import _classify_intent
        assert _classify_intent("لا") == "cancel"
        assert _classify_intent("cancel") == "cancel"
        assert _classify_intent("وقف") == "cancel"

    def test_proposal_phrase_treated_as_propose(self):
        # M4: 'custom' was split into 'question' (asks) and 'propose' (proposes).
        # A real proposal — declarative, longer than 3 words → propose
        from app.agent.self_coding.resume_hook import _classify_intent
        assert _classify_intent("اعملي validation function للحقول كلها") == "propose"

    def test_question_treated_as_question(self):
        # M4: question marker (؟ or question word) opens a discussion.
        from app.agent.self_coding.resume_hook import _classify_intent
        assert _classify_intent("ليش هاد الحل؟") == "question"
        assert _classify_intent("في طريقة أبسط؟") == "question"

    def test_agree_in_short_phrase(self):
        from app.agent.self_coding.resume_hook import _classify_intent
        # 'اه تمام' = 2 words, includes agree token
        assert _classify_intent("اه تمام") == "agree"

    def test_empty_treated_as_propose(self):
        # M4: empty falls through to 'propose' (was 'custom' pre-M4)
        from app.agent.self_coding.resume_hook import _classify_intent
        assert _classify_intent("") == "propose"


# ── End-to-end: SA3 patch on simulated content ────────────────────────────────
class TestRepoPatchE2EMocked:
    """E2E test of SA3 with mocked github_api + Redis."""

    def test_successful_patch_preserves_crlf_endings(self):
        """A CRLF file patched in the middle must remain CRLF."""
        from app.agent.self_coding import repo_patch

        original = "a\r\nb\r\nc\r\nd\r\n"
        mock_view = {
            "ok": True,
            "sha": "abc123",
            "lines": ["a", "b", "c", "d", ""],
            "full_content": original,
            "line_ending": "\r\n",
            "size": len(original),
            "path": "f.py",
        }
        captured = {}

        def _fake_update(file_path, *, new_content, sha, branch, message, repo=None):
            captured["new_content"] = new_content
            captured["sha"] = sha
            return {"ok": True, "status": 200, "commit_sha": "newsha", "new_blob_sha": "newblob"}

        with patch("app.agent.self_coding.repo_patch.get_cached_or_fetch", return_value=mock_view), \
             patch("app.agent.self_coding.repo_patch.github_api.update_file", side_effect=_fake_update), \
               patch("app.agent.self_coding.repo_patch.invalidate_file_cache"), \
               patch("app.agent.self_coding.repo_patch.validate_written_paths", return_value=(True, "")):
            result = repo_patch.repo_apply_patch(
                "f.py",
                2,
                2,
                ["B-new"],
                branch="feature",
            )

        assert result["ok"] is True
        assert result["commit_sha"] == "newsha"
        # CRITICAL: line endings preserved as CRLF
        assert captured["new_content"] == "a\r\nB-new\r\nc\r\nd\r\n"

    def test_409_triggers_reload_retry(self):
        from app.agent.self_coding import repo_patch

        mock_view_v1 = {
            "ok": True,
            "sha": "old_sha",
            "lines": ["a", "b", "c", ""],
            "full_content": "a\nb\nc\n",
            "line_ending": "\n",
            "size": 6,
            "path": "f.py",
        }
        mock_view_v2 = {**mock_view_v1, "sha": "fresh_sha"}

        view_calls = {"count": 0}

        def _fake_view(file_path, **kwargs):
            view_calls["count"] += 1
            return mock_view_v1 if view_calls["count"] == 1 else mock_view_v2

        update_calls = {"count": 0}

        def _fake_update(file_path, *, new_content, sha, branch, message, repo=None):
            update_calls["count"] += 1
            if update_calls["count"] == 1:
                return {"ok": False, "status": 409, "error": "conflict"}
            return {"ok": True, "status": 200, "commit_sha": "newsha", "new_blob_sha": "newblob"}

        with patch("app.agent.self_coding.repo_patch.get_cached_or_fetch", side_effect=_fake_view), \
             patch("app.agent.self_coding.repo_patch.github_api.update_file", side_effect=_fake_update), \
               patch("app.agent.self_coding.repo_patch.invalidate_file_cache"), \
               patch("app.agent.self_coding.repo_patch.validate_written_paths", return_value=(True, "")):
            result = repo_patch.repo_apply_patch(
                "f.py",
                2,
                2,
                ["B"],
                branch="feature",
            )

        assert result["ok"] is True
        assert result["retried"] is True
        assert update_calls["count"] == 2
        assert view_calls["count"] == 2

    def test_no_op_patch_returns_error(self):
        from app.agent.self_coding import repo_patch

        mock_view = {
            "ok": True,
            "sha": "abc",
            "lines": ["a", "b", "c", ""],
            "full_content": "a\nb\nc\n",
            "line_ending": "\n",
            "size": 6,
            "path": "f.py",
        }
        with patch("app.agent.self_coding.repo_patch.get_cached_or_fetch", return_value=mock_view), \
             patch("app.agent.self_coding.repo_patch.github_api.update_file") as mock_update:
            result = repo_patch.repo_apply_patch(
                "f.py",
                2,
                2,
                ["b"],  # same as original line 2
                branch="feature",
            )
        # SA3 short-circuits — should never call update_file
        mock_update.assert_not_called()
        assert result["ok"] is False
        assert "no-op" in result["error"]


class TestPostWriteValidation:
    def test_create_new_file_runs_validator(self):
        from app.agent.self_coding import repo_create

        validator_calls = []

        def _fake_validate(paths):
            validator_calls.append(list(paths))
            return True, ""

        with patch("app.agent.self_coding.repo_create.github_api.get_file_contents", return_value={"ok": False, "status": 404}), \
             patch("app.agent.self_coding.repo_create.github_api.create_file", return_value={"ok": True, "commit_sha": "abc123"}), \
             patch("app.agent.self_coding.repo_create.invalidate_file_cache"), \
             patch("app.agent.self_coding.repo_create.validate_written_paths", side_effect=_fake_validate), \
             patch("app.agent.self_coding.repo_create._record_task_progress"):
            result = repo_create.repo_create_or_replace(
                "cloud/app/generated_test.txt",
                "hello\n",
                branch="feature",
            )

        assert result["ok"] is True
        assert validator_calls == [["cloud/app/generated_test.txt"]]

    def test_patch_runs_validator(self):
        from app.agent.self_coding import repo_patch

        validator_calls = []

        def _fake_validate(paths):
            validator_calls.append(list(paths))
            return True, ""

        mock_view = {
            "ok": True,
            "sha": "sha123",
            "lines": ["a", "b", "c", ""],
            "full_content": "a\nb\nc\n",
            "line_ending": "\n",
            "size": 6,
            "path": "cloud/app/generated_test.py",
        }

        with patch("app.agent.self_coding.repo_patch.get_cached_or_fetch", return_value=mock_view), \
             patch("app.agent.self_coding.repo_patch.github_api.update_file", return_value={"ok": True, "status": 200, "commit_sha": "newsha", "new_blob_sha": "blobsha"}), \
             patch("app.agent.self_coding.repo_patch.invalidate_file_cache"), \
             patch("app.agent.self_coding.repo_patch.validate_written_paths", side_effect=_fake_validate), \
             patch("app.agent.self_coding.repo_patch._record_task_progress"):
            result = repo_patch.repo_apply_patch(
                "cloud/app/generated_test.py",
                2,
                2,
                ["B"],
                branch="feature",
            )

        assert result["ok"] is True
        assert validator_calls == [["cloud/app/generated_test.py"]]

    def test_create_new_file_blocks_when_locked(self):
        from app.agent.self_coding import repo_create

        with patch("app.agent.self_coding.repo_create.sa_redis.file_lock_acquire", return_value=False):
            result = repo_create.repo_create_or_replace(
                "cloud/app/locked_test.txt",
                "hello\n",
                branch="feature",
            )

        assert result["ok"] is False
        assert "قيد التعديل" in result["error"]

    def test_patch_blocks_when_locked(self):
        from app.agent.self_coding import repo_patch

        with patch("app.agent.self_coding.repo_patch.sa_redis.file_lock_acquire", return_value=False):
            result = repo_patch.repo_apply_patch(
                "cloud/app/locked_test.py",
                1,
                1,
                ["x"],
                branch="feature",
            )

        assert result["ok"] is False
        assert "قيد التعديل" in result["error"]

    def test_patch_blocks_when_hash_changed(self):
        from app.agent.self_coding import repo_patch

        mock_view = {
            "ok": True,
            "sha": "current_sha",
            "lines": ["a", "b", "c", ""],
            "full_content": "a\nb\nc\n",
            "line_ending": "\n",
            "size": 6,
            "path": "cloud/app/hash_guard.py",
        }

        with patch("app.agent.self_coding.repo_patch.get_cached_or_fetch", return_value=mock_view), \
             patch("app.agent.self_coding.repo_patch.sa_redis.file_lock_acquire", return_value=True), \
             patch("app.agent.self_coding.repo_patch.sa_redis.file_lock_release"), \
             patch("app.agent.self_coding.repo_patch.github_api.update_file") as mock_update:
            result = repo_patch.repo_apply_patch(
                "cloud/app/hash_guard.py",
                2,
                2,
                ["B"],
                branch="feature",
                expected_sha="old_sha",
            )

        mock_update.assert_not_called()
        assert result["ok"] is False
        assert "hash تغيّر" in result["error"]

    def test_create_blocks_when_hash_changed(self):
        from app.agent.self_coding import repo_create

        with patch("app.agent.self_coding.repo_create.github_api.get_file_contents", return_value={"ok": True, "sha": "current_sha", "content": "old"}), \
             patch("app.agent.self_coding.repo_create.sa_redis.file_lock_acquire", return_value=True), \
             patch("app.agent.self_coding.repo_create.sa_redis.file_lock_release"), \
             patch("app.agent.self_coding.repo_create.github_api.update_file") as mock_update, \
             patch("app.agent.self_coding.repo_create.github_api.create_file") as mock_create:
            result = repo_create.repo_create_or_replace(
                "cloud/app/hash_guard.txt",
                "new content\n",
                branch="feature",
                expected_sha="old_sha",
            )

        mock_update.assert_not_called()
        mock_create.assert_not_called()
        assert result["ok"] is False
        assert "hash تغيّر" in result["error"]


class TestReadHashTracking:
    def test_read_file_records_sha(self):
        from app.agent.self_coding.coding_agent_tools import AgentContext, _do_read_file

        ctx = AgentContext(branch="feature")
        mock_fetched = {
            "ok": True,
            "sha": "sha123",
            "lines": ["a", "b", ""],
            "full_content": "a\nb\n",
            "line_ending": "\n",
            "size": 4,
            "path": "cloud/app/tracked.py",
        }
        mock_view = {
            "ok": True,
            "start_line": 1,
            "end_line": 2,
            "total_lines": 3,
            "snippet": "1: a\n2: b",
        }

        with patch("app.agent.self_coding.coding_agent_tools.repo_view.get_cached_or_fetch", return_value=mock_fetched), \
             patch("app.agent.self_coding.coding_agent_tools.repo_view.repo_view_lines", return_value=mock_view):
            out = _do_read_file({"path": "cloud/app/tracked.py", "line_range": "1-2"}, ctx)

        assert "cloud/app/tracked.py" in out
        assert ctx.file_read_shas["cloud/app/tracked.py"] == "sha123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
