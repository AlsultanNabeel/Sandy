"""Email confirm/edit-loop helpers for Sandy."""

from __future__ import annotations

import re
from typing import Dict, Optional

_DRAFT_REQUEST_RE = re.compile(
    r"مسودة|احفظ|draft|حفظ|save",
    re.IGNORECASE | re.UNICODE,
)

_FIELD_RE = re.compile(
    r"^(?P<field>(?:الموضوع|موضوع|subject|إلى|الى|البريد|to|email|المحتوى|الجسم|الرسالة|body))\s*[:؟]\s*(?P<value>.+)",
    re.IGNORECASE | re.UNICODE | re.DOTALL,
)

_CHANGE_RE = re.compile(
    r"(?:غيّر|غير|عدّل|عدل|بدّل|بدل|change|update)\s+"
    r"(?P<field>الموضوع|موضوع|subject|المستلم|البريد|to|email|المحتوى|الجسم|body|الرسالة)\s+"
    r"(?:إلى|الى|ل|ب|to|:)?\s*(?P<value>.+)",
    re.IGNORECASE | re.UNICODE | re.DOTALL,
)

_APPEND_RE = re.compile(
    r"(?:أضف|اضف|append|زد)\s+(?P<value>.+)",
    re.IGNORECASE | re.UNICODE | re.DOTALL,
)

_FIELD_MAP = {
    "الموضوع": "subject",
    "موضوع": "subject",
    "subject": "subject",
    "إلى": "to",
    "الى": "to",
    "البريد": "to",
    "to": "to",
    "email": "to",
    "المستلم": "to",
    "المحتوى": "body",
    "الجسم": "body",
    "الرسالة": "body",
    "body": "body",
}


def _is_draft_request(text: str) -> bool:
    return bool(_DRAFT_REQUEST_RE.search(text or ""))


def _parse_email_field_edit(
    user_msg: str,
    current_to: str,
    current_subject: str,
    current_body: str,
    create_chat_completion_fn=None,
) -> Optional[Dict[str, str]]:
    """
    Detect a field-edit instruction in user_msg.
    Returns {field: "to"|"subject"|"body", op: "replace"|"append", value: "..."}
    or None if not an edit.
    Uses regex first; falls back to a mini LLM call.
    """
    msg = (user_msg or "").strip()

    # Regex: "الموضوع: ..." or "المحتوى: ..."
    m = _FIELD_RE.match(msg)
    if m:
        raw_field = m.group("field").strip().lower()
        field = _FIELD_MAP.get(raw_field)
        if field:
            return {"field": field, "op": "replace", "value": m.group("value").strip()}

    # Regex: "غيّر الموضوع إلى ..."
    m = _CHANGE_RE.search(msg)
    if m:
        raw_field = m.group("field").strip().lower()
        field = _FIELD_MAP.get(raw_field)
        if field:
            return {"field": field, "op": "replace", "value": m.group("value").strip()}

    # Regex: "أضف ..." → append to body
    m = _APPEND_RE.match(msg)
    if m:
        return {"field": "body", "op": "append", "value": m.group("value").strip()}

    # LLM fallback for complex edits like "أضف في النهاية شكراً" or "ابدأ بتحية"
    if create_chat_completion_fn and len(msg) >= 5:
        try:
            resp = create_chat_completion_fn(
                temperature=0,
                max_tokens=120,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an email editor. Given a user instruction and the current email fields, "
                            'return JSON: {"field":"to"|"subject"|"body", "op":"replace"|"append", "value":"..."} '
                            'or {"field":null} if the message is not an edit instruction. Return valid JSON only.'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"current_to={current_to}\n"
                            f"current_subject={current_subject}\n"
                            f"current_body={current_body[:200]}\n"
                            f"instruction={msg}"
                        ),
                    },
                ],
            )
            import json as _json

            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = _json.loads(raw)
            if data.get("field") and data.get("value"):
                return {
                    "field": data["field"],
                    "op": str(data.get("op", "replace")),
                    "value": str(data["value"]).strip(),
                }
        except Exception:
            pass

    return None


def _render_email_preview(to: str, subject: str, body: str) -> str:
    """Format a draft email preview with confirm/edit/draft options."""
    lines = ["📬 *مراجعة الإيميل:*", f"• إلى: `{to}`"]
    if subject:
        lines.append(f"• الموضوع: {subject}")
    if body:
        preview = body[:400] + ("…" if len(body) > 400 else "")
        lines.append(f"• المحتوى:\n{preview}")
    lines.append(
        "\n📨 *ارسل؟ احفظه مسودة؟ أو عدّل؟*\n"
        "_(نعم/ارسل — مسودة — لا/الغي — أو اكتب تعديلك مباشرة)_"
    )
    return "\n".join(lines)
