"""Response templates keyed by intent + persona intensity.

Fallback for when route_with_fc's Gemini response omits response_template.
Templates are short persona-flavored intros prepended to execution replies.
"""

from __future__ import annotations

_TEMPLATES: dict[str, dict[str, str]] = {
    "task.create": {
        "standard": "سجّلتها ✅",
        "playful": "خلاص! سجّلتها 🎉",
        "empathetic": "خلص، دوّنتها عشانك.",
        "formal": "تم إضافة المهمة بنجاح.",
        "minimal": "",
    },
    "task.complete": {
        "standard": "تم ✅",
        "playful": "ييي! أنجزتها 🎊",
        "empathetic": "ما شاء الله عليك.",
        "formal": "تم إتمام المهمة.",
        "minimal": "",
    },
    "task.uncomplete": {
        "standard": "رجّعتها للقائمة.",
        "formal": "تم إعادة المهمة.",
        "minimal": "",
    },
    "task.delete": {
        "standard": "حُذفت ✅",
        "formal": "تم حذف المهمة.",
        "minimal": "",
    },
    "task.delete_many": {
        "standard": "حُذفن ✅",
        "formal": "تم حذف المهام.",
        "minimal": "",
    },
    "task.update": {
        "standard": "عدّلتها ✅",
        "formal": "تم التعديل.",
        "minimal": "",
    },
    "reminder.create": {
        "standard": "سجّلت التذكير ⏰",
        "playful": "هذكّرك بوقتها! ⏰✨",
        "empathetic": "خلص، ما راح تنسى.",
        "formal": "تم إضافة التذكير.",
        "minimal": "",
    },
    "reminder.update": {
        "standard": "عدّلت التذكير ✅",
        "formal": "تم تعديل التذكير.",
        "minimal": "",
    },
    "reminder.delete": {
        "standard": "حُذف التذكير ✅",
        "formal": "تم حذف التذكير.",
        "minimal": "",
    },
    "calendar.add": {
        "standard": "أضفت الحدث 📅",
        "playful": "يالله! أضفته 📅🎉",
        "empathetic": "سجّلته عشانك.",
        "formal": "تم إضافة الحدث إلى التقويم.",
        "minimal": "",
    },
    "calendar.update": {
        "standard": "عدّلت الحدث ✅",
        "formal": "تم تعديل الحدث.",
        "minimal": "",
    },
    "calendar.delete": {
        "standard": "حُذف الحدث ✅",
        "formal": "تم حذف الحدث.",
        "minimal": "",
    },
    "email.send": {
        "standard": "تم إرسال الإيميل ✉️",
        "formal": "تم إرسال البريد الإلكتروني.",
        "minimal": "",
    },
    "email.reply": {
        "standard": "تم الرد ✉️",
        "formal": "تم إرسال الرد.",
        "minimal": "",
    },
    "research.web": {
        "standard": "إليك ما وجدت 🔍",
        "formal": "نتائج البحث:",
        "minimal": "",
    },
    "research.places": {
        "standard": "وجدت لك بعض الأماكن 📍",
        "formal": "نتائج البحث عن الأماكن:",
        "minimal": "",
    },
}

_VALID_INTENSITIES = frozenset(
    {"minimal", "standard", "empathetic", "playful", "formal"}
)


def get_response_template(intent: str, intensity: str) -> str:
    """Returns a persona-flavored intro template for the given intent + intensity.

    Args:
        intent: action intent (e.g. task.create)
        intensity: persona intensity (minimal/standard/empathetic/playful/formal)

    Returns:
        Short template string, or "" if none defined for this combination.
    """
    intent_templates = _TEMPLATES.get(intent)
    if not intent_templates:
        return ""

    safe_key = intensity if intensity in _VALID_INTENSITIES else "standard"
    if safe_key in intent_templates:
        return intent_templates[safe_key]
    return intent_templates.get("standard", "")
