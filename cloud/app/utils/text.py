import re

# قائمة الـ mood tags المسموح بها — أي tag من هدول لازم يُشال قبل الإرسال
_MOOD_TAG_PATTERN = re.compile(
    r"^\[(?:happy|think|sad|angry|bored|neutral|excited|romantic|shy|tired|serious|"
    r"love|morning|calm|care|playful|warn|info|ok|done|wait|error|laugh)\]\s*",
    re.IGNORECASE,
)

# شيل أي mood tag من أي مكان في النص (مش بس أوله)
_MOOD_TAG_ANYWHERE = re.compile(
    r"\[(?:happy|think|sad|angry|bored|neutral|excited|romantic|shy|tired|serious|"
    r"love|morning|calm|care|playful|warn|info|ok|done|wait|error|laugh)\]\s*",
    re.IGNORECASE,
)


# بتطلع الرياكشن (زي [happy]) من أول الرد وبتشيله من النص
def extract_reaction_and_clean_text(text: str):
    """
    استخراج الرياكشن (مثل [happy]) من بداية الرد، وحفظه في متغير منفصل.
    هذا المتغير مهم لتمريره للهاردوير (الشاشة) مستقبلاً.
    يرجع (reaction, text_without_reaction)
    """
    reaction = None
    cleaned = str(text or "").strip()

    match = re.match(r"^\[([a-zA-Z_]+)\]\s*", cleaned)
    if match:
        reaction = match.group(1).lower()
        cleaned = cleaned[match.end() :].strip()

    # شيل أي tags إضافية متبقية في النص
    cleaned = _MOOD_TAG_ANYWHERE.sub("", cleaned).strip()

    return reaction, cleaned


# هاي الدالة بتجهز النص قبل ما ينقرأ صوتياً: بتحذف الروابط، بتخفف القوائم الطويلة، وبدل ما يقرأ اللينكات بتحكي للمستخدم إنو الروابط مرفقة بالرسالة.
def prepare_tts_text(text: str) -> str:
    """
    Clean text before sending it to TTS:
    - remove mood tags
    - remove URLs
    - shorten long lists
    - add a friendly note instead of reading raw links
    """
    if not text:
        return ""

    cleaned = text

    # شيل mood tags أولاً
    cleaned = _MOOD_TAG_ANYWHERE.sub("", cleaned).strip()
    cleaned = re.sub(r"📍[^\n]*", "", cleaned)
    cleaned = re.sub(r"📞[^\n]*", "", cleaned)
    cleaned = re.sub(r"\+?[\d][\d\s\-]{6,}", "", cleaned)

    # Remove URLs
    has_urls = bool(re.search(r"https?://\S+", cleaned))
    cleaned = re.sub(r"https?://\S+", "", cleaned)

    # Remove excessive blank lines
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)

    # If original text had links, mention that links are attached in the message
    if has_urls:
        cleaned = cleaned.strip()
        if cleaned:
            cleaned += "\n\nالروابط مرفقة في الرسالة."
        else:
            cleaned = "تم تجهيز النتيجة، الروابط مرفقة في الرسالة."

    # Optional: shorten very long result lists
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) > 8:
        cleaned = "\n".join(lines[:8]) + "\n\nباقي التفاصيل والروابط موجودة في الرسالة."

    cleaned = localize_numbers_for_tts(cleaned)
    return cleaned.strip()


# هاي الدالة بتحول الأرقام المكتوبة لنسخة منطوقة حسب لغة النص، لكن فقط للصوت، أما الكتابة الأصلية بتضل زي ما هي.
def localize_numbers_for_tts(text: str) -> str:
    text = str(text or "").strip()

    def looks_arabic(s: str) -> bool:
        return bool(re.search(r"[\u0600-\u06FF]", s))

    arabic_numbers = {
        "1": "واحد",
        "2": "اثنين",
        "3": "ثلاثة",
        "4": "أربعة",
        "5": "خمسة",
        "6": "ستة",
        "7": "سبعة",
        "8": "ثمانية",
        "9": "تسعة",
        "10": "عشرة",
    }

    english_numbers = {
        "1": "one",
        "2": "two",
        "3": "three",
        "4": "four",
        "5": "five",
        "6": "six",
        "7": "seven",
        "8": "eight",
        "9": "nine",
        "10": "ten",
    }

    number_map = arabic_numbers if looks_arabic(text) else english_numbers

    for num, spoken in sorted(
        number_map.items(), key=lambda x: len(x[0]), reverse=True
    ):
        text = re.sub(
            rf"(?<![A-Za-z\u0600-\u06FF0-9/]){re.escape(num)}(?![A-Za-z\u0600-\u06FF0-9/.])",
            spoken,
            text,
        )

    return text
