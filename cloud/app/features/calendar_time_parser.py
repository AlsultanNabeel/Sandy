"""AI-powered time expression parser for reminders and calendar entries.

Public API: parse_reminder_time_ai(user_message, create_chat_completion_fn, return_json)
"""

import json
from datetime import datetime

from app.utils.arabic_days import resolve_day_name_to_iso, parse_date_from_text
from app.utils.nlp_normalizer import normalize_user_message
from app.utils.time import USER_TZ


def parse_reminder_time_ai(
    user_message: str,
    create_chat_completion_fn=None,
    return_json: bool = False,
):
    """Parse a user time expression into an ISO datetime string or structured JSON.

    Backwards compatible: by default returns an ISO datetime string (or None on failure).
    If `return_json=True` returns a dict with keys:
      - success: bool
      - remind_at_iso: str | None
      - intent: str (e.g. "reminder"/"calendar"/"task"/"unknown")
      - reason: str (explain failure)
      - original_text: str
    """
    if create_chat_completion_fn is None:
        suggested = parse_date_from_text(
            normalize_user_message(user_message) if user_message else ""
        )
        if return_json:
            return {
                "success": False,
                "remind_at_iso": None,
                "intent": "unknown",
                "reason": "no_completion_fn",
                "original_text": str(user_message or ""),
                "suggested_iso": suggested,
            }
        return None

    if not user_message:
        return None

    normalized_text = normalize_user_message(user_message)
    if not normalized_text:
        return None

    # Deterministic fast-path: if text names an Arabic weekday with no explicit
    # clock time, resolve it without calling the AI.
    det_iso = resolve_day_name_to_iso(normalized_text)
    if det_iso:
        now_check = datetime.now(USER_TZ)
        try:
            det_dt = datetime.fromisoformat(det_iso)
            if det_dt > now_check:
                print(f"[ParseAI] deterministic day parse -> {det_iso}", flush=True)
                if return_json:
                    return {
                        "success": True,
                        "remind_at_iso": det_iso,
                        "intent": "reminder",
                        "reason": "deterministic_day",
                        "original_text": user_message,
                    }
                return det_iso
        except Exception:
            pass

    now = datetime.now(USER_TZ)
    now_iso = now.isoformat()
    try:
        response = create_chat_completion_fn(
            temperature=0,
            max_tokens=140,
            response_format={"type": "json_object"},
            prefer_azure=True,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Convert reminder/time expressions to JSON only."
                        "Return fields: success (boolean), remind_at_iso (string|null), intent (one of 'reminder','calendar','task','unknown'), reason (string), original_text (string)."
                        "If no time can be inferred set success=false and provide a reason."
                        "If a date is given but no time, default to 09:00:00 in the user's timezone."
                        "Use the provided current datetime as reference and output full ISO format."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"current_datetime={now_iso}\n"
                        f"raw_text={user_message}\n"
                        f"normalized_text={normalized_text}"
                    ),
                },
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        success = bool(payload.get("success", False))
        iso_value = str(payload.get("remind_at_iso") or "").strip()
        intent_val = str(payload.get("intent") or "unknown").strip()
        reason_val = str(payload.get("reason") or "")
        original_text = str(payload.get("original_text") or user_message)

        if not success or not iso_value:
            print(f"[ParseAI] could not parse time: {reason_val or 'unknown'}")
            suggested = parse_date_from_text(normalized_text)
            if return_json:
                return {
                    "success": False,
                    "remind_at_iso": None,
                    "intent": intent_val,
                    "reason": reason_val or "no_iso",
                    "original_text": original_text,
                    "suggested_iso": suggested,
                }
            return None

        dt_value = datetime.fromisoformat(iso_value)
        cairo_tz = USER_TZ

        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=cairo_tz)
        else:
            dt_value = dt_value.astimezone(cairo_tz)

        if dt_value < now:
            print(f"[ParseAI] parsed time is in the past: {dt_value.isoformat()}")
            return None

        final_iso = dt_value.isoformat()
        print(f"[ParseAI] parsed by AI: {final_iso}")
        if return_json:
            return {
                "success": True,
                "remind_at_iso": final_iso,
                "intent": intent_val or "reminder",
                "reason": "parsed",
                "original_text": original_text,
            }
        return final_iso

    except Exception as e:
        print(f"[ParseAI] fallback parser failed: {e}")
        if return_json:
            return {
                "success": False,
                "remind_at_iso": None,
                "intent": "unknown",
                "reason": str(e),
                "original_text": user_message,
            }
        return None
