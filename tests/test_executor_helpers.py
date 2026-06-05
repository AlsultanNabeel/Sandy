"""Tests for cloud/app/agent/executor/helpers.py"""

from app.agent.executor.helpers import (
    _has_visible_task_note,
    _is_quick_confirmation,
    _task_choice_index,
    _task_choice_pair_indexes,
    is_cancellation,
)


class TestTaskChoiceIndex:
    # ── Digit input ──────────────────────────────────────────────────────────
    def test_digit_1_returns_0(self):
        assert _task_choice_index("1") == 0

    def test_digit_2_returns_1(self):
        assert _task_choice_index("2") == 1

    def test_digit_5_returns_4(self):
        assert _task_choice_index("5") == 4

    def test_arabic_digit_1_returns_0(self):
        assert _task_choice_index("١") == 0

    def test_arabic_digit_3_returns_2(self):
        assert _task_choice_index("٣") == 2

    # ── Arabic ordinals ──────────────────────────────────────────────────────
    def test_first_word_arabic(self):
        assert _task_choice_index("الاول") == 0

    def test_second_word_arabic(self):
        assert _task_choice_index("الثاني") == 1

    def test_third_word_arabic(self):
        assert _task_choice_index("الثالث") == 2

    def test_fourth_word_arabic(self):
        assert _task_choice_index("الرابع") == 3

    def test_fifth_word_arabic(self):
        assert _task_choice_index("الخامس") == 4

    def test_tani_variant(self):
        assert _task_choice_index("تاني") == 1

    def test_talt_variant(self):
        assert _task_choice_index("التالت") == 2

    # ── رقم X pattern ────────────────────────────────────────────────────────
    def test_raqam_pattern(self):
        assert _task_choice_index("رقم 3") == 2

    # ── Edge cases ───────────────────────────────────────────────────────────
    def test_empty_returns_none(self):
        assert _task_choice_index("") is None

    def test_unknown_text_returns_none(self):
        assert _task_choice_index("كلام عشوائي") is None

    def test_exclamation_returns_zero(self):
        assert _task_choice_index("!") == 0


class TestTaskChoicePairIndexes:
    def test_pair_word_with_2_choices(self):
        assert _task_choice_pair_indexes("التنتين", 2) == [0, 1]

    def test_pair_word_with_3_choices_returns_none(self):
        assert _task_choice_pair_indexes("التنتين", 3) is None

    def test_non_pair_word_returns_none(self):
        assert _task_choice_pair_indexes("الاول", 2) is None

    def test_اتنين_variant(self):
        assert _task_choice_pair_indexes("اتنين", 2) == [0, 1]

    def test_empty_returns_none(self):
        assert _task_choice_pair_indexes("", 2) is None


class TestHasVisibleTaskNote:
    def test_empty_notes_returns_false(self):
        assert _has_visible_task_note({}) is False

    def test_none_notes_returns_false(self):
        assert _has_visible_task_note({"notes": None}) is False

    def test_normal_note_returns_true(self):
        assert _has_visible_task_note({"notes": "اتصل الساعة 3"}) is True

    def test_sandy_internal_only_returns_false(self):
        assert _has_visible_task_note({"notes": "[SANDY_REMINDER_ID: abc123]"}) is False

    def test_mixed_note_returns_true(self):
        assert _has_visible_task_note({"notes": "ملاحظة\n[SANDY_REMINDER_ID: abc]"}) is True

    def test_whitespace_only_returns_false(self):
        assert _has_visible_task_note({"notes": "   \n  "}) is False


class TestIsQuickConfirmation:
    def test_ahe_is_confirmation(self):
        assert _is_quick_confirmation("اه") is True

    def test_naam_is_confirmation(self):
        assert _is_quick_confirmation("نعم") is True

    def test_ok_is_confirmation(self):
        assert _is_quick_confirmation("ok") is True

    def test_tamam_is_confirmation(self):
        assert _is_quick_confirmation("تمام") is True

    def test_random_text_not_confirmation(self):
        assert _is_quick_confirmation("بكرا الساعة 3") is False

    def test_empty_not_confirmation(self):
        assert _is_quick_confirmation("") is False


class TestIsCancellation:
    def test_la_is_cancellation(self):
        assert is_cancellation("لا") is True

    def test_no_is_cancellation(self):
        assert is_cancellation("no") is True

    def test_cancel_is_cancellation(self):
        assert is_cancellation("cancel") is True

    def test_alga_is_cancellation(self):
        assert is_cancellation("الغ") is True

    def test_la_tahthef_is_cancellation(self):
        assert is_cancellation("لا تحذف") is True

    def test_ansa_is_cancellation(self):
        assert is_cancellation("انسى") is True

    def test_confirmation_not_cancellation(self):
        assert is_cancellation("اه تمام") is False

    def test_empty_not_cancellation(self):
        assert is_cancellation("") is False
