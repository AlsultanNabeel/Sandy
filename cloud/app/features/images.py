import re


def is_image_generation_request(message: str) -> bool:
    """Detect if user asks for image generation using AI."""
    text = (message or "").strip()
    if not text:
        return False

    # فلتر سريع — لو واضح إنه مش صورة ارجع False بسرعة
    non_image_hints = ["ذكرني", "مهمة", "تذكير", "ابحث", "دوري", "كيف", "شو رأيك"]
    if any(hint in text for hint in non_image_hints):
        return False

    triggers = [
        # بدون ياء
        "ارسم",
        "رسمة",
        "صمم صورة",
        "صمم",
        "ولّد صورة",
        "ولد صورة",
        "اعمل صورة",
        "اعمل",
        "generate image",
        "draw",
        "create image",
        "بدي صورة",
        "صورلي",
        # مع ياء (خطاب للمؤنث)
        "ارسمي",
        "ارسميلي",
        "صممي صورة",
        "صممي",
        "ولديلي صورة",
        "ولّدي صورة",
        "اعملي صورة",
        "اعملي",
        "بدي يصير صورة",
        "صوري",
    ]

    return any(t in text.lower() for t in triggers)


def extract_image_prompt(message: str) -> str:
    """Extract prompt text for image generation."""
    text = (message or "").strip()
    text = re.sub(r"^(?:/image|/img)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?:ارسم|رسمة|صمم صورة|ول\s*د صورة|ولّد صورة|generate image|draw)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text
