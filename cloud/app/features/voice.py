import os
import re
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.utils.text import (
    extract_reaction_and_clean_text,
    prepare_tts_text,
)
from app.utils.thread_pool import sandy_executor
from app.integrations.gemini_tts import synthesize_voice_with_gemini
from app.integrations.google_tts import synthesize_voice_with_google
from app.integrations.azure_speech import synthesize_voice_with_azure
from app.utils.user_profiles import address_instruction

_TTS_MAX_CHARS = 120        # فوق هاد → Gemini يلخّص للصوت (للأدوات المؤهلة فقط)
_TTS_VOICE_BUDGET = 240     # سقف التقصير الحتمي (fallback لو فشل التلخيص)

# B6: شغّل Gemini + Google بالتوازي ونفضّل Gemini لو نجح، بدل التسلسل.
# "0" = السلوك القديم (تسلسلي). افتراضياً مفعّل لتقليل اللاتنسي عند تعثّر Gemini.
_TTS_PARALLEL = os.getenv("SANDY_TTS_PARALLEL", "1").strip().lower() not in {
    "0", "false", "off", "no",
}

_GEMINI_SUMMARIZE_TOOLS = {
    "research_web", "research_places", "fetch_url",
    "email_read", "email_draft", "email_reply",
    "chat_respond", "chat_emotional",
    "memory_recall", "memory_store",
    "github_commits", "github_issues", "github_create_issue", "github_file", "github_info",
    "heroku_info", "cost_report", "get_weather",
}

# الأدوات اللي محتواها فني/شعري — لازم يُتلى كما هو، ما يُلخّص أبداً
_NEVER_SUMMARIZE_TOOLS = {
    "digital_gift",
}

# تقسيم على نهايات الجمل (عربي + لاتيني) أو أسطر جديدة
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…])\s+|\n+")


def _shorten_for_tts(text: str, budget: int = _TTS_VOICE_BUDGET) -> str:
    """تقصير حتمي sentence-aware — يُستخدم كـfallback لو فشل التلخيص (بدّل قص [:120])."""
    text = (text or "").strip()
    if len(text) <= budget:
        return text
    out = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if out and len(out) + len(sentence) + 1 > budget:
            break
        out = f"{out} {sentence}".strip() if out else sentence
        if len(out) >= budget:
            break
    # جملة وحدة أطول من السقف → قص على حدود كلمة (أو قص صلب لو بلا مسافات)
    if len(out) > budget:
        head = out[:budget]
        out = head.rsplit(" ", 1)[0] if " " in head else head
    return out or text[:budget]


def _summarize_for_tts(text: str, mood: str = "neutral", user_message: str = "") -> str:
    """يولّد ملخص صوتي طبيعي عبر Gemini — جملتين مثالياً.
    يحاول مرة وحدة قبل الرجوع للتقصير الحتمي."""
    question_context = f"سؤال المستخدم: {user_message}\n\n" if user_message else ""
    mood_hint = f"النبرة: {mood}.\n" if mood and mood != "neutral" else ""

    prompt = (
        "أنتِ Sandy، مساعدة صوتية. لخّصي الرد التالي بالعامية الفلسطينية للنطق الصوتي.\n\n"
        "مثال:\n"
        "سؤال: شو أخبار الذكاء الاصطناعي؟\n"
        "ملخص: Anthropic راحت تحقق بمشاكل كلود بعد شكاوى المستخدمين، وجوجل أطلقت Gemini 2.5 Pro الأسبوع الماضي.\n\n"
        "القواعد: لخّص بأقل عدد جمل ممكن دون خسارة معلومة مهمة. "
        "لا تكرر نفسك ولا تطوّل بدون فائدة. "
        "لا مقدمات، لا 'خليني أحكيلك'، ابدأ مباشرة بالمعلومة الأهم. "
        f"كل جملة تبدأها تكملها. {address_instruction()}\n\n"
        f"{mood_hint}"
        f"{question_context}"
        f"الرد:\n{text}"
    )

    try:
        from app.integrations.azure_intent_client import AzureIntentClient
        client = AzureIntentClient()
        summary = client.generate_text(
            prompt,
            response_mime_type="text/plain",
            temperature=0.7,
            max_output_tokens=500,
        ).strip()
        if len(summary) >= 10:
            return summary
    except Exception as e:
        print(f"[Voice] TTS summarize failed: {e}")

    print("[Voice] TTS summarize fallback to deterministic shorten")
    return _shorten_for_tts(text)


def _remove_emojis(text: str) -> str:
    try:
        import emoji

        return emoji.replace_emoji(text, replace="")
    except ImportError:
        return text


def send_text_and_voice_reply(
    chat_id: int,
    text: str,
    *,
    telegram_bot: Any,
    agent_memory: Optional[Dict[str, Any]] = None,
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[Any] = None,
    set_last_assistant_reaction_fn: Optional[Callable[[Optional[str]], None]] = None,
    google_tts_voice: str = "",
    google_tts_language_code: str = "ar-XA",
    mood_tts_voices: Optional[Dict[str, str]] = None,
    azure_speech_available: bool = False,
    azure_speech_key: str = "",
    azure_speech_region: str = "",
    azure_speech_voice: str = "",
    skip_text_message: bool = False,
) -> None:
    # بتبعت رسالة نصية للمستخدم، ولو فيه صوت بتبعت كمان رد صوتي
    text = str(text or "[think] صار خلل وما رجع نص واضح.")

    # استخرج الرياكشن من الرد (إذا موجود)
    reaction, text_without_reaction = extract_reaction_and_clean_text(text)

    # حفظ الرياكشن في متغير خارجي (للهاردوير لاحقاً) إذا تم تمرير setter
    if set_last_assistant_reaction_fn:
        try:
            set_last_assistant_reaction_fn(reaction)
        except Exception as e:
            print(f"[Voice] failed to store reaction: {e}")

    if not skip_text_message:
        telegram_bot.send_message(
            chat_id,
            text_without_reaction,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
            parse_mode=None,
        )

    # كل بوت — إذا SANDY_VOICE_ENABLED=0 نوقّف الصوت كليّاً (نص فقط)
    if os.getenv("SANDY_VOICE_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return

    # TTS في background — النص وصل فوراً، الصوت يتبعه
    tts_text = prepare_tts_text(text_without_reaction)
    tts_text = _remove_emojis(tts_text)
    if not tts_text or len(tts_text.strip()) < 5:
        return

    agent_memory = agent_memory or {}
    current_state = agent_memory.get("sandy_state", {})
    current_mood = current_state.get("mood", "neutral")
    current_style = current_state.get("style", "normal")
    user_message = current_state.get("message", "")
    tool_name = (current_state.get("function_call") or {}).get("name", "")

    # لو الرد طويل والأداة مؤهلة → Gemini يلخّص مع سياق السؤال (في الـthread).
    # استثناء: digital_gift وأشباهه — المحتوى فني (شعر/نكتة/لغز) يتلى كاملاً.
    needs_summary = (
        len(tts_text) > _TTS_MAX_CHARS
        and tool_name not in _NEVER_SUMMARIZE_TOOLS
        and (not tool_name or tool_name in _GEMINI_SUMMARIZE_TOOLS)
    )

    def _gen_google(voice_text):
        return synthesize_voice_with_google(
            voice_text,
            mood=current_mood,
            style=current_style,
            google_tts_voice=google_tts_voice,
            google_tts_language_code=google_tts_language_code,
            mood_tts_voices=mood_tts_voices,
        )

    def _synthesize(voice_text):
        """Gemini أساسي، Google احتياطي، Azure ملاذ أخير.

        B6: في الوضع المتوازي نطلق Gemini + Google معاً ونفضّل Gemini لو نجح؛
        لو تعثّر، نتيجة Google تكون جاهزة بدون انتظار إضافي.
        """
        if _TTS_PARALLEL:
            gem_future = sandy_executor.submit(
                synthesize_voice_with_gemini, voice_text, mood=current_mood
            )
            goog_future = sandy_executor.submit(_gen_google, voice_text)
            try:
                audio = gem_future.result(timeout=15)
                if audio:
                    return audio
            except Exception:
                pass
            try:
                audio = goog_future.result(timeout=15)
                if audio:
                    return audio
            except Exception:
                pass
        else:
            audio = synthesize_voice_with_gemini(voice_text, mood=current_mood)
            if audio:
                return audio
            audio = _gen_google(voice_text)
            if audio:
                return audio

        # Azure Speech (last resort) — تسلسلي في الحالتين
        return synthesize_voice_with_azure(
            voice_text,
            azure_speech_available=azure_speech_available,
            azure_speech_key=azure_speech_key,
            azure_speech_region=azure_speech_region,
            azure_speech_voice=azure_speech_voice,
        )

    def _send_voice_bg():
        t0 = time.perf_counter()
        try:
            voice_text = tts_text
            if needs_summary:
                voice_text = _summarize_for_tts(
                    tts_text, mood=current_mood, user_message=user_message
                )
            audio_bytes = _synthesize(voice_text)
            elapsed = (time.perf_counter() - t0) * 1000
            if audio_bytes:
                telegram_bot.send_voice(chat_id, audio_bytes, timeout=120)
                print(f"[Voice] TTS: {elapsed:.0f}ms")
            else:
                print(f"[Voice] TTS no audio after {elapsed:.0f}ms")
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[Voice] background TTS failed ({elapsed:.0f}ms): {e}")

    threading.Thread(target=_send_voice_bg, daemon=True).start()
