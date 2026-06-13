"""Per-chat profile storage for Sandy."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

from app.utils.files import read_json_file, write_json_file


def _parse_id_set(raw: str) -> set:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


_OWNER_IDS: set = _parse_id_set(os.getenv("OWNER_CHAT_ID", "")) | _parse_id_set(
    os.getenv("SANDY_USER_CHAT_ID", "")
)

# Keep these for any external code that still imports them directly
OWNER_CHAT_ID = (os.getenv("OWNER_CHAT_ID", "") or "").strip()
LEGACY_OWNER_CHAT_ID = (os.getenv("SANDY_USER_CHAT_ID", "") or "").strip()

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "memory"
USER_PROFILES_FILE = DATA_DIR / "user_profiles.json"
_ACTIVE_PROFILE_STATE = threading.local()

DEFAULT_TONE_BY_RELATION = {
    "owner": "casual",
    "family": "gentle",
    "guest": "formal",
}

DEFAULT_PERMISSIONS_BY_RELATION = {
    "owner": "all",
    "family": "chat-only",
    "guest": "chat-only",
}

SENSITIVE_KEYWORDS = (
    "task",
    "tasks",
    "مهام",
    "مهمة",
    "calendar",
    "تقويم",
    "موعد",
    "مواعيد",
    "email",
    "mail",
    "gmail",
    "بريد",
    "إيميل",
    "ايميل",
    "ذاكرة",
    "memory",
    "ذكرياتي",
    "تذكر",
    "ذكّر",
    "ذكرني",
)


def _chat_key(chat_id: Any) -> str:
    return str(chat_id).strip()


def is_owner_chat_id(chat_id: Any) -> bool:
    chat_key = _chat_key(chat_id)
    if not chat_key:
        return False
    return chat_key in (
        _parse_id_set(OWNER_CHAT_ID) | _parse_id_set(LEGACY_OWNER_CHAT_ID)
    )


def set_active_user_profile(profile: Optional[Dict[str, Any]]) -> None:
    _ACTIVE_PROFILE_STATE.profile = profile


def get_active_user_profile() -> Optional[Dict[str, Any]]:
    profile = getattr(_ACTIVE_PROFILE_STATE, "profile", None)
    return profile if isinstance(profile, dict) else None


@contextmanager
def active_user_profile_context(profile: Optional[Dict[str, Any]]):
    previous = get_active_user_profile()
    set_active_user_profile(profile)
    try:
        yield
    finally:
        set_active_user_profile(previous)


def active_profile_is_owner() -> bool:
    profile = get_active_user_profile()
    if not profile:
        return False
    relation = str(profile.get("relation", "guest") or "guest").strip().lower()
    permissions = (
        str(profile.get("permissions", "chat-only") or "chat-only").strip().lower()
    )
    return relation == "owner" or permissions == "all"


def address_instruction(profile: Optional[Dict[str, Any]] = None) -> str:
    """Arabic line telling Sandy which grammatical gender to address the current
    speaker with. The default speaker is the owner (male), so anything that
    isn't an explicitly-female profile resolves to masculine."""
    if profile is None:
        profile = get_active_user_profile()
    gender = str((profile or {}).get("gender", "") or "").strip().lower()
    if gender == "female":
        return "المتحدثة معك أنثى — خاطبيها بصيغة المؤنث."
    return (
        "المتحدث معك ذكر (المالك نبيل افتراضياً حتى يتعرّف على ضيف) — "
        "خاطبيه بصيغة المذكر."
    )


def active_profile_allows_privileged_access() -> bool:
    profile = get_active_user_profile()
    if profile is None:
        return False
    return active_profile_is_owner()


def active_profile_is_guest() -> bool:
    profile = get_active_user_profile()
    if not profile:
        return False
    return not active_profile_is_owner()


def _normalize_relation(value: str) -> str:
    relation = str(value or "guest").strip().lower()
    return relation if relation in {"owner", "family", "guest"} else "guest"


def _normalize_profile(
    chat_id: Any, profile: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    relation = "owner" if is_owner_chat_id(chat_id) else "guest"
    normalized = {
        "chat_id": _chat_key(chat_id),
        "name": "",
        "relation": relation,
        "tone": DEFAULT_TONE_BY_RELATION[relation],
        "permissions": DEFAULT_PERMISSIONS_BY_RELATION[relation],
        "gender": "",
    }

    if isinstance(profile, dict):
        normalized["name"] = str(profile.get("name", "") or "").strip()
        normalized["relation"] = _normalize_relation(profile.get("relation", relation))
        normalized["tone"] = str(profile.get("tone", "") or "").strip().lower()
        normalized["permissions"] = (
            str(profile.get("permissions", "") or "").strip().lower()
        )
        _g = str(profile.get("gender", "") or "").strip().lower()
        normalized["gender"] = _g if _g in {"male", "female"} else ""

    if is_owner_chat_id(chat_id):
        normalized["relation"] = "owner"
        normalized["tone"] = "casual"
        normalized["permissions"] = "all"
        normalized["gender"] = "male"  # the owner is male — the default speaker
    else:
        normalized["relation"] = _normalize_relation(normalized["relation"])
        normalized["tone"] = (
            normalized["tone"]
            if normalized["tone"] in {"casual", "gentle", "formal"}
            else DEFAULT_TONE_BY_RELATION[normalized["relation"]]
        )
        normalized["permissions"] = "chat-only"

    return normalized


def _default_profile(chat_id: Any) -> Dict[str, Any]:
    return _normalize_profile(chat_id, None)


def _read_json_profiles() -> Dict[str, Dict[str, Any]]:
    raw = read_json_file(USER_PROFILES_FILE, {})
    return raw if isinstance(raw, dict) else {}


def _write_json_profiles(profiles: Dict[str, Dict[str, Any]]) -> bool:
    return write_json_file(USER_PROFILES_FILE, profiles)


def find_user_profile(chat_id: Any, mongo_db: Any = None) -> Optional[Dict[str, Any]]:
    chat_key = _chat_key(chat_id)
    if not chat_key:
        return None

    if is_owner_chat_id(chat_key):
        return _default_profile(chat_key)

    if mongo_db is not None:
        try:
            doc = mongo_db["user_profiles"].find_one({"_id": chat_key})
            if doc:
                doc = dict(doc)
                doc.pop("_id", None)
                return _normalize_profile(chat_key, doc)
        except Exception:
            pass

    profiles = _read_json_profiles()
    profile = profiles.get(chat_key)
    if profile:
        return _normalize_profile(chat_key, profile)
    return None


def save_user_profile(
    chat_id: Any, profile: Dict[str, Any], mongo_db: Any = None
) -> Dict[str, Any]:
    chat_key = _chat_key(chat_id)
    normalized = _normalize_profile(chat_id, profile)

    if mongo_db is not None:
        try:
            mongo_db["user_profiles"].replace_one(
                {"_id": chat_key},
                {**normalized, "_id": chat_key},
                upsert=True,
            )
            return normalized
        except Exception:
            pass

    profiles = _read_json_profiles()
    profiles[chat_key] = normalized
    _write_json_profiles(profiles)
    return normalized


def ensure_user_profile(
    chat_id: Any, mongo_db: Any = None
) -> Tuple[Dict[str, Any], bool]:
    existing = find_user_profile(chat_id, mongo_db=mongo_db)
    if existing is not None:
        return existing, False
    created = _default_profile(chat_id)
    return save_user_profile(chat_id, created, mongo_db=mongo_db), True


def update_user_profile(
    chat_id: Any, updates: Dict[str, Any], mongo_db: Any = None
) -> Dict[str, Any]:
    profile, _ = ensure_user_profile(chat_id, mongo_db=mongo_db)
    merged = {**profile, **(updates or {})}
    return save_user_profile(chat_id, merged, mongo_db=mongo_db)


def extract_profile_name(message_text: str) -> str:
    text = str(message_text or "").strip()
    if not text:
        return ""

    patterns = [
        r"^(?:اسمي|أنا اسمي|انا اسمي|اسمي هو|أنا هو|انا هو|my name is)\s*[:=\-–]?\s*(.+)$",
        r"^(?:أنا|انا)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if candidate and len(candidate.split()) <= 3 and len(candidate) <= 40:
                return candidate

    words = text.split()
    if len(words) == 1 and len(text) <= 30 and not any(ch in text for ch in "؟?!.،,"):
        return text

    return ""


def is_sensitive_request(message_text: str) -> bool:
    text = str(message_text or "").lower()
    for keyword in SENSITIVE_KEYWORDS:
        # Latin keywords (task/email/...) get word-boundary matching so they don't
        # false-fire inside longer words. Arabic keywords keep substring matching:
        # Arabic word boundaries are unreliable, and this is a safety gate where we
        # deliberately err toward over-blocking guests.
        if keyword.isascii():
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return True
        elif keyword in text:
            return True
    return False


def build_user_profile_prompt_sections(
    profile: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    if not profile:
        return {"user_profile_block": "", "user_profile_priority_line": ""}

    normalized = _normalize_profile(profile.get("chat_id", ""), profile)
    tone_text = {
        "casual": "ردّي بأسلوب قريب وخفيف ومباشر، مع ألفاظ مألوفة بدون تكلف.",
        "gentle": "ردّي بلطف وهدوء، مع احترام واضح ولمسة ودّية.",
        "formal": "ردّي بصياغة مهذبة ورسمية ومختصرة.",
    }[normalized["tone"]]

    profile_block = (
        "\n👤 ملف المستخدم الحالي:\n"
        f"- الاسم: {normalized['name'] or 'غير معروف'}\n"
        f"- العلاقة: {normalized['relation']}\n"
        f"- النبرة: {normalized['tone']}\n"
        f"- الصلاحيات: {normalized['permissions']}\n"
        f"- توجيه النبرة: {tone_text}\n"
    )

    privacy_line = ""
    if normalized["permissions"] != "all":
        privacy_line = (
            "\n🔒 هذا الحساب chat-only: لا تنفذي أو تذكري أي تفاصيل من المهام أو التقويم أو البريد أو الذاكرة. "
            "إذا طُلب شيء من هذه المجالات، ارجعي فقط إلى: هذا خاص بنبيل 😊\n"
        )

    return {
        "user_profile_block": profile_block,
        "user_profile_priority_line": privacy_line,
    }


def is_sensitive_domain_request(message_text: str) -> bool:
    # Public alias kept for callers (telegram_handlers, tests); the canonical
    # implementation is is_sensitive_request.
    return is_sensitive_request(message_text)
