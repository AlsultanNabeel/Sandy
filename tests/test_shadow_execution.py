"""Tests for Shadow Execution (Task 38) — handle_shadow_draft_action only."""

from datetime import datetime, timedelta

from app.agent.shadow_execution import (
    handle_shadow_draft_action,
    SESSION_KEY,
)
from app.utils.time import USER_TZ


def _future_iso(minutes=30):
    return (datetime.now(USER_TZ) + timedelta(minutes=minutes)).isoformat()


class TestHandleShadowDraftAction:

    def test_stores_draft_in_session(self):
        session = {}
        params = {
            "draft_type": "email_draft",
            "preview": "رد على أحمد بخصوص الاجتماع",
            "confirm_action": {"type": "email", "params": {"action": "send"}},
        }
        result = handle_shadow_draft_action(params, session)

        assert result["handled"] is True
        assert SESSION_KEY in session
        assert session[SESSION_KEY]["draft_type"] == "email_draft"
        assert session[SESSION_KEY]["preview"] == "رد على أحمد بخصوص الاجتماع"

    def test_reply_contains_confirmation_prompt(self):
        session = {}
        params = {
            "draft_type": "task_plan",
            "preview": "قائمة مهام المشروع",
            "confirm_action": {"type": "task", "params": {}},
        }
        result = handle_shadow_draft_action(params, session)

        assert "جهّزت" in result["reply"]
        assert "تبيني" in result["reply"]

    def test_reply_contains_preview(self):
        session = {}
        params = {
            "draft_type": "email_draft",
            "preview": "مرحباً أحمد، شكراً على التواصل",
            "confirm_action": {},
        }
        result = handle_shadow_draft_action(params, session)
        assert "مرحباً أحمد" in result["reply"]

    def test_reply_contains_yes_no_instruction(self):
        session = {}
        params = {"draft_type": "general", "preview": "مسودة", "confirm_action": {}}
        result = handle_shadow_draft_action(params, session)
        assert "نعم" in result["reply"] or "لا" in result["reply"]

    def test_empty_params_returns_not_handled(self):
        session = {}
        result = handle_shadow_draft_action({}, session)
        assert result["handled"] is False

    def test_draft_has_ttl(self):
        session = {}
        params = {"draft_type": "general", "preview": "مسودة", "confirm_action": {"type": "chat"}}
        handle_shadow_draft_action(params, session)
        draft = session[SESSION_KEY]
        assert "expires_at" in draft
        exp = datetime.fromisoformat(draft["expires_at"].replace("Z", "+00:00"))
        assert exp > datetime.now(USER_TZ)

    def test_draft_type_labels(self):
        for draft_type, expected_label in [
            ("email_draft", "إيميل"),
            ("reply_draft", "رد"),
            ("task_plan", "مهام"),
        ]:
            session = {}
            params = {"draft_type": draft_type, "preview": "preview", "confirm_action": {"type": "x"}}
            result = handle_shadow_draft_action(params, session)
            assert expected_label in result["reply"]


class TestDispatchIntegration:
    """Test that dispatch.py correctly routes shadow_draft actions."""

    def test_shadow_draft_action_dispatched(self):
        from app.agent.executor.dispatch import execute_operational_action
        session = {}
        result = execute_operational_action(
            "shadow_draft",
            {"draft_type": "task_plan", "preview": "خطة المشروع", "confirm_action": {"type": "task"}},
            user_message="جهّزي خطة",
            normalized_user_message="جهزي خطة",
            session=session,
            session_file=None,
            mongo_db=None,
            tasks_file=None,
            create_chat_completion_fn=None,
            save_session_fn=None,
        )
        assert result["handled"] is True
        assert SESSION_KEY in session
