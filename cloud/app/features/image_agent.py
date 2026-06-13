import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from app.utils.user_profiles import address_instruction


def _lazy_plan_image_action(user_message, *, session, create_chat_completion_fn):
    from app.features.image_planner import plan_image_action_with_ai

    return plan_image_action_with_ai(
        user_message,
        session=session,
        create_chat_completion_fn=create_chat_completion_fn,
    )


_PHOTO_EDIT_KEYWORDS = [
    "عدل",
    "حط",
    "شيل",
    "اشيل",
    "اشيلي",
    "حطي",
    "غيري",
    "عدلي",
    "اضف",
    "أضف",
    "زود",
    "زودي",
    "اجعل",
    "اجعلي",
    "خلي",
    "خليها",
    "خليه",
    "اعمل",
    "اعملي",
    "ابعد",
    "احذف",
    "امسح",
    "لون",
    "لوني",
    "إطار",
    "اطار",
]


def _default_image_state() -> Dict[str, Any]:
    return {
        "active_image": None,
        "active_image_bytes": None,
        "history": [],
        "pending_image_action": None,
    }


def ensure_image_state(session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session, dict):
        return _default_image_state()
    image_state = session.get("image_state")
    if not isinstance(image_state, dict):
        image_state = _default_image_state()
        session["image_state"] = image_state
    image_state.setdefault("active_image", None)
    image_state.setdefault("active_image_bytes", None)
    image_state.setdefault("history", [])
    image_state.setdefault("pending_image_action", None)
    return image_state


_ARABIC_DIACRITICS = re.compile(r"[ً-ٟؐ-ؚ]")


def is_photo_edit_caption(caption: str) -> bool:
    """Return True if the photo caption is requesting an edit (not just description)."""
    text = _ARABIC_DIACRITICS.sub("", (caption or "").strip())
    if not text:
        return False
    return any(kw in text for kw in _PHOTO_EDIT_KEYWORDS)


def _recent_image_history_text(image_state: Optional[Dict[str, Any]]) -> str:
    history = (image_state or {}).get("history") or []
    if not isinstance(history, list):
        return "[]"
    tail = []
    for item in history[-3:]:
        if not isinstance(item, dict):
            continue
        tail.append(
            {
                "user_request": item.get("user_request", ""),
                "short_caption_ar": item.get("short_caption_ar", ""),
                "action": item.get("action", ""),
                "created_at": item.get("created_at", ""),
            }
        )
    return json.dumps(tail, ensure_ascii=False)


def render_image_reply_with_ai(
    *,
    create_chat_completion_fn,
    user_message: str,
    plan: Optional[Dict[str, Any]] = None,
    success: bool,
    fallback_text: str,
) -> str:
    if create_chat_completion_fn is None:
        return fallback_text

    plan = plan or {}

    try:
        response = create_chat_completion_fn(
            temperature=0.9,
            max_tokens=140,
            prefer_azure=True,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Sandy, a warm physical robot companion talking by voice. "
                        "Write ONE short natural Arabic reply (1-2 sentences) by Palestinian dialect. "
                        "Sound like a real friend, NEVER robotic, never use [tags], minimal emojis. "
                        "VARY your wording every time — do not repeat the same opening. "
                        + address_instruction() + " "
                        + "Behavior by action: "
                        "  • action=generate_new/edit_last/variation AND success=true: "
                        "    – mention the subject naturally (use short_caption_ar as hint, don't copy verbatim). "
                        "    – mention you sent the image to Telegram (vary wording: 'بعتها', 'وصلتك ع التيلي', 'شوفها بالشات', 'حمّلتلك ياها'). "
                        "  • action=describe_last AND success=true: "
                        "    – describe the image content naturally in 1-2 sentences. "
                        "    – DO NOT mention sending the image (the image is already shown — no need to say 'بعتها'). "
                        "  • success=false: brief, clear, helpful. "
                        "Tone: warm, occasional gentle endearments but NOT every time. "
                        "Return ONLY the final Arabic reply text, no quotes, no JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"user_message={user_message}\n"
                        f"success={success}\n"
                        f"action={plan.get('action', '')}\n"
                        f"short_caption_ar={plan.get('short_caption_ar', '')}\n"
                        f"fallback_hint={fallback_text}"
                    ),
                },
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        text = re.sub(
            r"\[(happy|think|sad|angry|calm|excited)\]\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        return text or fallback_text
    except Exception as e:
        print(f"[ImageReply] AI reply render failed: {e}")
        return fallback_text


def handle_image_message(
    user_message: str,
    *,
    session: Optional[Dict[str, Any]],
    create_chat_completion_fn,
    generate_image_with_azure_fn,
    azure_openai_client: Any,
    azure_openai_image_deployment: Optional[str],
    size: str = "1024x1024",
) -> Dict[str, Any]:
    image_state = ensure_image_state(session)
    plan = _lazy_plan_image_action(
        user_message,
        session=session,
        create_chat_completion_fn=create_chat_completion_fn,
    )

    if not plan.get("handled"):
        return {"handled": False}
    if plan.get("action") == "describe_last":
        active_image = image_state.get("active_image") or {}
        if not active_image:
            image_state["pending_image_action"] = None
            return {
                "handled": True,
                "success": False,
                "needs_followup": True,
                "text_only": True,
                "reply_text": "ما عندي صورة سابقة أوصفها. ابعت صورة أو اطلب توليد صورة جديدة.",
                "plan": plan,
            }

        image_state["pending_image_action"] = None

        description_seed = active_image.get("short_caption_ar") or active_image.get(
            "user_request"
        )

        reply_text = render_image_reply_with_ai(
            create_chat_completion_fn=create_chat_completion_fn,
            user_message=user_message,
            plan={
                **plan,
                "short_caption_ar": description_seed,
            },
            success=True,
            fallback_text=f"الصورة السابقة كانت تقريبًا: {description_seed}",
        )

        return {
            "handled": True,
            "success": True,
            "needs_followup": False,
            "text_only": True,
            "reply_text": reply_text,
            "plan": plan,
        }

    if plan.get("needs_followup") or not plan.get("generation_prompt"):
        image_state["pending_image_action"] = {
            "last_user_message": (user_message or "").strip(),
            "asked_at": datetime.now().isoformat(),
            "followup_question": plan.get("followup_question", "").strip(),
        }
        followup_text = (
            plan.get("followup_question") or "شو الشكل أو التعديل اللي بدك ياه بالضبط؟"
        )

        return {
            "handled": True,
            "success": False,
            "needs_followup": True,
            "reply_text": render_image_reply_with_ai(
                create_chat_completion_fn=create_chat_completion_fn,
                user_message=user_message,
                plan=plan,
                success=False,
                fallback_text=followup_text,
            ),
        }

    image_bytes = generate_image_with_azure_fn(
        plan["generation_prompt"],
        azure_openai_client=azure_openai_client,
        azure_openai_image_deployment=azure_openai_image_deployment,
        size=size,
    )
    if not image_bytes:
        fallback_text = (
            "ما قدرت أولد الصورة حاليًا. جرّب تغيّر الوصف شوي أو أعد المحاولة."
        )

        return {
            "handled": True,
            "success": False,
            "needs_followup": False,
            "reply_text": render_image_reply_with_ai(
                create_chat_completion_fn=create_chat_completion_fn,
                user_message=user_message,
                plan=plan,
                success=False,
                fallback_text=fallback_text,
            ),
        }

    now_iso = datetime.now().isoformat()
    previous_active = image_state.get("active_image")
    current_entry = {
        "user_request": (user_message or "").strip(),
        "generation_prompt": plan["generation_prompt"],
        "short_caption_ar": plan.get("short_caption_ar")
        or (user_message or "").strip(),
        "action": plan.get("action", "generate_new"),
        "created_at": now_iso,
        "derived_from": (
            (previous_active or {}).get("created_at") if previous_active else None
        ),
    }

    history = image_state.get("history", [])
    if isinstance(history, list):
        history.append(current_entry)
        image_state["history"] = history[-12:]
    else:
        image_state["history"] = [current_entry]

    image_state["active_image"] = current_entry
    # احفظ الـ bytes كذلك حتى يقدر تعديل الصورة لاحقاً يجد المصدر
    image_state["active_image_bytes"] = image_bytes
    image_state["pending_image_action"] = None

    action = plan.get("action", "generate_new")
    if action == "edit_last":
        fallback_text = "تمام، عدّلت الصورة على نفس السياق."
    elif action == "variation":
        fallback_text = "جهزت نسخة جديدة بنفس الفكرة."
    else:
        fallback_text = "جهزت الصورة."

    reply_text = render_image_reply_with_ai(
        create_chat_completion_fn=create_chat_completion_fn,
        user_message=user_message,
        plan=plan,
        success=True,
        fallback_text=fallback_text,
    )

    return {
        "handled": True,
        "success": True,
        "needs_followup": False,
        "reply_text": reply_text,
        "image_bytes": image_bytes,
        "caption": current_entry["short_caption_ar"],
        "plan": plan,
    }
