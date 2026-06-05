"""Authoritative mapping of Arabic day-name variants to Python weekday numbers.

Convention: 0=Monday … 6=Sunday  (matches datetime.weekday()).
All callers should import from here — do not duplicate day-name lists.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from app.utils.time import USER_TZ

# Covers: with/without ال, colloquial spellings, common typos, tashkeel-free.
DAY_NAME_TO_WEEKDAY: dict[str, int] = {
    # Sunday = 6
    "أحد": 6,
    "احد": 6,
    "الأحد": 6,
    "الاحد": 6,
    # Monday = 0
    "اثنين": 0,
    "اتنين": 0,
    "إثنين": 0,
    "إتنين": 0,
    "الاثنين": 0,
    "الاتنين": 0,
    "الإثنين": 0,
    "الإتنين": 0,
    # Tuesday = 1
    "ثلاثاء": 1,
    "ثلاثا": 1,
    "تلاتاء": 1,
    "تلاتا": 1,
    "تلاثاء": 1,
    "تلاثا": 1,
    "الثلاثاء": 1,
    "الثلاثا": 1,
    "التلاتاء": 1,
    "التلاتا": 1,
    "التلاثاء": 1,
    "التلاثا": 1,
    # Wednesday = 2
    "أربعاء": 2,
    "اربعاء": 2,
    "أربعا": 2,
    "اربعا": 2,
    "الأربعاء": 2,
    "الاربعاء": 2,
    "الأربعا": 2,
    "الاربعا": 2,
    # Thursday = 3
    "خميس": 3,
    "الخميس": 3,
    # Friday = 4
    "جمعة": 4,
    "جمعه": 4,
    "الجمعة": 4,
    "الجمعه": 4,
    # Saturday = 5
    "سبت": 5,
    "السبت": 5,
}

# Reverse: weekday number → canonical display name (0=Mon … 6=Sun)
WEEKDAY_TO_AR_NAME: dict[int, str] = {
    0: "الاثنين",
    1: "الثلاثاء",
    2: "الأربعاء",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}

# Words that indicate an explicit time was given — used to decide whether to
# apply a default reminder time when only a day name is present.
_EXPLICIT_TIME_PAT = re.compile(
    r"الساعة|ساعة|صباحاً|مساءً|صباح|مساء|الصبح|الضهر|الظهر|المساء|الليل"
    r"|\d+\s*:\s*\d+|\d+\s*(?:am|pm)",
    re.IGNORECASE,
)


def parse_arabic_day_name(text: str) -> Optional[int]:
    """Return weekday (0=Mon … 6=Sun) for an Arabic day-name token, or None."""
    return DAY_NAME_TO_WEEKDAY.get(text.strip())


def find_day_in_text(text: str) -> Optional[int]:
    """Scan tokens in *text* for any known Arabic day name; return weekday or None."""
    for token in text.split():
        token = token.strip("،.,!؟'\"()[]")
        wd = DAY_NAME_TO_WEEKDAY.get(token)
        if wd is not None:
            return wd
    return None


def has_explicit_time(text: str) -> bool:
    """Return True if *text* contains words that indicate a specific clock time."""
    return bool(_EXPLICIT_TIME_PAT.search(text))


def next_weekday_date(
    weekday: int,
    *,
    reference: Optional[date] = None,
    allow_today: bool = False,
) -> date:
    """Return the next occurrence of *weekday* (0=Mon … 6=Sun).

    allow_today=True  → returns *reference* itself if it matches *weekday*.
    allow_today=False → always returns a strictly future date (next week when same day).
    """
    ref = reference or datetime.now(USER_TZ).date()
    days_ahead = (weekday - ref.weekday()) % 7
    if days_ahead == 0 and not allow_today:
        days_ahead = 7
    return ref + timedelta(days=days_ahead)


def resolve_day_name_to_iso(
    text: str,
    *,
    default_hour: int = 9,
    reference: Optional[date] = None,
) -> Optional[str]:
    """If *text* contains an Arabic day name and NO explicit time, return an ISO
    datetime string for the next occurrence of that day at *default_hour*:00.

    Returns None if no day name is found or if an explicit time is already present
    (so the caller should fall through to AI parsing).
    """
    if has_explicit_time(text):
        return None
    weekday = find_day_in_text(text)
    if weekday is None:
        return None
    target = next_weekday_date(weekday, reference=reference)
    dt = datetime(
        target.year, target.month, target.day, default_hour, 0, 0, tzinfo=USER_TZ
    )
    return dt.isoformat()


def parse_numeric_date(
    text: str, *, default_hour: int = 9, reference: Optional[date] = None
) -> Optional[str]:
    """Try to parse common numeric date formats from *text* and return ISO datetime at default_hour.

    Supports: YYYY/MM/DD, DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, YYYY_MM_DD, DDMMYYYY, YYYYMMDD
    Returns None if no numeric date found or parse fails.
    """
    if not text:
        return None

    # normalize separators
    s = text.strip()
    s = re.sub(r"[\\._,]", "-", s)

    # yyyy[-/]mm[-/]dd
    m = re.search(r"(20\d{2}|19\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = datetime(y, mo, d, default_hour, 0, 0, tzinfo=USER_TZ)
            return dt.isoformat()
        except Exception:
            return None

    # dd[-/]mm[-/]yyyy
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2}|19\d{2})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = datetime(y, mo, d, default_hour, 0, 0, tzinfo=USER_TZ)
            return dt.isoformat()
        except Exception:
            return None

    # compact 8 digits either YYYYMMDD or DDMMYYYY
    m = re.search(r"(\d{8})", s)
    if m:
        v = m.group(1)
        # try YYYYMMDD first
        y, mo, d = int(v[0:4]), int(v[4:6]), int(v[6:8])
        try:
            dt = datetime(y, mo, d, default_hour, 0, 0, tzinfo=USER_TZ)
            return dt.isoformat()
        except Exception:
            pass
        # fallback try DDMMYYYY
        d, mo, y = int(v[0:2]), int(v[2:4]), int(v[4:8])
        try:
            dt = datetime(y, mo, d, default_hour, 0, 0, tzinfo=USER_TZ)
            return dt.isoformat()
        except Exception:
            return None

    return None


def parse_relative_simple(
    text: str, *, default_hour: int = 9, reference: Optional[date] = None
) -> Optional[str]:
    """Handle simple relative phrases like 'بعد X يوم', 'بعد أسبوع', 'بعد أسبوعين'."""
    if not text:
        return None

    s = text.strip()
    ref_date = reference or datetime.now(USER_TZ).date()

    # بعد X أيام or بعد X يوم
    m = re.search(r"بعد\s*(\d+)\s*(?:أيام|يوم)", s)
    if m:
        days = int(m.group(1))
        target = ref_date + timedelta(days=days)
        dt = datetime(
            target.year, target.month, target.day, default_hour, 0, 0, tzinfo=USER_TZ
        )
        return dt.isoformat()

    # بعد يوم / بكرا / غدا
    if re.search(r"بعد\s+يوم|بكرا|بكره|بكرة|غدا|غداً", s):
        target = ref_date + timedelta(days=1)
        dt = datetime(
            target.year, target.month, target.day, default_hour, 0, 0, tzinfo=USER_TZ
        )
        return dt.isoformat()

    # بعد X أسبوع(ين)
    m = re.search(r"بعد\s*(\d+)\s*(?:أسبوع|أسابيع)", s)
    if m:
        weeks = int(m.group(1))
        target = ref_date + timedelta(weeks=weeks)
        dt = datetime(
            target.year, target.month, target.day, default_hour, 0, 0, tzinfo=USER_TZ
        )
        return dt.isoformat()

    # بعد أسبوع / الأسبوع الجاي
    if re.search(
        r"بعد\s+أسبوع|الأسبوع\s+الجاي|الأسبوع\s+القادم|الأسبوع\s+اللي\s+جاي", s
    ):
        target = ref_date + timedelta(weeks=1)
        dt = datetime(
            target.year, target.month, target.day, default_hour, 0, 0, tzinfo=USER_TZ
        )
        return dt.isoformat()

    return None


def parse_date_from_text(
    text: str, *, default_hour: int = 9, reference: Optional[date] = None
) -> Optional[str]:
    """Combined heuristics: day names, numeric dates, simple relative phrases."""
    if not text:
        return None

    # prefer explicit day-name resolution (already returns None on explicit times)
    det = resolve_day_name_to_iso(text, default_hour=default_hour, reference=reference)
    if det:
        return det

    num = parse_numeric_date(text, default_hour=default_hour, reference=reference)
    if num:
        return num

    rel = parse_relative_simple(text, default_hour=default_hour, reference=reference)
    if rel:
        return rel

    return None
