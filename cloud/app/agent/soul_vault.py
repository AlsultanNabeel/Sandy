"""مدير شخصية Sandy.

يجيب persona_snippet المناسب حسب مستوى الـ intensity. الـ snippets
الحساسة بتيجي من env vars عشان ما تظهر في GitHub.
"""

from __future__ import annotations

import os
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

# لما المود stressed أو frustrated أو sad، ما بنستخدم playful أبداً
_MOOD_FORBIDDEN: dict[str, set[str]] = {
    "stressed": {"playful"},
    "frustrated": {"playful"},
    "sad": {"playful"},
}

# snippets افتراضية لكل مستوى. في production بنبدّلها بـ SANDY_SOUL_* env vars
_DEFAULT_SNIPPETS: dict[str, str] = {
    "minimal": "",
    "standard": "هيه، وينك؟ 🤍",
    "empathetic": "أنا هون معك، خود وقتك.",
    "playful": "يالله نشوف شو عندنا! 🎉",
    "formal": "تم التنفيذ بنجاح.",
}

# ردود متنوّعة لكل intensity عشان ما تتكرر نفس الجملة
_PERSONA_VARIATIONS: dict[str, list[str]] = {
    "standard": [
        "هيه، وينك؟ 🤍",
        "كيفك؟ شو في عندك؟",
        "يا هلا! شو بقدر ساعدك؟",
        "هلأ معك.",
    ],
    "empathetic": [
        "أنا هون معك، خود وقتك.",
        "كلامك وصل — ما رح تكون لحالك.",
        "حاسة فيك، ارتاح شوي.",
        "والله يعطيك العافية.",
    ],
    "playful": [
        "يالله نشوف شو عندنا! 🎉",
        "يي! شو مثير 😄",
        "هاد يوم تمام كمان! 🌟",
        "عنجد؟ يسعدني! 🥳",
    ],
    "formal": [
        "تم التنفيذ بنجاح.",
        "بكل سرور.",
        "جاهزة دائماً.",
    ],
}

# ردود اعتذار مناسبة للمود
_APOLOGY_SNIPPETS: dict[str, str] = {
    "neutral":    "آسفة، صار خطأ ما توقعته. حاول مرة ثانية؟",
    "calm":       "آسفة، ما اشتغل كما يجب. جرّب معي مرة ثانية.",
    "happy":      "آسفة! صار خطأ بس إحنا نتعدّاه — جرّب تاني؟",
    "sad":        "آسفة إنك تواجه هاد فوق كل شي — خذ وقتك وجرّب مرة ثانية.",
    "stressed":   "والله آسفة، خطأ في هالوقت الضاغط بالذات. لما تكون جاهز، جرّب مرة ثانية.",
    "frustrated": "آسفة جداً — أنا عارفة إنك عم تحاول. خليني نجرّب ثاني مع بعض.",
    "angry":      "آسفة، وكلامك وصلني. هالغلطة ما كانت لازم تصير.",
}

# تلميحات بصيغة سؤال للمودات الضاغطة
_HINT_SNIPPETS: dict[str, str] = {
    "stressed":   "بدك تاخد نفس قبل؟ مش لازم نحسم كل شي هلأ.",
    "frustrated": "ممكن نرجع لهالموضوع بعد شوي؟ أحياناً الوقت بغيّر كتير.",
    "sad":        "بدك نحكي أول؟ أنا مش مستعجلة.",
    "angry":      "خليني أفهم أكثر — شو اللي ضايقك بالضبط؟",
}

# ردود امتنان متبادل
_GRATITUDE_SNIPPETS: list[str] = [
    "الشكر لك، إنت اللي بتخلي الشغل يحمس 🤍",
    "والله يسعدك، هيك كلام بحمّس 😊",
    "إنت اللي تشكر — بصراحة!",
    "يسلم قلبك 🌸 هالكلام وصل",
]

_VALID_INTENSITIES = frozenset(_DEFAULT_SNIPPETS.keys())
_DEFAULT_INTENSITY = "standard"
_DEFAULT_MINIMAL_PERSONA = ""


def _load_snippet(intensity: str) -> str:
    """يقرأ الـ snippet من env var، وإلا من الـ default."""
    env_key = f"SANDY_SOUL_{intensity.upper()}"
    return os.environ.get(env_key, _DEFAULT_SNIPPETS.get(intensity, ""))


def _safe_intensity(intensity: Optional[str], mood: Optional[str]) -> str:
    """يتأكد إن الـ intensity صالح ويطبّق قاعدة المود الممنوع."""
    if not intensity or intensity not in _VALID_INTENSITIES:
        intensity = _DEFAULT_INTENSITY

    forbidden = _MOOD_FORBIDDEN.get(mood or "", set())
    if intensity in forbidden:
        intensity = "empathetic"

    return intensity


def get_persona(
    user_id: str,
    intensity: Optional[str],
    mood: Optional[str] = None,
) -> dict:
    """يجيب persona للمستخدم بالـ intensity المناسب.

    Args:
        user_id: معرف المستخدم
        intensity: المستوى المطلوب
        mood: مزاج المستخدم، عشان نطبّق قاعدة المود الممنوع

    Returns:
        dict فيه intensity و snippet
    """
    safe_int = _safe_intensity(intensity, mood)

    # للمودات الضاغطة بنستخدم تلميح بصيغة سؤال لو ما في env override
    env_key = f"SANDY_SOUL_{safe_int.upper()}"
    if not os.environ.get(env_key) and mood in _HINT_SNIPPETS:
        snippet = _HINT_SNIPPETS[mood]
    else:
        snippet = _load_snippet(safe_int)

    return {
        "intensity": safe_int,
        "snippet": snippet,
    }


def get_apology(mood: Optional[str] = None) -> str:
    """اعتذار مناسب للمود الحالي."""
    return _APOLOGY_SNIPPETS.get(mood or "neutral", _APOLOGY_SNIPPETS["neutral"])


def get_hint_snippet(mood: Optional[str] = None) -> Optional[str]:
    """تلميح بصيغة سؤال للمودات الضاغطة. يرجّع None لباقي المودات."""
    return _HINT_SNIPPETS.get(mood or "")


def get_gratitude_snippet() -> str:
    """رد امتنان عشوائي."""
    return random.choice(_GRATITUDE_SNIPPETS)


def get_varied_snippet(intensity: str, mood: Optional[str] = None) -> str:
    """snippet متنوّع لردود الشات."""
    safe_int = _safe_intensity(intensity, mood)
    env_val = os.environ.get(f"SANDY_SOUL_{safe_int.upper()}", "")
    if env_val:
        return env_val
    variations = _PERSONA_VARIATIONS.get(safe_int)
    if variations:
        return random.choice(variations)
    return _DEFAULT_SNIPPETS.get(safe_int, "")
