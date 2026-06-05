"""AI image-action planner for Sandy.

Decides whether a user message is requesting a new image, an edit, a variation,
a description, clarification, or nothing image-related at all.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from app.features.image_agent import ensure_image_state, _recent_image_history_text

_DIRECT_COMMAND_RE = re.compile(r"^(?:/image|/img)\s*", flags=re.IGNORECASE)


def _safe_json_loads(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}
    return {}


def _extract_direct_command_prompt(user_message: str) -> str:
    return _DIRECT_COMMAND_RE.sub("", (user_message or "").strip()).strip()


def _fallback_new_image_prompt(text: str) -> str:
    return (text or "").strip()


def _fallback_edit_image_prompt(
    text: str, active_image: Optional[Dict[str, Any]]
) -> str:
    previous_prompt = str(
        (active_image or {}).get("generation_prompt", "") or ""
    ).strip()
    text = (text or "").strip()
    if previous_prompt:
        return (
            f"Keep the same overall style, composition, and main subject from this previous image prompt: "
            f"{previous_prompt}. "
            f"Apply this user change/request: {text}. "
            f"Only change what the user asked for."
        )
    return text


_IMAGE_PLANNER_SYSTEM = (
    "You are Sandy's image-intent planner. Return strict JSON only. "
    "Decide whether the user is asking to create a brand new image, modify the last generated image, request a variation, ask for clarification, or is not talking about images at all. "
    "Output fields exactly: handled (bool), action (string), generation_prompt (string), short_caption_ar (string), needs_followup (bool), followup_question (string). "
    "Valid action values: none, generate_new, edit_last, variation, describe_last, clarify. "
    "Rules: "
    "1) If the message is ordinary chat and not about image generation or continuation, handled=false. "
    "2) Having an active_image does NOT mean the next image-related message is automatically an edit. "
    "3) If the user asks for a clearly new subject, scene, or concept, choose generate_new even if there is an active image. "
    "4) Choose edit_last only when the user clearly refers to the previous image: خليها, عدلها, نفس الصورة, نفسها, شيل منها, زود عليها, نفس الفكرة but changed. "
    "5) Choose variation when the user wants another version: نسخة ثانية, variation, another version, واحدة ثانية من نفس الفكرة. "
    "6) Choose describe_last ONLY when user asks to describe or explain the current image: اوصفها, وصفيها, شو فيها, شو في الصورة, احكيلي عنها, describe it, what's in it. Do NOT treat these as edit requests. "
    "7) If the user message introduces a new main subject or standalone scene, treat it as generate_new, not edit_last. "
    "8) Example: active image is a cartoon cat. User says 'بدي صورة مدينة مستقبلية' => generate_new. "
    "9) Example: active image is a cartoon cat. User says 'خليها بمدينة' => edit_last. "
    "10) Example: user says 'اوصفيها' or 'شو فيها' => describe_last. "
    "11) Example: user says 'اعمل نسخة ثانية' => variation. "
    "12) The generation_prompt must be a fully self-contained English image prompt. "
    "13) If modifying the last image, preserve the previous style unless user explicitly changes it. "
    "14) If the request is too vague, choose clarify and ask one short Arabic question. "
    "15) If pending_image_action exists and the new message answers it, resolve it. "
    "16) short_caption_ar should be a very short natural Arabic description of the intended image. "
    "17) If pending_image_action exists and user shows clear cancellation intent (e.g. 'ما بدي', 'بطل', 'ألغي', 'خلاص', 'لا'), set action=none and handled=false so chat takes over — do NOT ask the same followup again. "
    "18) If pending_image_action exists and user clearly wants to generate as-is (e.g. 'انشئها', 'اعمليها', 'ولّدها', 'اعطيني ياها بدون تعديل', 'هاي تمام'), set action=generate_new with the same prompt from active_image. "
    "19) If pending_image_action exists and user asks for a completely DIFFERENT new image (different subject), set action=generate_new with the new prompt — do NOT treat as edit. "
    "20) Never loop: if you already asked the same followup_question recently and user response is vague, set action=generate_new from best available context instead of asking again. "
)


def plan_image_action_with_ai(
    user_message: str,
    *,
    session: Optional[Dict[str, Any]],
    create_chat_completion_fn=None,
) -> Dict[str, Any]:
    text = (user_message or "").strip()
    if not text or create_chat_completion_fn is None:
        return {"handled": False}

    image_state = ensure_image_state(session)
    active_image = image_state.get("active_image") or {}
    pending_image_action = image_state.get("pending_image_action") or {}
    recent_history_text = _recent_image_history_text(image_state)
    direct_prompt = _extract_direct_command_prompt(text)
    is_direct_command = bool(_DIRECT_COMMAND_RE.match(text))

    if is_direct_command:
        if not direct_prompt:
            return {
                "handled": True,
                "action": "clarify",
                "needs_followup": True,
                "followup_question": "اكتب وصف الصورة بعد الأمر. مثال: /image قطة كرتونية تلبس نظارات خضرا",
                "generation_prompt": "",
                "short_caption_ar": "",
            }
        return {
            "handled": True,
            "action": "generate_new",
            "needs_followup": False,
            "followup_question": "",
            "generation_prompt": direct_prompt,
            "short_caption_ar": direct_prompt,
        }

    try:
        response = create_chat_completion_fn(
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
            prefer_azure=True,
            messages=[
                {"role": "system", "content": _IMAGE_PLANNER_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"active_image={json.dumps(active_image, ensure_ascii=False)}\n"
                        f"recent_history={recent_history_text}\n"
                        f"pending_image_action={json.dumps(pending_image_action, ensure_ascii=False)}\n"
                        f"message={text}"
                    ),
                },
            ],
        )
        payload = _safe_json_loads(response.choices[0].message.content or "{}")
    except Exception as e:
        print(f"[ImagePlanner] planner failed: {e}")
        return {"handled": False}

    handled = bool(payload.get("handled", False))
    action = str(payload.get("action", "none") or "none").strip().lower()
    if action not in {
        "none",
        "generate_new",
        "edit_last",
        "variation",
        "describe_last",
        "clarify",
    }:
        action = "none"

    generation_prompt = str(payload.get("generation_prompt", "") or "").strip()
    short_caption_ar = str(payload.get("short_caption_ar", "") or "").strip()
    needs_followup = bool(payload.get("needs_followup", False))
    followup_question = str(payload.get("followup_question", "") or "").strip()

    if handled:
        if (
            action == "generate_new"
            and not generation_prompt
            and text
            and len(text.split()) >= 2
        ):
            generation_prompt = _fallback_new_image_prompt(text)
            short_caption_ar = short_caption_ar or text
            needs_followup = False
            followup_question = ""

        elif action in {"edit_last", "variation"}:
            if not active_image:
                action = "clarify"
                generation_prompt = ""
                needs_followup = True
                followup_question = (
                    "ما عندي صورة سابقة أعدل عليها. ابعت وصف صورة جديدة."
                )
            elif not generation_prompt and text:
                generation_prompt = _fallback_edit_image_prompt(text, active_image)
                short_caption_ar = short_caption_ar or text
                needs_followup = False
                followup_question = ""

    return {
        "handled": handled,
        "action": action,
        "generation_prompt": generation_prompt,
        "short_caption_ar": short_caption_ar,
        "needs_followup": needs_followup,
        "followup_question": followup_question,
    }
