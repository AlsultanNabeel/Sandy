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
from app.utils.user_profiles import set_active_user_profile  # noqa: E402


def _noop_chat_completion(*args, **kwargs):
    return None


def _save_session(session, **kwargs):
    return None


_OWNER_PROFILE = {"relation": "owner", "permissions": "all", "tone": "casual", "name": "Test"}


class ExecutorRoutingTests(unittest.TestCase):
    def setUp(self):
        set_active_user_profile(_OWNER_PROFILE)

    def tearDown(self):
        set_active_user_profile(None)

    def _call(self, action_type, params, session=None):
        return execute_operational_action(
            action_type,
            params,
            user_message="test",
            normalized_user_message="test",
            session=session if session is not None else {},
            session_file=None,
            mongo_db=None,
            tasks_file=None,
            create_chat_completion_fn=_noop_chat_completion,
            save_session_fn=_save_session,
        )

    @patch("app.agent.executor.calendar_handlers.list_upcoming_events")
    def test_calendar_list_route(self, mock_list_upcoming_events):
        mock_list_upcoming_events.return_value = [
            {
                "summary": "موعد دكتور",
                "id": "abc123456789",
                "start": {"dateTime": "2026-04-24T09:00:00+02:00"},
            }
        ]

        result = self._call("calendar", {"action": "list"})

        self.assertTrue(result["handled"])
        self.assertIn("المواعيد القادمة", result["reply"])
        self.assertIn("موعد دكتور", result["reply"])

    def test_calendar_delete_multi_sets_pending_confirmation(self):
        session = {}

        result = self._call(
            "calendar",
            {"action": "delete_multi", "titles": ["اجتماع 1", "اجتماع 2"]},
            session=session,
        )

        self.assertTrue(result["handled"])
        self.assertEqual(session["pending_action"]["type"], "calendar")
        self.assertEqual(session["pending_action"]["action"], "confirm_delete_multi")
        self.assertEqual(session["pending_action"]["titles"], ["اجتماع 1", "اجتماع 2"])

    @patch("app.agent.executor.reminder_handlers.add_calendar_event")
    def test_reminder_create_route(self, mock_add_calendar_event):
        mock_add_calendar_event.return_value = {"success": True}

        result = self._call(
            "reminder",
            {
                "action": "create",
                "text": "رياضة",
                "remind_at_iso": "2027-01-01T20:00:00+02:00",
                "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=TH",
                "end_iso": "2027-06-01T20:00:00+02:00",
            },
        )

        self.assertTrue(result["handled"])
        self.assertIn("سجلت التذكير", result["reply"])
        sent_recurrence = mock_add_calendar_event.call_args.kwargs["recurrence"]
        self.assertIn("UNTIL=", sent_recurrence)

    @patch("app.agent.executor.reminder_handlers.load_reminders")
    def test_reminder_list_route(self, mock_load_reminders):
        mock_load_reminders.return_value = [
            {
                "text": "اشرب ماء",
                "remind_at": "2026-04-24T10:00:00+02:00",
                "is_recurring": False,
            }
        ]

        result = self._call("reminder", {"action": "list"})

        self.assertTrue(result["handled"])
        self.assertIn("اشرب ماء", result["reply"])

    @patch("app.tools.cost_tool.get_azure_cost", return_value={"provider": "Azure", "available": True, "spent": 12.5})
    @patch("app.tools.cost_tool.format_cost_report", return_value="cost report")
    def test_cost_route(self, mock_format_cost_report, mock_get_azure_cost):
        result = self._call("cost", {"provider": "azure"})

        self.assertTrue(result["handled"])
        self.assertEqual(result["reply"], "cost report")
        mock_get_azure_cost.assert_called_once()
        mock_format_cost_report.assert_called_once()

    @patch("app.agent.facade.briefing.build_morning_briefing", return_value="briefing text")
    def test_briefing_route(self, mock_build_briefing):
        session = {"sandy_state": {"home_city": "Riyadh"}}

        result = self._call("briefing", {}, session=session)

        self.assertTrue(result["handled"])
        self.assertEqual(result["reply"], "briefing text")
        self.assertEqual(session["last_briefing_date"], session["sandy_state"]["last_briefing_date"])
        mock_build_briefing.assert_called_once()

    @patch("app.features.google_places.search_places")
    @patch("app.features.google_places.format_places_for_reply", return_value="أماكن قريبة")
    def test_places_route(self, mock_format_places, mock_search_places):
        mock_search_places.return_value = [{"name": "Cafe"}]
        session = {}

        result = self._call("places", {"query": "coffee"}, session=session)

        self.assertTrue(result["handled"])
        self.assertEqual(result["reply"], "أماكن قريبة")
        self.assertEqual(session.get("last_search_results", {}).get("domain"), "places")
        mock_search_places.assert_called_once()
        mock_format_places.assert_called_once()

    def test_update_location_route(self):
        session = {}

        result = self._call("update_location", {"city": "Amman"}, session=session)

        self.assertTrue(result["handled"])
        self.assertEqual(session["home_city"], "Amman")
        self.assertEqual(session["sandy_state"]["home_city"], "Amman")

    def test_unknown_action_returns_unhandled(self):
        result = self._call("unknown_route", {})

        self.assertFalse(result["handled"])
        self.assertEqual(result["reply"], "")

    @patch("app.agent.executor.calendar_handlers.list_events_for_date_range")
    def test_calendar_list_today_route(self, mock_list_events):
        mock_list_events.return_value = [
            {
                "summary": "اجتماع مع الفريق",
                "id": "evt123",
                "start": {"dateTime": "2027-06-01T09:00:00+02:00"},
            }
        ]

        result = self._call("calendar", {"action": "list", "query": "today"})

        self.assertTrue(result["handled"])
        self.assertIn("مواعيد اليوم", result["reply"])
        self.assertIn("اجتماع مع الفريق", result["reply"])

    @patch("app.agent.executor.calendar_handlers.add_calendar_event")
    def test_calendar_add_route(self, mock_add):
        mock_add.return_value = {"success": True, "event_id": "abc", "link": "http://cal.example"}

        result = self._call(
            "calendar",
            {
                "action": "add",
                "title": "موعد طبيب",
                "start_iso": "2027-06-01T10:00:00+02:00",
                "location": "عيادة الدكتور أحمد",
            },
        )

        self.assertTrue(result["handled"])
        self.assertIn("موعد طبيب", result["reply"])
        mock_add.assert_called_once()

    @patch("app.agent.executor.calendar_handlers.find_calendar_event_by_title")
    def test_calendar_update_by_title_sets_pending(self, mock_find):
        mock_find.return_value = {
            "found": True,
            "event_id": "evt_abc",
            "summary": "اجتماع أسبوعي",
        }
        session = {}

        result = self._call(
            "calendar",
            {"action": "update", "title": "اجتماع أسبوعي", "start_iso": "2027-06-02T11:00:00+02:00"},
            session=session,
        )

        self.assertTrue(result["handled"])
        self.assertIn("pending_action", session)
        self.assertEqual(session["pending_action"]["action"], "confirm_update")
        self.assertEqual(session["pending_action"]["event_id"], "evt_abc")


    @patch("app.agent.executor.calendar_handlers.list_events_for_date_range")
    def test_calendar_list_tomorrow_named_query(self, mock_list_events):
        """query='tomorrow' must call list_events_for_date_range with a 24-hour window."""
        from datetime import datetime, timedelta
        from app.utils.time import USER_TZ

        mock_list_events.return_value = [
            {
                "summary": "موعد نظارة",
                "id": "evt_tom",
                "start": {"dateTime": "2027-06-02T11:00:00+02:00"},
            }
        ]

        result = self._call("calendar", {"action": "list", "query": "tomorrow"})

        self.assertTrue(result["handled"])
        self.assertIn("مواعيد بكرا", result["reply"])
        self.assertIn("موعد نظارة", result["reply"])

        # Verify the range passed is exactly tomorrow 00:00–23:59
        call_args = mock_list_events.call_args
        start_arg = call_args[0][0]
        end_arg   = call_args[0][1]
        tomorrow  = (datetime.now(USER_TZ) + timedelta(days=1)).date()
        self.assertTrue(start_arg.startswith(str(tomorrow)), f"start should be tomorrow: {start_arg}")
        self.assertIn("23:59", end_arg)

    @patch("app.agent.executor.calendar_handlers.list_events_for_date_range")
    def test_calendar_list_explicit_time_range(self, mock_list_events):
        """time_min / time_max from AI planner must be forwarded directly."""
        mock_list_events.return_value = []

        result = self._call(
            "calendar",
            {
                "action": "list",
                "time_min": "2027-05-01T00:00:00+02:00",
                "time_max": "2027-05-01T23:59:59+02:00",
            },
        )

        self.assertTrue(result["handled"])
        mock_list_events.assert_called_once_with(
            "2027-05-01T00:00:00+02:00",
            "2027-05-01T23:59:59+02:00",
            max_results=10,
        )


    @patch("app.agent.executor.task_handlers.load_tasks")
    @patch("app.agent.executor.task_handlers.parse_reminder_time_ai")
    def test_bulk_update_due_date_sets_pending(self, mock_parse_time, mock_load_tasks):
        future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+02:00")
        past   = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+02:00")
        past_z = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000Z")
        other_z = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%dT00:00:00.000Z")
        mock_parse_time.side_effect = [
            future,  # to_due_iso (first call)
            past,    # from_due_iso (second call)
        ]
        mock_load_tasks.return_value = [
            {"id": "t1", "text": "اتصل بالدكتور", "due": past_z},
            {"id": "t2", "text": "اشتر دواء",     "due": past_z},
            {"id": "t3", "text": "اجتماع مختلف",  "due": other_z},
        ]

        session = {}
        result = self._call(
            "task",
            {"action": "bulk_update_due_date", "from_due_text": "بكرا", "to_due_text": "الأربعاء"},
            session=session,
        )

        self.assertTrue(result["handled"])
        self.assertIn("pending_action", session)
        pending = session["pending_action"]
        self.assertEqual(pending["action"], "bulk_update_due_date")
        self.assertEqual(len(pending["tasks"]), 2)
        task_texts = [t["text"] for t in pending["tasks"]]
        self.assertIn("اتصل بالدكتور", task_texts)
        self.assertIn("اشتر دواء", task_texts)
        self.assertNotIn("اجتماع مختلف", task_texts)
        self.assertIn("بدي أؤجل 2 مهام", result["reply"])

    @patch("app.agent.executor.task_handlers.load_tasks")
    @patch("app.agent.executor.task_handlers.parse_reminder_time_ai")
    def test_bulk_update_due_date_no_matching_tasks(self, mock_parse_time, mock_load_tasks):
        future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+02:00")
        past   = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+02:00")
        mock_parse_time.side_effect = [
            future,
            past,
        ]
        mock_load_tasks.return_value = [
            {"id": "t1", "text": "مهمة ليس لها تاريخ", "due": ""},
        ]

        result = self._call(
            "task",
            {"action": "bulk_update_due_date", "from_due_text": "بكرا", "to_due_text": "الأربعاء"},
        )

        self.assertTrue(result["handled"])
        self.assertIn("ما في مهام مستحقة", result["reply"])


if __name__ == "__main__":
    unittest.main()