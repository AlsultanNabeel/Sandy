"""Research intent detection — classifies the user message before any API call.

Public API:
  detect_research_type(message) -> str
  extract_requested_result_count(message, default) -> int
  is_research_request(message) -> bool
  is_research_followup_request(message) -> bool
"""

import re


def is_place_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    triggers = [
        "كافيه",
        "كافي",
        "cafe",
        "café",
        "coffee shop",
        "coffee",
        "مطعم",
        "restaurant",
        "restaurants",
        "صيدلية",
        "pharmacy",
        "pharmacies",
        "فندق",
        "hotel",
        "hotels",
        "جيم",
        "gym",
        "gyms",
        "مول",
        "mall",
        "malls",
        "عيادة",
        "clinic",
        "clinics",
        "محل",
        "shop",
        "shops",
    ]
    return any(word in text for word in triggers)


def detect_research_type(message: str) -> str:
    text = (message or "").strip().lower()

    if is_place_request(text):
        return "places"

    education_triggers = [
        "جامعة",
        "جامعات",
        "ماجستير",
        "ماجيستير",
        "ماستر",
        "بكالوريوس",
        "دكتوراه",
        "منحة",
        "قبول",
        "تقديم",
        "admission",
        "university",
        "universidad",
        "master",
        "masters",
        "phd",
        "bachelor",
        "degree",
        "ielts",
        "toefl",
        "روبوت",
        "روبوتكس",
        "robotics",
        "automation",
        "automatica",
        "robótica",
        "robotica",
    ]
    travel_triggers = [
        "سافر",
        "سفر",
        "فندق",
        "رحلة",
        "تأشيرة",
        "فيزا",
        "hotel",
        "trip",
        "travel",
        "visa",
    ]
    product_triggers = [
        "اشتري",
        "اشتريلي",
        "منتج",
        "منتجات",
        "سعر",
        "مقارنة أسعار",
        "buy",
        "price",
        "product",
        "products",
        "compare",
    ]
    news_triggers = [
        "خبر",
        "أخبار",
        "اليوم",
        "آخر",
        "اخر",
        "latest",
        "news",
        "update",
        "updates",
    ]

    if any(word in text for word in education_triggers):
        return "education"
    if any(word in text for word in travel_triggers):
        return "travel"
    if any(word in text for word in product_triggers):
        return "product"
    if any(word in text for word in news_triggers):
        return "news"
    return "general"


def extract_requested_result_count(message: str, default: int = 5) -> int:
    text = (message or "").strip().lower()

    if any(
        x in text
        for x in [
            "من هدول",
            "من بينهم",
            "من النتائج",
            "فيهم",
            "منهم",
            "الأفضل",
            "افضل",
            "best",
            "top",
        ]
    ):
        return 1

    arabic_number_words = {
        "واحد": 1,
        "وحدة": 1,
        "واحدة": 1,
        "اثنين": 2,
        "اتنين": 2,
        "اثنتين": 2,
        "ثلاثة": 3,
        "ثلاث": 3,
        "اربعة": 4,
        "اربع": 4,
        "أربع": 4,
        "أربعة": 4,
        "خمسة": 5,
        "ستة": 6,
        "سبعة": 7,
        "ثمانية": 8,
        "تسعة": 9,
        "عشرة": 10,
    }

    match = re.search(r"\b(\d+)\b", text)
    if match:
        try:
            return max(1, min(int(match.group(1)), 20))
        except Exception:
            pass

    for word, value in arabic_number_words.items():
        if word in text:
            return value

    single_result_triggers = [
        "أفضل جامعة",
        "افضل جامعة",
        "أرخص جامعة",
        "ارخص جامعة",
        "جامعة وحدة",
        "جامعة واحدة",
        "صفحة وحدة",
        "صفحة واحدة",
        "أفضل صفحة",
        "افضل صفحة",
        "أرخص صفحة",
        "ارخص صفحة",
        "best university",
        "cheapest university",
        "one university",
        "single university",
        "one page",
        "single page",
        "best page",
        "cheapest page",
    ]
    if any(t.lower() in text for t in single_result_triggers):
        return 1

    if any(
        x in text
        for x in ["لخص", "لخصي", "لخصها", "summary", "summarize", "brief", "تلخيص"]
    ):
        return 3

    if any(
        x in text for x in ["أخبار", "اخبار", "news", "latest", "updates", "update"]
    ):
        return 5

    return default


def is_research_request(message: str) -> bool:
    text = (message or "").strip().lower()
    if is_place_request(text):
        return False
    # Strong triggers: an explicit search / news / study word — always research.
    strong_triggers = [
        "ابحث",
        "ابحثي",
        "ابحثو",
        "دوري",
        "دورلي",
        "دور لي",
        "research",
        "find",
        "search",
        "أخبار",
        "اخبار",
        "خبر",
        "آخر أخبار",
        "اخر اخبار",
        "لخص",
        "لخصي",
        "لخصيها",
        "لخصها",
        "جامعة",
        "جامعات",
        "ماجستير",
        "masters",
        "robotics",
        "قارن",
        "قارنة",
        "منحة",
        "قبول",
        "admission",
        "ielts",
        "toefl",
    ]
    if any(t in text for t in strong_triggers):
        return True
    # Generic question words fire a search only for a real question, not casual
    # chat — "كيف حالك" stays a chat, "ما هو أفضل لابتوب للبرمجة" is research.
    generic_triggers = ["ما هو", "ما هي", "كيف", "why", "what is", "who is"]
    if len(text.split()) >= 4 and any(t in text for t in generic_triggers):
        return True
    return False


def is_research_followup_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    triggers = [
        "من هدول",
        "من بينهم",
        "من النتائج",
        "فيهم",
        "منهم",
        "الأفضل",
        "افضل",
        "الأحسن",
        "احسن",
        "best",
        "top",
        "which of these",
        "from these",
        "among them",
        "among these",
    ]
    return any(t in text for t in triggers)
