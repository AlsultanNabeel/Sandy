from typing import Any, Dict


from app.utils.nlp_normalizer import normalize_user_message
from app.agent.pending import create_pending_action, clear_pending_action


def _task_choice_index(text: str):
    value = normalize_user_message(str(text or "").strip()).lower()
    value = value.translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    )
    value = (
        value.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
        .replace("ى", "ي")
    )
    value = " ".join(value.split())

    if not value:
        return None

    if value == "!":
        return 0

    if value.isdigit():
        return int(value) - 1

    first_words = {
        "اول",
        "الاول",
        "اولى",
        "الاولى",
        "اولي",
        "الاولي",
        "الاولاني",
        "الاولانية",
        "اولاني",
        "اولانية",
        "اول واحد",
        "اول وحده",
        "اول وحدة",
        "المهمة الاولى",
        "المهمه الاولى",
        "المهمة الاولي",
        "المهمه الاولي",
        "اول مهمة",
        "اول مهمه",
        "المهمة الاولانية",
        "المهمه الاولانيه",
    }
    second_words = {
        "ثاني",
        "الثاني",
        "ثانية",
        "الثانية",
        "تاني",
        "التاني",
        "تانية",
        "التانية",
        "ثاني مهمة",
        "تاني مهمة",
        "المهمة الثانية",
        "المهمه التانية",
    }

    third_words = {
        "ثالث",
        "الثالث",
        "ثالثة",
        "الثالثة",
        "تالت",
        "التالت",
        "تالتة",
        "التالتة",
        "ثالث مهمة",
        "تالت مهمة",
        "المهمة الثالثة",
        "المهمه التالتة",
    }

    fourth_words = {
        "رابع",
        "الرابع",
        "رابعة",
        "الرابعة",
        "رابع مهمة",
        "المهمة الرابعة",
        "المهمه الرابعة",
    }

    fifth_words = {
        "خامس",
        "الخامس",
        "خامسة",
        "الخامسة",
        "خامس مهمة",
        "المهمة الخامسة",
        "المهمه الخامسة",
    }

    groups = [first_words, second_words, third_words, fourth_words, fifth_words]

    for index, words in enumerate(groups):
        if value in words:
            return index

    if "رقم " in value:
        maybe_number = value.split("رقم ", 1)[1].strip()
        if maybe_number.isdigit():
            return int(maybe_number) - 1

    return None


def _task_choice_pair_indexes(text: str, choices_count: int):
    value = normalize_user_message(str(text or "").strip()).lower()
    value = value.translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    )
    value = (
        value.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
    )
    value = " ".join(value.split())

    pair_words = {
        "التنتين",
        "التنين",
        "الاتنين",
        "الثنتين",
        "الاثنين",
        "اتنين",
        "اثنين",
        "التنين مع بعض",
        "الاتنين مع بعض",
        "التنتين مع بعض",
    }

    if choices_count == 2 and value in pair_words:
        return [0, 1]

    return None


def _has_visible_task_note(task: Dict[str, Any]) -> bool:
    notes = str(task.get("notes", "") or "").strip()
    if not notes:
        return False

    visible_lines = [
        line.strip()
        for line in notes.splitlines()
        if line.strip()
        and not (line.strip().startswith("[SANDY_") and line.strip().endswith("]"))
    ]

    return bool(visible_lines)


def _is_quick_confirmation(text: str) -> bool:
    """True for a short yes/confirm on an operational decision (not a pending reply)."""
    return text.strip().lower() in {
        "اه",
        "أه",
        "نعم",
        "ايوه",
        "أيوا",
        "اكيد",
        "أكيد",
        "yes",
        "ok",
        "okay",
        "تمام",
        "احذف",
        "احذفهم",
        "confirmed",
    }


def is_cancellation(text: str) -> bool:
    """Returns True when the user wants to cancel/reject a pending action."""
    import re

    normalized = " ".join(text.strip().lower().split())
    patterns = [
        r"^(لا|لأ|الغ|إلغاء|مش|خلص|لا وقت)$",
        r"^(no|cancel|nope|dont|stop)$",
        r"(لا تحذف|مش الآن|انسى|انسي|وقف|وقفي)",
        r"(الغي|الغيها|الغيهم|لا تضيفيها|لا تضيفها|لا تضيف)",
    ]
    return any(re.search(p, normalized) for p in patterns)


def _handle_modify_response(
    *,
    user_message: str,
    pending: Dict[str, Any],
    pending_type: str,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    """User wants to fix something. Ask for the right field based on pending_type."""

    if pending_type == "reminder":
        # Ask for the new date/time.
        session["pending_action"] = create_pending_action(
            {
                "type": "reminder",
                "action": "awaiting_corrected_date",
                "original_action": pending.get("action", ""),
                "original_data": pending,
                "correction_step": 1,
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        return {
            "handled": True,
            "reply": "تمام، قول لي التاريخ والساعة الصحيحة للتذكير؟\nمثلاً: غدا عند الساعة 3 أو الجمعة عند 9 صباح",
        }

    elif pending_type == "task":
        # Ask which field to change.
        session["pending_action"] = create_pending_action(
            {
                "type": "task",
                "action": "awaiting_field_to_modify",
                "original_action": pending.get("action", ""),
                "original_data": pending,
                "correction_step": 1,
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        task_text = pending.get("text", "المهمة")
        return {
            "handled": True,
            "reply": f"متأكد، بدك تعدّل شنو من المهمة؟\nالمهمة: {task_text}\n\nبدك تعدّل: الاسم، التاريخ، الملاحظة، أو الأولوية؟",
        }

    elif pending_type == "calendar":
        # Ask which field to change.
        session["pending_action"] = create_pending_action(
            {
                "type": "calendar",
                "action": "awaiting_field_to_modify",
                "original_action": pending.get("action", ""),
                "original_data": pending,
                "correction_step": 1,
            }
        )
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        event_title = pending.get("title", "الحدث")
        return {
            "handled": True,
            "reply": f"متأكد، بدك تعدّل شنو من الحدث؟\nالحدث: {event_title}\n\nبدك تعدّل: الوقت، التاريخ، الموقع، أو الوصف؟",
        }

    else:
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {
            "handled": True,
            "reply": "ما قدرت أكمل التعديل. جرّب من جديد.",
        }
