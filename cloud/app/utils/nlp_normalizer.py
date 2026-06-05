"""User message normalization pipeline.

Public API: normalize_user_message(text) -> str
Basic sanitization only — Gemini Flash handles all datetime/duration/recurrence parsing.
"""

import re

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_EASTERN_ARABIC_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def normalize_user_message(text: str) -> str:
    """Basic sanitization without destroying human context. The LLM handles the rest."""
    text = str(text or "").strip()
    if not text:
        return ""

    normalized = text.translate(_ARABIC_INDIC_DIGITS).translate(_EASTERN_ARABIC_DIGITS)
    normalized = normalized.replace("،", ",").replace("؟", "?").replace("ـ", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized
