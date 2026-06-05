"""Self-awareness meta-tool: report Sandy's available capabilities + the
live health of each one (M6b).

Single source of truth: the ToolRegistry. Anything registered shows up
here; anything not registered doesn't — keeping the agent honest about
what it can / can't actually do right now."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from app.agent.tools.registry import get_registry

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext


# Arabic category labels + short Arabic summary for each bucket.
# الترتيب يحدد ترتيب العرض النهائي للمستخدم.
_CATEGORIES = (
    ("📝 المهام",       ("task_",),                    "إضافة، تعديل، حذف، وإكمال المهام"),
    ("⏰ التذكيرات",     ("reminder_",),                 "تذكير بوقت محدد أو متكرر"),
    ("📅 التقويم",       ("calendar_",),                 "حجز مواعيد، عرض جدولك، وكشف التعارضات"),
    ("✉️ الإيميل",       ("email_",),                    "قراءة، صياغة، وإرسال رسائل Gmail"),
    ("🎨 الصور",         ("image_",),                    "توليد صور، تعديلها، ووصف الصور المرفوعة"),
    ("🛠️ الهاردوير",     ("hardware_",),                 "تحريك السيرفو، الكاميرا، تعابير الوجه، البَزر"),
    ("🔍 البحث",         ("research_", "exa_"),          "بحث على الويب وتلخيص النتائج"),
    ("🎁 الهدايا",       ("gift_",),                     "اقتراحات هدايا ومناسبات"),
    ("🎯 الأهداف",       ("goal_",),                     "تتبع أهدافك وتقدمك عليها"),
    ("🔮 رسائل المستقبل", ("future_message_",),          "كتابة رسالة لنفسك يتم تسليمها بوقت لاحق"),
    ("🤝 المحتوى",       ("content_",),                  "كتابة وتنسيق محتوى عام"),
    ("🐙 GitHub",        ("github_",),                   "Issues، commits، ملفات، وبناء مشاريع كاملة"),
    ("🧠 الذاكرة",       ("memory_", "store_", "recall_"), "حفظ معلومات عنك واسترجاعها"),
    ("🔧 الدردشة",       ("chat_", "ask_", "request_", "pending_"), "حوار، أسئلة توضيحية، وتأكيدات"),
)


def _bucket_for(name: str) -> int:
    """يرجع index الـ bucket المناسب — أو -1 إذا ما في تصنيف."""
    for idx, (_label, prefixes, _desc) in enumerate(_CATEGORIES):
        if any(name.startswith(p) for p in prefixes):
            return idx
    return -1


def get_capabilities_handler(
    args: Dict[str, Any], ctx: "DispatchContext"
) -> Dict[str, Any]:
    """Return a grouped, owner-friendly summary of every registered tool
    plus a callout for any that are currently degraded.

    Args:
        scope (str, optional): "all" (default), "degraded", or "category"
                               to filter the output.
    """
    scope = str((args or {}).get("scope") or "all").strip().lower()
    caps = get_registry().describe_capabilities()
    degraded = [c for c in caps if c.get("health", {}).get("status") == "degraded"]

    if scope == "degraded":
        if not degraded:
            return {
                "handled": True,
                "reply": "كل قدراتي شغالة تمام حالياً 👌",
            }
        lines = ["⚠️ القدرات المعطّلة حالياً:"]
        for c in degraded:
            h = c.get("health", {})
            label_idx = _bucket_for(c["name"])
            cat_label = _CATEGORIES[label_idx][0] if label_idx >= 0 else "أخرى"
            fail_pct = int((1 - h.get("success_rate", 1.0)) * 100)
            line = (
                f"• {cat_label} — {fail_pct}% فشل في آخر "
                f"{h.get('n_calls', 0)} محاولة"
            )
            if h.get("last_error"):
                line += f"\n   (آخر خطأ: {h['last_error'][:80]})"
            lines.append(line)
        return {"handled": True, "reply": "\n".join(lines)}

    # Default scope: عرض مجمّع بالعربي مع وصف قصير
    buckets: Dict[int, int] = {}
    other_count = 0
    for c in caps:
        idx = _bucket_for(c["name"])
        if idx == -1:
            other_count += 1
        else:
            buckets[idx] = buckets.get(idx, 0) + 1

    lines: List[str] = []
    lines.append(f"🧰 عندي {len(caps)} قدرة، موزّعة هيك:")
    lines.append("")
    for idx, (label, _prefixes, desc) in enumerate(_CATEGORIES):
        count = buckets.get(idx, 0)
        if count == 0:
            continue
        lines.append(f"{label} — {desc} ({count})")
    if other_count:
        lines.append(f"🧰 أخرى ({other_count})")

    if degraded:
        lines.append("")
        lines.append(
            f"⚠️ في {len(degraded)} قدرة معطّلة حالياً — اسأليني "
            f"'شو القدرات المعطّلة؟' للتفاصيل."
        )
    return {"handled": True, "reply": "\n".join(lines)}


SELF_AWARENESS_TOOLS = [
    {
        "name": "get_capabilities",
        "description": (
            "🧭 يعرض قائمة قدرات ساندي الحالية + أي قدرة معطّلة من اللي "
            "اتسجلت. استخدمه لما الأونر يسأل: 'شو بتقدري تعملي؟', 'وريني "
            "قدراتك', 'في أداة معطّلة؟', 'شو حالة قدراتك؟', 'بتشتغل كل "
            "ادواتك؟'. الـ data بتيجي من الـ tool registry مباشرة + tool "
            "health tracker — مصدر واحد، ما في تخمين."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "degraded"],
                    "description": (
                        "all = كل القدرات مجمّعة بمواضيع. "
                        "degraded = فقط القدرات اللي عندها مشاكل الآن."
                    ),
                },
            },
            "required": [],
        },
        "handler": get_capabilities_handler,
    },
]
