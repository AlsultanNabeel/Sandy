"""Tests for cloud/app/features/calendar_time_parser.py"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from app.features.calendar_time_parser import parse_reminder_time_ai


def _make_completion(iso_str, success=True, intent="reminder"):
    """Return a mock create_chat_completion_fn that yields structured JSON."""
    def fn(**kwargs):
        payload = {
            "success": success,
            "remind_at_iso": iso_str,
            "intent": intent,
            "reason": "parsed",
            "original_text": "test",
        }
        msg = SimpleNamespace(content=json.dumps(payload))
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice])
    return fn


class TestNoCompletionFn:
    def test_returns_none_when_no_fn(self):
        result = parse_reminder_time_ai("بكرا الساعة 3", create_chat_completion_fn=None)
        assert result is None

    def test_return_json_when_no_fn(self):
        result = parse_reminder_time_ai(
            "بكرا الساعة 3",
            create_chat_completion_fn=None,
            return_json=True,
        )
        assert result["success"] is False
        assert result["reason"] == "no_completion_fn"

    def test_empty_message_returns_none(self):
        fn = _make_completion("2030-01-01T09:00:00")
        result = parse_reminder_time_ai("", create_chat_completion_fn=fn)
        assert result is None

    def test_none_message_returns_none(self):
        result = parse_reminder_time_ai(None, create_chat_completion_fn=None)
        assert result is None


class TestDeterministicDayPath:
    def test_arabic_weekday_resolved_without_ai(self):
        """resolve_day_name_to_iso returns a future date for named day — no AI call."""
        fn = MagicMock(side_effect=AssertionError("AI should not be called"))
        with patch(
            "app.features.calendar_time_parser.resolve_day_name_to_iso",
            return_value="2099-12-31T09:00:00+03:00",
        ):
            result = parse_reminder_time_ai("الاثنين", create_chat_completion_fn=fn)
        assert result is not None
        assert "2099" in result
        fn.assert_not_called()

    def test_deterministic_past_date_falls_through_to_ai(self):
        """If deterministic parse returns a past time, fall through to AI."""
        with patch(
            "app.features.calendar_time_parser.resolve_day_name_to_iso",
            return_value="2000-01-01T09:00:00+03:00",  # past
        ):
            fn = _make_completion("2099-06-01T09:00:00+03:00")
            result = parse_reminder_time_ai("الاثنين", create_chat_completion_fn=fn)
        assert result is not None


class TestAIPath:
    def test_successful_future_time_returned(self):
        fn = _make_completion("2099-06-15T10:00:00+03:00")
        result = parse_reminder_time_ai("بكرا الساعة 10", create_chat_completion_fn=fn)
        assert result is not None
        assert "2099" in result

    def test_past_time_returns_none(self):
        fn = _make_completion("2000-01-01T10:00:00+03:00")
        result = parse_reminder_time_ai("بكرا الساعة 10", create_chat_completion_fn=fn)
        assert result is None

    def test_success_false_returns_none(self):
        fn = _make_completion(None, success=False)
        result = parse_reminder_time_ai("شو رأيك", create_chat_completion_fn=fn)
        assert result is None

    def test_success_false_return_json(self):
        fn = _make_completion(None, success=False, intent="unknown")
        result = parse_reminder_time_ai("شو رأيك", create_chat_completion_fn=fn, return_json=True)
        assert result["success"] is False
        assert result["remind_at_iso"] is None

    def test_return_json_success(self):
        fn = _make_completion("2099-06-15T10:00:00+03:00")
        result = parse_reminder_time_ai("بكرا", create_chat_completion_fn=fn, return_json=True)
        assert result["success"] is True
        assert result["remind_at_iso"] is not None

    def test_exception_in_ai_returns_none(self):
        def bad_fn(**kwargs):
            raise RuntimeError("network error")

        result = parse_reminder_time_ai("بكرا", create_chat_completion_fn=bad_fn)
        assert result is None

    def test_exception_return_json(self):
        def bad_fn(**kwargs):
            raise RuntimeError("fail")

        result = parse_reminder_time_ai("بكرا", create_chat_completion_fn=bad_fn, return_json=True)
        assert result["success"] is False
        assert "fail" in result["reason"]

    def test_naive_iso_gets_localized(self):
        """Naive ISO (no tz) should be localised, not rejected."""
        fn = _make_completion("2099-06-15T10:00:00")  # no tz
        result = parse_reminder_time_ai("بكرا الساعة 10", create_chat_completion_fn=fn)
        assert result is not None
