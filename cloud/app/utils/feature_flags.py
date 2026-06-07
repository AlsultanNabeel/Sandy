"""Feature flags بسيطة لإطلاق آمن للتغييرات الكبيرة (R6 light).

الفلسفة (بناءً على مراجعة senior):
- ساندي عائلية (3-4 users) → A/B testing بـ chi-square = صفر دلالة إحصائية
- بدل ذلك: feature flags بسيطة + manual review عبر eval suite + Langfuse compare
- Heroku Config Vars توفّر مكان مركزي لتفعيل/إطفاء flag → restart → done في < 5 دقايق

الـ flags المعرّفة:
    SANDY_USE_PROMPT_CACHING    → تفعيل Anthropic prompt caching على Claude calls
    SANDY_USE_DSPY_PROMPTS      → استخدام DSPy-compiled prompts بدل handcrafted
    SANDY_USE_FEEDBACK_BUTTONS  → إضافة 👍/👎 لكل رد في تيليغرام

كل الـ flags default `False`.
"""

from __future__ import annotations

import os
from typing import Iterable, List

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled", "y"})
_FALSY = frozenset({"0", "false", "no", "off", "disabled", "n", ""})

# Catalog من الـ flags المعروفة — مفيد للـ tagging والـ /flags command لاحقاً
KNOWN_FLAGS: tuple[str, ...] = (
    "USE_PROMPT_CACHING",
    "USE_DSPY_PROMPTS",
    "USE_FEEDBACK_BUTTONS",
)


def _env_name(flag: str) -> str:
    """Convert short flag name to env var name. 'USE_X' → 'SANDY_USE_X'."""
    flag = flag.upper().strip()
    return flag if flag.startswith("SANDY_") else f"SANDY_{flag}"


def is_enabled(flag: str, default: bool = False) -> bool:
    """يرجع True لو الـ flag مفعّل في Heroku Config Vars.

    يقبل القيم: true/1/yes/on/enabled/y (case-insensitive).
    أي قيمة أخرى → default.

        >>> is_enabled("USE_PROMPT_CACHING")
        False
        >>> # بعد ما نضيف SANDY_USE_PROMPT_CACHING=true على Heroku
        >>> is_enabled("USE_PROMPT_CACHING")
        True
    """
    raw = os.getenv(_env_name(flag), "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return default


def get_active_flags(known: Iterable[str] = KNOWN_FLAGS) -> List[str]:
    """يرجع قائمة الـ flags المفعّلة حالياً — للـ Langfuse tagging."""
    return [f.lower() for f in known if is_enabled(f)]


def get_state() -> dict:
    """Snapshot كامل لكل flag معروف (مفيد للـ logging والـ debugging)."""
    return {f.lower(): is_enabled(f) for f in KNOWN_FLAGS}


# Convenience accessors — أسرع وأوضح في الكود
def use_prompt_caching() -> bool:
    # default False بمجرد ما R3 يدخل (overridable لو احتجنا نطفّيه)
    return is_enabled("USE_PROMPT_CACHING", default=False)


def use_dspy_prompts() -> bool:
    return is_enabled("USE_DSPY_PROMPTS")


def use_feedback_buttons() -> bool:
    return is_enabled("USE_FEEDBACK_BUTTONS")
