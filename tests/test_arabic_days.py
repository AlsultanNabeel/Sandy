"""Tests for cloud/app/utils/arabic_days.py

Covers every variant in DAY_NAME_TO_WEEKDAY and the helper utilities.
Convention: 0=Monday … 6=Sunday  (datetime.weekday()).
"""

import sys
import os
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cloud"))

from app.utils.arabic_days import (
    DAY_NAME_TO_WEEKDAY,
    WEEKDAY_TO_AR_NAME,
    find_day_in_text,
    has_explicit_time,
    next_weekday_date,
    parse_arabic_day_name,
    resolve_day_name_to_iso,
)

# Fixed reference date: Thursday 2026-04-30
_REF = date(2026, 4, 30)  # weekday() == 3 (Thursday)


class TestDayNameToWeekday(unittest.TestCase):
    """Every key in DAY_NAME_TO_WEEKDAY maps to the correct weekday integer."""

    def _assert_all(self, variants: list[str], expected: int) -> None:
        for v in variants:
            with self.subTest(variant=v):
                self.assertEqual(DAY_NAME_TO_WEEKDAY.get(v), expected,
                                 f"'{v}' should map to {expected}")

    def test_sunday_variants(self):
        self._assert_all(["أحد", "احد", "الأحد", "الاحد"], 6)

    def test_monday_variants(self):
        self._assert_all([
            "اثنين", "اتنين", "إثنين", "إتنين",
            "الاثنين", "الاتنين", "الإثنين", "الإتنين",
        ], 0)

    def test_tuesday_variants(self):
        self._assert_all([
            "ثلاثاء", "ثلاثا",
            "تلاتاء", "تلاتا",
            "تلاثاء", "تلاثا",
            "الثلاثاء", "الثلاثا",
            "التلاتاء", "التلاتا",
            "التلاثاء", "التلاثا",
        ], 1)

    def test_wednesday_variants(self):
        self._assert_all([
            "أربعاء", "اربعاء",
            "أربعا",  "اربعا",
            "الأربعاء", "الاربعاء",
            "الأربعا",  "الاربعا",
        ], 2)

    def test_thursday_variants(self):
        self._assert_all(["خميس", "الخميس"], 3)

    def test_friday_variants(self):
        self._assert_all(["جمعة", "جمعه", "الجمعة", "الجمعه"], 4)

    def test_saturday_variants(self):
        self._assert_all(["سبت", "السبت"], 5)

    def test_no_unknown_keys(self):
        for k, v in DAY_NAME_TO_WEEKDAY.items():
            self.assertIn(v, range(7), f"Weekday value {v} for '{k}' out of range")


class TestWeekdayToArName(unittest.TestCase):
    """WEEKDAY_TO_AR_NAME covers 0-6 and is consistent with DAY_NAME_TO_WEEKDAY."""

    def test_all_weekdays_present(self):
        for i in range(7):
            self.assertIn(i, WEEKDAY_TO_AR_NAME, f"Missing weekday {i}")

    def test_canonical_names(self):
        self.assertEqual(WEEKDAY_TO_AR_NAME[0], "الاثنين")
        self.assertEqual(WEEKDAY_TO_AR_NAME[1], "الثلاثاء")
        self.assertEqual(WEEKDAY_TO_AR_NAME[2], "الأربعاء")
        self.assertEqual(WEEKDAY_TO_AR_NAME[3], "الخميس")
        self.assertEqual(WEEKDAY_TO_AR_NAME[4], "الجمعة")
        self.assertEqual(WEEKDAY_TO_AR_NAME[5], "السبت")
        self.assertEqual(WEEKDAY_TO_AR_NAME[6], "الأحد")

    def test_reverse_roundtrip(self):
        """Every canonical name in WEEKDAY_TO_AR_NAME must be in DAY_NAME_TO_WEEKDAY."""
        for wd, name in WEEKDAY_TO_AR_NAME.items():
            self.assertIn(name, DAY_NAME_TO_WEEKDAY,
                          f"Canonical name '{name}' missing from DAY_NAME_TO_WEEKDAY")
            self.assertEqual(DAY_NAME_TO_WEEKDAY[name], wd)


class TestParseArabicDayName(unittest.TestCase):
    def test_exact_tokens(self):
        self.assertEqual(parse_arabic_day_name("الجمعة"), 4)
        self.assertEqual(parse_arabic_day_name("خميس"), 3)
        self.assertEqual(parse_arabic_day_name("الاربعا"), 2)
        self.assertEqual(parse_arabic_day_name("تلاتا"), 1)

    def test_strips_whitespace(self):
        self.assertEqual(parse_arabic_day_name("  السبت  "), 5)

    def test_unknown_returns_none(self):
        self.assertIsNone(parse_arabic_day_name("مرحبا"))
        self.assertIsNone(parse_arabic_day_name(""))
        self.assertIsNone(parse_arabic_day_name("يوم"))


class TestFindDayInText(unittest.TestCase):
    def test_finds_in_sentence(self):
        self.assertEqual(find_day_in_text("ذكريني الخميس بالاجتماع"), 3)
        self.assertEqual(find_day_in_text("أضيفي مهمة يوم الجمعة"), 4)
        self.assertEqual(find_day_in_text("اذكريني السبت"), 5)

    def test_finds_without_al(self):
        self.assertEqual(find_day_in_text("حطي تذكير خميس"), 3)
        self.assertEqual(find_day_in_text("أضيفي مهمة اثنين"), 0)
        self.assertEqual(find_day_in_text("ذكريني جمعه"), 4)

    def test_finds_colloquial(self):
        self.assertEqual(find_day_in_text("أضيفي مهمة التلاتا"), 1)
        self.assertEqual(find_day_in_text("ذكريني الاربعا"), 2)

    def test_strips_punctuation_from_token(self):
        self.assertEqual(find_day_in_text("ذكريني الجمعة،"), 4)
        self.assertEqual(find_day_in_text("(الأحد)"), 6)

    def test_no_day_returns_none(self):
        self.assertIsNone(find_day_in_text("أضيفي مهمة مراجعة الكود"))
        self.assertIsNone(find_day_in_text("ذكريني بعد بكرة"))
        self.assertIsNone(find_day_in_text(""))

    def test_returns_first_day_found(self):
        # Two days in same text — returns the first one found
        result = find_day_in_text("من الاثنين لحد الجمعة")
        self.assertIn(result, (0, 4))


class TestNextWeekdayDate(unittest.TestCase):
    # _REF = Thursday 2026-04-30  (weekday 3)

    def test_future_day_same_week(self):
        # Thu → next Sat is +2
        self.assertEqual(next_weekday_date(5, reference=_REF), date(2026, 5, 2))

    def test_wrap_to_next_week(self):
        # Thu → next Mon is +4
        self.assertEqual(next_weekday_date(0, reference=_REF), date(2026, 5, 4))

    def test_same_day_no_allow_today_goes_next_week(self):
        # Thu → Thu, but allow_today=False → +7
        self.assertEqual(next_weekday_date(3, reference=_REF), date(2026, 5, 7))

    def test_same_day_allow_today_returns_today(self):
        self.assertEqual(next_weekday_date(3, reference=_REF, allow_today=True), _REF)

    def test_sunday(self):
        # Thu → Sun is +3
        self.assertEqual(next_weekday_date(6, reference=_REF), date(2026, 5, 3))

    def test_uses_user_tz_when_no_reference(self):
        # Smoke test — just confirm it returns a valid date
        result = next_weekday_date(2)
        self.assertIsInstance(result, date)


class TestHasExplicitTime(unittest.TestCase):
    def test_detects_clock_time(self):
        self.assertTrue(has_explicit_time("ذكريني الخميس الساعة 3"))
        self.assertTrue(has_explicit_time("أضيفي مهمة الجمعة 10:30"))
        self.assertTrue(has_explicit_time("ذكريني مساء الخميس"))
        self.assertTrue(has_explicit_time("بكرة الصبح"))

    def test_no_time_words(self):
        self.assertFalse(has_explicit_time("ذكريني الخميس"))
        self.assertFalse(has_explicit_time("أضيفي مهمة الجمعة القادم"))
        self.assertFalse(has_explicit_time(""))


class TestResolveDayNameToIso(unittest.TestCase):
    # _REF = Thursday 2026-04-30

    def test_returns_iso_for_day_name(self):
        # "الخميس" from Thursday → next Thursday = 2026-05-07
        iso = resolve_day_name_to_iso("ذكريني الخميس", reference=_REF)
        self.assertIsNotNone(iso)
        self.assertTrue(iso.startswith("2026-05-07"))

    def test_default_hour_is_9am(self):
        iso = resolve_day_name_to_iso("ذكريني الجمعة", reference=_REF)
        self.assertIsNotNone(iso)
        # 2026-05-01 is Friday; should be at 09:00
        self.assertIn("09:00", iso)

    def test_custom_default_hour(self):
        iso = resolve_day_name_to_iso("ذكريني السبت", reference=_REF, default_hour=11)
        self.assertIsNotNone(iso)
        self.assertIn("11:00", iso)

    def test_returns_none_when_explicit_time_present(self):
        # Falls through to AI when user already gave a time
        self.assertIsNone(resolve_day_name_to_iso("ذكريني الخميس الساعة 3", reference=_REF))

    def test_returns_none_when_no_day_name(self):
        self.assertIsNone(resolve_day_name_to_iso("ذكريني بكرة", reference=_REF))
        self.assertIsNone(resolve_day_name_to_iso("", reference=_REF))

    def test_colloquial_variant_resolves(self):
        iso = resolve_day_name_to_iso("حطي تذكير الاربعا", reference=_REF)
        self.assertIsNotNone(iso)
        # From Thursday 2026-04-30, next Wednesday = 2026-05-06
        self.assertTrue(iso.startswith("2026-05-06"))


if __name__ == "__main__":
    unittest.main()
