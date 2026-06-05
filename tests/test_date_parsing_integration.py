"""Integration tests for new date parsing and pending_action confirmation flow."""

import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, 'cloud')

from app.features.google_calendar import parse_reminder_time_ai
from app.utils.arabic_days import (
    parse_numeric_date,
    parse_relative_simple,
    parse_date_from_text,
)
from app.utils.time import USER_TZ


class TestDateParsingDeterministic(unittest.TestCase):
    """Test deterministic date parsing without AI."""

    def test_numeric_date_yyyy_mm_dd_slash(self):
        """YYYY/MM/DD format."""
        iso = parse_numeric_date("2026/06/15")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 15)
        self.assertEqual(dt.hour, 9)  # default hour

    def test_numeric_date_dd_mm_yyyy_slash(self):
        """DD/MM/YYYY format."""
        iso = parse_numeric_date("15/06/2026")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 15)

    def test_numeric_date_yyyy_mm_dd_dash(self):
        """YYYY-MM-DD format."""
        iso = parse_numeric_date("2026-06-15")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 15)

    def test_numeric_date_compact_yyyymmdd(self):
        """Compact YYYYMMDD format."""
        iso = parse_numeric_date("20260615")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 15)

    def test_relative_date_after_n_days(self):
        """بعد X يوم format."""
        iso = parse_relative_simple("بعد 3 أيام")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        now = datetime.now(USER_TZ)
        expected = now.date() + timedelta(days=3)
        self.assertEqual(dt.date(), expected)

    def test_relative_date_tomorrow(self):
        """بكرا / غدا formats."""
        iso = parse_relative_simple("بكرا")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        now = datetime.now(USER_TZ)
        expected = now.date() + timedelta(days=1)
        self.assertEqual(dt.date(), expected)

    def test_relative_date_after_weeks(self):
        """بعد أسبوع format."""
        iso = parse_relative_simple("بعد أسبوع")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        now = datetime.now(USER_TZ)
        expected = now.date() + timedelta(weeks=1)
        self.assertEqual(dt.date(), expected)

    def test_combined_parse_day_name(self):
        """parse_date_from_text: day name (highest priority)."""
        iso = parse_date_from_text("الجمعة")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        # should be next Friday
        self.assertEqual(dt.weekday(), 4)  # Friday

    def test_combined_parse_numeric(self):
        """parse_date_from_text: numeric fallback."""
        iso = parse_date_from_text("15/06/2026")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.day, 15)

    def test_combined_parse_relative(self):
        """parse_date_from_text: relative fallback."""
        iso = parse_date_from_text("بعد 2 أيام")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        now = datetime.now(USER_TZ)
        expected = now.date() + timedelta(days=2)
        self.assertEqual(dt.date(), expected)


class TestParseReminderTimeAIJsonOutput(unittest.TestCase):
    """Test parse_reminder_time_ai JSON output mode."""

    def test_no_ai_function_numeric_date_suggestion(self):
        """Without AI, should return suggested_iso for numeric dates."""
        result = parse_reminder_time_ai("2026/06/15", create_chat_completion_fn=None, return_json=True)
        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("reason"), "no_completion_fn")
        self.assertIsNotNone(result.get("suggested_iso"))

    def test_no_ai_function_relative_date_suggestion(self):
        """Without AI, should suggest for relative dates."""
        result = parse_reminder_time_ai("بعد 5 أيام", create_chat_completion_fn=None, return_json=True)
        self.assertFalse(result.get("success"))
        self.assertIsNotNone(result.get("suggested_iso"))

    def test_no_ai_function_backward_compatible(self):
        """Without AI and return_json=False, should return None (old behavior)."""
        result = parse_reminder_time_ai("منتصف الأسبوع القادم", create_chat_completion_fn=None, return_json=False)
        self.assertIsNone(result)

    def test_json_output_structure(self):
        """Check JSON output has all required fields."""
        result = parse_reminder_time_ai("15/06/2026", create_chat_completion_fn=None, return_json=True)
        self.assertIn("success", result)
        self.assertIn("remind_at_iso", result)
        self.assertIn("intent", result)
        self.assertIn("reason", result)
        self.assertIn("original_text", result)
        self.assertIn("suggested_iso", result)


class TestPendingActionConfirmationFlow(unittest.TestCase):
    """Test the flow of suggesting a date and waiting for user confirmation."""

    def test_confirm_reminder_at_action_structure(self):
        """Verify pending_action structure for reminder confirmation."""
        pending = {
            "type": "reminder",
            "action": "confirm_remind_at",
            "reminder_text": "اجتماع مهم",
            "suggested_iso": "2026-05-15T09:00:00+03:00",
            "confirmation_status": "pending",
        }
        self.assertEqual(pending["type"], "reminder")
        self.assertEqual(pending["action"], "confirm_remind_at")
        self.assertIsNotNone(pending["suggested_iso"])

    def test_confirm_task_due_date_action_structure(self):
        """Verify pending_action structure for task due date confirmation."""
        pending = {
            "type": "task",
            "action": "confirm_task_due_date",
            "task_text": "إنهاء المشروع",
            "suggested_iso": "2026-06-20T09:00:00+03:00",
            "confirmation_status": "pending",
        }
        self.assertEqual(pending["type"], "task")
        self.assertEqual(pending["action"], "confirm_task_due_date")
        self.assertIsNotNone(pending["suggested_iso"])

    def test_confirm_update_with_time_action_structure(self):
        """Verify pending_action structure for calendar event time confirmation."""
        pending = {
            "type": "calendar",
            "action": "confirm_update_with_time",
            "event_id": "event123",
            "suggested_start_iso": "2026-05-20T14:00:00+03:00",
            "title_display": "اجتماع العمل",
            "confirmation_status": "pending",
        }
        self.assertEqual(pending["type"], "calendar")
        self.assertEqual(pending["action"], "confirm_update_with_time")
        self.assertIsNotNone(pending["event_id"])


if __name__ == "__main__":
    unittest.main()
