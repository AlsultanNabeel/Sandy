import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLOUD_DIR = ROOT / "cloud"
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

from app.agent.executor import execute_operational_action  # noqa: E402
from app.agent.executor.pending_execution import execute_pending_action  # noqa: E402
from app.agent.executor.helpers import is_cancellation  # noqa: E402
from app.agent.pending import create_pending_action  # noqa: E402
from app.utils.time import USER_TZ  # noqa: E402
from app.utils.user_profiles import set_active_user_profile  # noqa: E402

_OWNER = {"relation": "owner", "permissions": "all", "tone": "casual", "name": "Test"}
_TASK = {"id": "task-001", "text": "شراء حليب", "status": "needsAction"}
_SINGLE = {"status": "single", "tasks": [_TASK]}
_NOT_FOUND = {"status": "not_found", "tasks": []}


def _save(session, **kwargs):
    pass


def _noop_ai(*a, **kw):
    return None


def _call(action_type, params, session=None, msg="test"):
    return execute_operational_action(
        action_type, params,
        user_message=msg,
        normalized_user_message=msg,
        session=session if session is not None else {},
        session_file=None, mongo_db=None, tasks_file=None,
        create_chat_completion_fn=_noop_ai,
        save_session_fn=_save,
    )


class TaskActionsTests(unittest.TestCase):
    def setUp(self):
        set_active_user_profile(_OWNER)

    def tearDown(self):
        set_active_user_profile(None)

    @patch("app.agent.executor.task_handlers.resolve_task_references_for_write", return_value=_SINGLE)
    def test_complete_single_sets_pending(self, _):
        session = {}
        r = _call("task", {"action": "complete", "reference": "شراء حليب"}, session)
        self.assertTrue(r["handled"])
        self.assertEqual(session["pending_action"]["action"], "complete")
        self.assertEqual(session["pending_action"]["task_id"], "task-001")

    @patch("app.agent.executor.task_handlers.resolve_task_references_for_write", return_value=_NOT_FOUND)
    def test_complete_not_found_asks_clarification(self, _):
        session = {}
        r = _call("task", {"action": "complete", "reference": "مهمة وهمية"}, session)
        self.assertTrue(r["handled"])
        self.assertIn("pending_action", session)
        self.assertEqual(session["pending_action"]["action"], "clarify_task_write")

    @patch("app.agent.executor.task_handlers.resolve_task_reference_for_write",
           return_value={"status": "single", "task": _TASK})
    def test_delete_single_sets_pending(self, _):
        session = {}
        r = _call("task", {"action": "delete", "reference": "شراء حليب"}, session)
        self.assertTrue(r["handled"])
        self.assertEqual(session["pending_action"]["action"], "delete_one")
        self.assertEqual(session["pending_action"]["task_id"], "task-001")

    @patch("app.agent.executor.task_handlers.load_tasks", return_value=[_TASK])
    @patch("app.agent.executor.task_handlers.add_task", return_value="task-002")
    def test_create_task_returns_reply(self, mock_add, _):
        r = _call("task", {"action": "create", "text": "مهمة جديدة"})
        self.assertTrue(r["handled"])
        mock_add.assert_called_once()

    @patch("app.features.tasks_store.load_tasks", return_value=[_TASK])
    @patch("app.agent.executor.task_handlers.resolve_task_references_for_write", return_value=_SINGLE)
    def test_append_note_success(self, _, __):
        r = _call("task", {"action": "append_note", "reference": "حليب", "notes": "من السوبر ماركت"})
        self.assertTrue(r["handled"])

    @patch("app.features.tasks_store.load_tasks", return_value=[_TASK])
    @patch("app.agent.executor.task_handlers.resolve_task_references_for_write", return_value=_SINGLE)
    def test_replace_note_success(self, _, __):
        r = _call("task", {"action": "replace_note", "reference": "حليب", "notes": "ملاحظة جديدة"})
        self.assertTrue(r["handled"])

    @patch("app.agent.executor.task_handlers.resolve_task_references_for_write",
           return_value={"status": "ambiguous", "matches": [_TASK, {"id": "task-002", "text": "شراء خبز"}]})
    def test_complete_ambiguous_asks_choice(self, _):
        session = {}
        r = _call("task", {"action": "complete", "reference": "شراء"}, session)
        self.assertTrue(r["handled"])
        self.assertEqual(session["pending_action"]["action"], "clarify_task_choice")
        self.assertIn("لقيت أكثر من مهمة", r["reply"])

    def test_invalid_action_rejected(self):
        r = _call("task", {"action": "hack"})
        self.assertTrue(r["handled"])
        self.assertIn("غير صالح", r["reply"])

    @patch("app.agent.executor.task_handlers.build_task_display", return_value=("📋 لا توجد مهام", {}))
    def test_list_returns_reply(self, _):
        r = _call("task", {"action": "list"})
        self.assertTrue(r["handled"])
        self.assertIn("مهام", r["reply"])

    @patch("app.agent.executor.deps.complete_task", return_value=True)
    @patch("app.agent.executor.deps.delete_sandy_reminder_by_task_id")
    def test_confirmed_complete_one_executes_task_id(self, mock_delete_reminder, mock_complete):
        session = {
            "pending_action": create_pending_action({
                "type": "task",
                "action": "complete",
                "task_id": _TASK["id"],
                "text": _TASK["text"],
                "confirmation_status": "pending",
            })
        }

        result = execute_pending_action(
            user_message="تمام",
            session=session,
            session_file=None,
            mongo_db=None,
            tasks_file=None,
            save_session_fn=_save,
        )

        self.assertTrue(result["handled"])
        self.assertIn("المهمة كمكتملة", result["reply"])
        mock_complete.assert_called_once_with(_TASK["id"], mongo_db=None, tasks_file=None)
        mock_delete_reminder.assert_called_once_with(_TASK["id"])
        self.assertIsNone(session.get("pending_action"))

    @patch("app.agent.executor.deps.delete_task", return_value=True)
    @patch("app.agent.executor.deps.delete_sandy_reminder_by_task_id")
    def test_confirmed_delete_one_executes_task_id(self, mock_delete_reminder, mock_delete):
        session = {
            "pending_action": create_pending_action({
                "type": "task",
                "action": "delete_one",
                "task_id": _TASK["id"],
                "text": _TASK["text"],
                "confirmation_status": "pending",
            })
        }

        result = execute_pending_action(
            user_message="اه",
            session=session,
            session_file=None,
            mongo_db=None,
            tasks_file=None,
            save_session_fn=_save,
        )

        self.assertTrue(result["handled"])
        self.assertIn("حذفت المهمة", result["reply"])
        mock_delete.assert_called_once_with(_TASK["id"], mongo_db=None, tasks_file=None)
        mock_delete_reminder.assert_called_once_with(_TASK["id"])
        self.assertIsNone(session.get("pending_action"))


class ReminderActionsTests(unittest.TestCase):
    def setUp(self):
        set_active_user_profile(_OWNER)

    def tearDown(self):
        set_active_user_profile(None)

    @patch("app.agent.executor.reminder_handlers.add_reminder", return_value={"success": True})
    def test_reminder_future_creates_successfully(self, mock_add):
        future = (datetime.now(USER_TZ) + timedelta(hours=2)).isoformat()
        r = _call("reminder", {"action": "create", "text": "اتصل بالطبيب", "remind_at_iso": future})
        self.assertTrue(r["handled"])
        mock_add.assert_called_once()

    def test_reminder_past_time_blocked(self):
        past = (datetime.now(USER_TZ) - timedelta(hours=1)).isoformat()
        r = _call("reminder", {"action": "create", "text": "موعد فات", "remind_at_iso": past})
        self.assertTrue(r["handled"])
        self.assertIn("ماضي", r["reply"])

    @patch("app.agent.executor.reminder_handlers.load_reminders", return_value=[
        {"task_id": "t-001", "text": "اتصل بالطبيب", "remind_at": "2099-01-01T10:00:00+03:00"}
    ])
    @patch("app.agent.executor.reminder_handlers.delete_sandy_reminder_by_task_id", return_value=1)
    def test_reminder_delete_by_text(self, mock_del, _):
        r = _call("reminder", {"action": "delete", "text": "اتصل بالطبيب"})
        self.assertTrue(r["handled"])
        mock_del.assert_called_once_with("t-001")

    @patch("app.agent.executor.reminder_handlers.load_reminders", return_value=[])
    def test_reminder_list_empty(self, _):
        r = _call("reminder", {"action": "list"})
        self.assertTrue(r["handled"])


class IsCancellationTests(unittest.TestCase):
    def test_arabic_no(self):
        self.assertTrue(is_cancellation("لا"))
        self.assertTrue(is_cancellation("لأ"))

    def test_arabic_cancel(self):
        self.assertTrue(is_cancellation("إلغاء"))
        self.assertTrue(is_cancellation("الغيها"))

    def test_english_cancel(self):
        self.assertTrue(is_cancellation("cancel"))
        self.assertTrue(is_cancellation("no"))

    def test_not_cancellation(self):
        self.assertFalse(is_cancellation("نعم"))
        self.assertFalse(is_cancellation("تمام"))
        self.assertFalse(is_cancellation("ok"))
        self.assertFalse(is_cancellation("ضيفي مهمة"))


class PendingCancellationFlowTests(unittest.TestCase):
    def setUp(self):
        set_active_user_profile(_OWNER)

    def tearDown(self):
        set_active_user_profile(None)

    def test_cancellation_clears_pending_reminder(self):
        from app.agent.executor.pending_execution import _handle_confirm_remind_at
        session = {
            "pending_action": create_pending_action({
                "type": "reminder", "action": "pending_confirmation",
                "text": "اتصل", "remind_at_iso": "2099-01-01T10:00:00+03:00",
                "confirmation_status": "pending",
            })
        }
        pending = session["pending_action"]
        result = _handle_confirm_remind_at(
            "لا", pending,
            session=session, session_file=None, mongo_db=None,
            save_session_fn=_save,
        )
        self.assertTrue(result.get("handled"))
        self.assertIsNone(session.get("pending_action"))


class ResearchPipelineTests(unittest.TestCase):
    def setUp(self):
        set_active_user_profile(_OWNER)

    def tearDown(self):
        set_active_user_profile(None)

    def test_research_returns_summary(self):
        from app.features.research import execute_web_research

        def _mock_exa(query, **kwargs):
            return [{"title": "أخبار اليوم", "url": "https://example.com", "text": "خبر مهم", "score": 0.9}]

        def _mock_ai(messages, **kwargs):
            return "ملخص النتائج"

        reply, items = execute_web_research(
            query="أخبار السعودية",
            user_message="أخبار السعودية",
            research_type="news",
            search_exa_fn=_mock_exa,
            create_chat_completion_fn=_mock_ai,
        )
        self.assertIsInstance(reply, str)
        self.assertIsInstance(items, list)


if __name__ == "__main__":
    unittest.main()
