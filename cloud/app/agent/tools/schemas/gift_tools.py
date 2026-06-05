"""F5 — نظام الهدايا الرقمية.

Sandy تولّد هدية رقمية مخصصة (بيت شعر، رسالة تحفيزية، اقتباس، أو ابتسامة)
بناءً على mood + history + كلمات مفتاحية.

تستخدم Gemini Flash للتوليد (رخيص) — fallback إلى Azure إذا فشل.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)

# قوالب fallback — تُستخدم إذا فشل LLM
_FALLBACK_GIFTS = {
    "poem": [
        "إذا تعبت من الدنيا فعد لها\nبصدر يحضن أحلامك ولا يتعب",
        "خذ من كل يوم بسمته الأولى\nواترك التعب للريح",
    ],
    "quote": [
        "الخطوة الصغيرة اليوم أهم من الخطة الكاملة بكرا.",
        "اللي بيقدر يصبر دقيقة، بيقدر يصبر حياة كاملة.",
        "أنت بتكفي — هيك تماماً مثل ما إنت.",
    ],
    "motivation": [
        "ذكّر نفسك: كل اللي فات صعب، إنت قطعته. هذا يكفي.",
        "اليوم مش لازم تكون أفضل من الكل، فقط أفضل من أمس بخطوة.",
    ],
    "smile": [
        "ابتسامة صغيرة 🌷 — لأنها تليق فيك.",
        "خذ بوسة على جبينك من Sandy 💛",
    ],
    "joke": [
        "واحد فلسطيني رايح ع الدكتور قاله: دكتور، كل ما أشرب قهوة عيني تتألم. الدكتور قاله: طلّع الملعقة من الكاسة! هاهاها 😂",
        "ليش الكمبيوتر برد؟ لأنه ترك windows مفتوحة! هاهاها 😆",
        "واحد قال لجارته: شو طبختي اليوم؟ قالتله: نفس اللي طبختي امبارح. قالها: يعني سندويش من المطعم؟ هاهاها 🤣",
    ],
    "riddle": [
        "شو الشي اللي بتكسره قبل ما تستخدمه؟ 🤔 (هي هي هي، فكّر شوي)\n\nالجواب: البيضة 🥚 ههه",
        "بدخل المي ما بنبل، بدخل النار ما بحترق — شو أنا؟ 😏\n\nالجواب: الظل 🌑",
        "بتسمع كل شي، بس ما بتحكي ولا حرف — شو أنا؟ 🤭\n\nالجواب: الأذن 👂",
    ],
}

_GIFT_PROMPTS = {
    "poem": """ولّدي بيتين شعر بالعربية الفصحى. لا مقدمات. ابدئي مباشرة بالشعر.
مثال للأسلوب المطلوب:
يا قلبُ لا تيأسْ، فبعدَ الليلِ فجرُ
والصبرُ مفتاحٌ، وللآلامِ أجرُ

اكتبي بيتين مختلفين الآن — كاملين، وزن وقافية.""",

    "quote": """ولّدي اقتباساً واحداً قصيراً (سطر-سطرين) عميقاً بالعربية. لا مقدمات.""",

    "motivation": """ولّدي رسالة تحفيز قصيرة دافئة (سطر-سطرين) بالعامية الفلسطينية. ابدئي بـ 💪.""",

    "smile": """ولّدي جملة لطيفة قصيرة جداً (سطر واحد) بالعامية الفلسطينية تخلي المستخدم يبتسم.""",

    "joke": """احكي نكتة فلسطينية لطيفة (3-5 أسطر) واختمي بضحكة "هاهاها 😂".
مثال للأسلوب:
في واحد فلسطيني راح ع الدكتور، قاله: دكتور كل ما أشرب قهوة عيني تتألم.
الدكتور قاله: طلّع الملعقة من الكاسة!
هاهاها 😂

اكتبي نكتة جديدة الآن، بنفس البنية:""",

    "riddle": """اكتبي لغزاً عربياً قصيراً متبوعاً بالجواب — يجب أن تشمل الإجابة في نفس الرد.

الفورمات الإلزامي:
السطر 1: اللغز كاملاً (سؤال)
السطر 2: "هي هي هي 😏 فكر شوي..."
السطر 3: (فارغ)
السطر 4: "💡 الجواب: ..."

مثال:
شو الشي اللي بمشي بلا رجلين وبدق بلا يدين؟
هي هي هي 😏 فكر شوي...

💡 الجواب: الساعة ⏰

اكتبي لغزاً جديداً الآن بنفس الفورمات تماماً، اكتبي الجواب في نفس الرد:""",
}


def _generate_with_llm(gift_type: str, context: str) -> str | None:
    """يحاول التوليد عبر Azure GPT-4o-mini، None إذا فشل."""
    prompt = _GIFT_PROMPTS.get(gift_type)
    if not prompt:
        return None
    if context:
        prompt += f"\n\nسياق المستخدم: {context}"

    try:
        from app.integrations.azure_intent_client import AzureIntentClient
        client = AzureIntentClient()
        out = client._generate_with_gemini(
            prompt,
            response_mime_type="text/plain",
            temperature=0.8,
            max_output_tokens=4000,
        )
        if out and len(out.strip()) > 5:
            return out.strip()
    except Exception as exc:
        logger.debug(f"[gift] Azure intent failed: {exc}")
    return None


def digital_gift(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يولّد هدية رقمية: شعر | اقتباس | تحفيز | ابتسامة."""
    gift_type = str(args.get("type") or "smile").lower()
    context = str(args.get("context") or "").strip()

    if gift_type not in _FALLBACK_GIFTS:
        gift_type = "smile"

    # حاول LLM أولاً
    content = _generate_with_llm(gift_type, context)
    if not content:
        content = random.choice(_FALLBACK_GIFTS[gift_type])

    prefix = {
        "poem": "💐 هدية صغيرة من Sandy:\n\n",
        "quote": "📜 اقتباس لك اليوم:\n\n",
        "motivation": "💪 ",
        "smile": "🌷 ",
        "joke": "😄 ",
        "riddle": "🧩 ",
    }.get(gift_type, "🌷 ")

    return {"handled": True, "reply": prefix + content}


GIFT_TOOLS = [
    {
        "name": "digital_gift",
        "description": (
            "ولّدي هدية للمستخدم. **يجب** تحديد النوع المناسب: "
            "poem=شعر (طلب شعر/قصيدة/أبيات)، "
            "quote=اقتباس (حكمة/مقولة)، "
            "motivation=تحفيز (طاقة إيجابية/تشجيع)، "
            "smile=ابتسامة (شي بسيط يفرح)، "
            "joke=نكتة (نكتة/ضحكة)، "
            "riddle=لغز (لغز/تحدي/خمّن)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["poem", "quote", "motivation", "smile", "joke", "riddle"],
                    "description": "نوع الهدية — مطلوب اختيار النوع الصحيح حسب طلب المستخدم",
                },
                "context": {"type": "string", "description": "سياق اختياري (مزاج، حدث)"},
            },
            "required": ["type"],
        },
        "handler": digital_gift,
    },
]
