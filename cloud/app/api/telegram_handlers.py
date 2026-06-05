import io
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from pymongo.write_concern import WriteConcern

from app.utils.rate_limiter import is_rate_limited
from app.utils.thread_pool import sandy_executor
from app.utils.error_tracking import log_unhandled_exception
from app.utils.user_profiles import (
    ensure_user_profile,
    extract_profile_name,
    find_user_profile,
    is_sensitive_domain_request,
    active_user_profile_context,
    update_user_profile,
)
from app.api.telegram_guards import (
    is_duplicate_telegram_message as _is_duplicate_telegram_message,
)

logger = logging.getLogger(__name__)

# جلسات تسجيل بصمة الصوت (Phase 4): chat_id → قائمة عيّنات PCM مجمّعة.
# مؤقتة في الذاكرة — تبدأ بـ /enroll_voice وتنتهي بـ /enroll_done أو /enroll_cancel.
_voice_enroll_sessions: Dict[int, list] = {}
_ENROLL_MIN_SAMPLES = 3


def _chat_action(telegram_bot: Any, chat_id: Any, action: str) -> None:
    """Show the 'typing' / 'upload' indicator without blocking the reply.

    send_chat_action is a Telegram round-trip and the indicator is just
    cosmetic, so we don't wait for it. Submit it to the pool instead (B12).
    """
    def _run():
        try:
            telegram_bot.send_chat_action(chat_id, action)
        except Exception:
            pass

    try:
        sandy_executor.submit(_run)
    except Exception:
        pass


def _enqueue_local_tts(mongo_db: Any, clean_text: str) -> None:
    """Drop a reply into sandy_local_queue for the Mac TTS listener.

    Runs off the reply path. The bounded write_concern means a slow Mongo
    won't hang the worker thread.
    """
    try:
        mongo_db["sandy_local_queue"].with_options(
            write_concern=WriteConcern(wtimeout=2000)
        ).insert_one(
            {
                "text": clean_text,
                "timestamp": datetime.now(timezone.utc),
                "played": False,
            }
        )
    except Exception as exc:
        logger.warning("[Mongo] sandy_local_queue insert failed: %s", exc)


def process_conflict_resolution_callback(
    *,
    telegram_bot: Any,
    agent: Any,
    call: Any,
    profile_allows_full_access_fn: Callable[[Any], bool],
    log_handler_exception_fn: Callable[..., None],
    persist_agent_session_fn: Optional[Callable[[], None]] = None,
) -> bool:
    data = str(getattr(call, "data", "") or "")
    if not data.startswith("conflict:"):
        return False

    try:
        chat_id = call.message.chat.id
        parts = data.split(":")
        if len(parts) < 3:
            telegram_bot.answer_callback_query(call.id, "زر غير صالح")
            return True

        action = parts[1].strip().lower()
        conflict_id = parts[2].strip()

        if not profile_allows_full_access_fn(chat_id):
            telegram_bot.answer_callback_query(call.id, "هذا خاص بنبيل 😊")
            return True

        pending = (getattr(agent, "session", None) or {}).get(
            "pending_conflict_resolution"
        ) or {}
        if str(pending.get("id", "") or "") != conflict_id:
            telegram_bot.answer_callback_query(call.id, "انتهت صلاحية هذا التنبيه")
            return True

        try:
            telegram_bot.edit_message_reply_markup(
                chat_id, call.message.message_id, reply_markup=None
            )
        except Exception:
            pass

        mongo_db = getattr(agent, "mongo_db", None)
        event_id = str(pending.get("event_id", "") or "").strip()
        title = str(pending.get("title", "") or "").strip() or "موعد"
        suggestions = list(pending.get("suggestions") or [])
        profile = find_user_profile(chat_id, mongo_db=mongo_db) or {
            "chat_id": str(chat_id),
            "relation": "owner",
            "permissions": "all",
            "tone": "casual",
        }

        chosen_index = 0
        if action == "pick":
            if len(parts) != 4:
                telegram_bot.answer_callback_query(call.id, "خيار غير صالح")
                return True
            try:
                chosen_index = int(parts[3].strip())
            except Exception:
                telegram_bot.answer_callback_query(call.id, "خيار غير صالح")
                return True
            action = "yes"

        if action == "yes":
            if not event_id or not suggestions:
                telegram_bot.answer_callback_query(call.id, "لا يوجد اقتراح صالح")
                return True

            if chosen_index < 0 or chosen_index >= len(suggestions):
                telegram_bot.answer_callback_query(call.id, "خيار غير صالح")
                return True

            chosen = suggestions[chosen_index]
            start_iso = str(chosen.get("start_iso", "") or "").strip()
            end_iso = str(chosen.get("end_iso", "") or "").strip()
            chosen_label = f"{chosen_index + 1}"

            from app.features.google_calendar import update_calendar_event

            with active_user_profile_context(profile):
                update_result = update_calendar_event(
                    event_id=event_id, start_iso=start_iso, end_iso=end_iso
                )
            if update_result.get("success"):
                (getattr(agent, "session", None) or {}).pop(
                    "pending_conflict_resolution", None
                )
                if persist_agent_session_fn:
                    persist_agent_session_fn()

                if mongo_db is not None:
                    now = datetime.now(timezone.utc)
                    mongo_db["conflict_resolutions"].update_one(
                        {"conflict_id": conflict_id},
                        {
                            "$set": {
                                "status": "resolved",
                                "event_id": event_id,
                                "title": title,
                                "chosen_start_iso": start_iso,
                                "chosen_end_iso": end_iso,
                                "updated_at": now,
                            }
                        },
                        upsert=True,
                    )
                    mongo_db["calendar_events"].update_one(
                        {"event_id": event_id},
                        {
                            "$set": {
                                "title": title,
                                "start_iso": start_iso,
                                "end_iso": end_iso,
                                "updated_at": now,
                            }
                        },
                        upsert=True,
                    )

                telegram_bot.answer_callback_query(call.id, "تم التعديل")
                telegram_bot.send_message(
                    chat_id,
                    f"✅ عدّلت '{title}' على الاقتراح {chosen_label}: {start_iso}",
                    parse_mode=None,
                )
            else:
                telegram_bot.answer_callback_query(call.id, "ما قدرت أعدّل")
                telegram_bot.send_message(
                    chat_id,
                    "صار خلل أثناء تعديل الموعد. جرّب مرة ثانية.",
                    parse_mode=None,
                )
            return True

        if action == "no":
            (getattr(agent, "session", None) or {}).pop(
                "pending_conflict_resolution", None
            )
            if persist_agent_session_fn:
                persist_agent_session_fn()

            if mongo_db is not None:
                mongo_db["conflict_resolutions"].update_one(
                    {"conflict_id": conflict_id},
                    {
                        "$set": {
                            "status": "kept",
                            "event_id": event_id,
                            "title": title,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                    upsert=True,
                )

            telegram_bot.answer_callback_query(call.id, "تمام")
            telegram_bot.send_message(chat_id, "تمام، خلّيته كما هو", parse_mode=None)
            return True

        telegram_bot.answer_callback_query(call.id, "خيار غير معروف")
        return True

    except Exception as exc:
        _chat_id = None
        try:
            _chat_id = call.message.chat.id
        except Exception:
            _chat_id = None
        log_handler_exception_fn(
            exc,
            "telegram_handlers.process_conflict_resolution_callback",
            chat_id=_chat_id,
            extra={"callback_data": data},
        )
        telegram_bot.answer_callback_query(call.id, "حدث خطأ")
        return True


def register_basic_telegram_handlers(
    *,
    telegram_bot: Any,
    agent: Any,
    sandy_user_chat_id: str,
    is_image_generation_request_fn: Callable[[str], bool],
    extract_image_prompt_fn: Callable[[str], str],
    generate_image_with_azure_fn: Callable[..., Optional[bytes]],
    edit_image_with_azure_fn: Optional[Callable[..., Optional[bytes]]] = None,
    analyze_image_with_azure_fn: Callable[..., str],
    download_telegram_file_bytes_fn: Callable[..., Any],
    transcribe_audio_with_azure_fn: Callable[..., Optional[str]],
    create_chat_completion_fn: Callable[..., Any],
    send_text_and_voice_reply_fn: Callable[..., None],
    set_last_assistant_reaction_fn: Optional[Callable[[Optional[str]], None]] = None,
    handle_image_message_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    persist_agent_session_fn: Optional[Callable[[], None]] = None,
    google_tts_voice: str = "",
    google_tts_language_code: str = "ar-XA",
    mood_tts_voices: Optional[Dict[str, str]] = None,
    azure_speech_available: bool = False,
    azure_speech_key: str = "",
    azure_speech_region: str = "",
    azure_speech_voice: str = "",
    azure_openai_client: Any = None,
    azure_openai_image_deployment: Optional[str] = None,
    azure_openai_vision_deployment: Optional[str] = None,
    azure_openai_chat_deployment: Optional[str] = None,
    azure_openai_stt_deployment: Optional[str] = None,
    openai_model: Optional[str] = None,
) -> None:
    # Per-chat session store.
    # سابقاً: dict بدون حد → leak تدريجي (صور قديمة، pending قديم...)
    # الآن: TTL ٣٠ دقيقة + cap ١٠٠ chat — الـ owner مستثنى (مشترك مع agent.session).
    import time as _time
    _SESSION_TTL_SEC = 30 * 60
    _SESSION_MAX_ENTRIES = 100
    _session_store: Dict[str, Any] = {}
    _session_last_seen: Dict[str, float] = {}

    def _evict_stale_sessions() -> None:
        """يحذف الجلسات اللي ما تحرّكت منذ TTL، ويلتزم بالـ cap."""
        now = _time.time()
        owner_key = str(sandy_user_chat_id) if sandy_user_chat_id else None
        stale = [
            k for k, ts in _session_last_seen.items()
            if k != owner_key and now - ts > _SESSION_TTL_SEC
        ]
        for k in stale:
            _session_store.pop(k, None)
            _session_last_seen.pop(k, None)
        # cap — إذا لسّى كبير، احذف الأقدم (ما عدا الـ owner)
        if len(_session_store) > _SESSION_MAX_ENTRIES:
            candidates = sorted(
                ((k, ts) for k, ts in _session_last_seen.items() if k != owner_key),
                key=lambda x: x[1],
            )
            overflow = len(_session_store) - _SESSION_MAX_ENTRIES
            for k, _ in candidates[:overflow]:
                _session_store.pop(k, None)
                _session_last_seen.pop(k, None)

    def _get_chat_session(chat_id: Any) -> Dict[str, Any]:
        """يرجع session خاص بكل chat_id. الـ owner يشارك agent.session للـ persistence."""
        key = str(chat_id)
        _session_last_seen[key] = _time.time()
        if key not in _session_store:
            _evict_stale_sessions()
            if str(chat_id) == str(sandy_user_chat_id):
                _session_store[key] = getattr(agent, "session", None) or {}
            else:
                _session_store[key] = {}
        return _session_store[key]

    def _log_handler_exception(
        exc: BaseException, source: str, *, chat_id=None, extra=None
    ):
        log_unhandled_exception(
            getattr(agent, "mongo_db", None),
            exc,
            chat_id=chat_id,
            source=source,
            extra=extra,
        )

    def _current_profile(chat_id: Any):
        mongo_db = getattr(agent, "mongo_db", None)
        profile = find_user_profile(chat_id, mongo_db=mongo_db)
        if profile is None:
            return ensure_user_profile(chat_id, mongo_db=mongo_db)  # (profile, created)
        return profile, False  # (profile, created)

    def _graph_respond(text: str, chat_id, from_user_id) -> dict:
        """Run the LangGraph pipeline and return a response dict."""
        from app.agent.graph.graph import run_graph, get_final_reply

        # Owner-only intercept before the graph runs, for Self-Coding tasks
        # that are waiting on the user. The hook does approve/cancel/revision
        # routing so the message doesn't fall through to a fresh chat turn.
        try:
            from app.utils.user_profiles import is_owner_chat_id
            if is_owner_chat_id(chat_id):
                from app.agent.self_coding import resume_hook
                _resume_session = _get_chat_session(chat_id)
                intercepted = resume_hook.try_handle_resume(
                    str(text), chat_id, session=_resume_session
                )
                if intercepted is not None:
                    return intercepted
        except Exception as _e:
            # Never let the resume hook break the chat flow
            print(f"[resume_hook] suppressed error: {_e}")

        # Morning briefing intercept, keyword-based so it skips LLM routing.
        try:
            from app.utils.user_profiles import is_owner_chat_id, active_user_profile_context
            if is_owner_chat_id(chat_id):
                from app.agent.facade.briefing import should_send_briefing, build_morning_briefing
                from app.agent.memory import save_memory
                from app.utils.time import USER_TZ
                from datetime import datetime
                if should_send_briefing(agent.memory, str(text)):
                    _b_profile, _ = _current_profile(chat_id)
                    with active_user_profile_context(_b_profile):
                        briefing_text = build_morning_briefing(
                            memory=agent.memory,
                            mongo_db=getattr(agent, "mongo_db", None),
                            tasks_file=getattr(agent, "tasks_file", None),
                        )
                    agent.memory.setdefault("sandy_state", {})["last_briefing_date"] = (
                        datetime.now(USER_TZ).strftime("%Y-%m-%d")
                    )
                    save_memory(
                        agent.memory,
                        memory_file=agent.memory_file,
                        mongo_db=getattr(agent, "mongo_db", None),
                    )
                    return {"text": briefing_text, "reply_markup": None, "image_bytes": None, "caption": ""}
        except Exception as _be:
            print(f"[briefing_intercept] suppressed error: {_be}")

        sess = _get_chat_session(chat_id)
        pending_state = sess.get("pending_action")
        image_state = sess.get("image_state")
        gmail_list_state = sess.get("gmail_last_list")
        profile, _ = _current_profile(chat_id)
        with active_user_profile_context(profile):
            state = run_graph(
                str(text),
                str(from_user_id),
                str(chat_id),
                pending_state=pending_state,
                image_state=image_state,
                gmail_list_state=gmail_list_state,
            )
        sess["pending_action"] = state.get("pending_state")
        new_gmail = state.get("gmail_list_state")
        if new_gmail is not None:
            sess["gmail_last_list"] = new_gmail
        # Push maestro decisions into agent.memory["sandy_state"]; the legacy
        # facade reads it, and voice.py uses tool_name to decide on TTS summary.
        try:
            sandy_state = agent.memory.setdefault("sandy_state", {})
            sandy_state["mood"] = state.get("mood") or "neutral"
            sandy_state["message"] = str(text)
            sandy_state["function_call"] = state.get("function_call") or {}
        except Exception:
            pass
        if persist_agent_session_fn and str(chat_id) == str(sandy_user_chat_id):
            persist_agent_session_fn()
        reply = get_final_reply(state)
        chunks = reply.get("chunks", [reply["text"]])
        for chunk in chunks[1:]:
            telegram_bot.send_message(chat_id, chunk, parse_mode=None)
        return {
            "text": chunks[0],
            "reply_markup": reply["reply_markup"],
            "image_bytes": reply.get("image_bytes"),
            "caption": reply.get("caption", ""),
        }

    def _onboard_or_capture_name(message) -> Optional[str]:
        chat_id = message.chat.id
        profile, created = _current_profile(chat_id)

        if created and profile.get("relation") != "owner":
            return "مرحبا! أنا ساندي، شو اسمك؟"

        if (
            profile.get("relation") != "owner"
            and not str(profile.get("name", "") or "").strip()
        ):
            candidate_name = extract_profile_name(message.text or message.caption or "")
            if candidate_name:
                update_user_profile(
                    chat_id,
                    {"name": candidate_name},
                    mongo_db=getattr(agent, "mongo_db", None),
                )
                return f"تشرفت يا {candidate_name} 😊"

        return None

    def _profile_allows_full_access(chat_id: Any) -> bool:
        profile = find_user_profile(chat_id, mongo_db=getattr(agent, "mongo_db", None))
        return (
            bool(profile)
            and str(profile.get("permissions", "chat-only") or "chat-only")
            .strip()
            .lower()
            == "all"
        )

    def _send_voice_reply(
        chat_id, text, reply_to_message_id=None, reply_markup=None, skip_text=False
    ):
        """Send text plus voice, with mood tags stripped out."""
        send_text_and_voice_reply_fn(
            chat_id,
            text,
            telegram_bot=telegram_bot,
            agent_memory=agent.memory,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
            set_last_assistant_reaction_fn=set_last_assistant_reaction_fn,
            google_tts_voice=google_tts_voice,
            google_tts_language_code=google_tts_language_code,
            mood_tts_voices=mood_tts_voices,
            azure_speech_available=azure_speech_available,
            azure_speech_key=azure_speech_key,
            azure_speech_region=azure_speech_region,
            azure_speech_voice=azure_speech_voice,
            skip_text_message=skip_text,
        )
        # Queue the reply for sandy_local.py (Mac TTS listener). Fire and forget
        # so the Mongo write (up to wtimeout=2s) never blocks the reply (B12).
        if getattr(agent, "mongo_db", None) is not None:
            import re

            _clean = re.sub(r"\[.*?\]", "", str(text or "")).strip()
            if _clean:
                sandy_executor.submit(_enqueue_local_tts, agent.mongo_db, _clean)

    def _send_photo_async(chat_id, image_bytes, caption="", *, source="generated"):
        """Send a photo with inline buttons from a background thread.

        source: "generated" (Flux), "camera_snapshot", "camera_room_scan".
                يحدد سلوك زر "نسخة ثانية".
        """
        import telebot.types as tgtypes

        # خزّن الصورة + مصدرها في الجلسة عشان أزرار الوصف/التعديل/النسخة الثانية يشتغلوا
        try:
            sess = _get_chat_session(chat_id)
            sess["last_image_bytes"] = image_bytes
            sess["last_image_source"] = source
            # image_state["active_image_bytes"] — يستخدمها مسار التعديل
            from app.features.image_agent import ensure_image_state
            img_state = ensure_image_state(sess)
            img_state["active_image_bytes"] = image_bytes
        except Exception:
            pass

        def _worker():
            try:
                _chat_action(telegram_bot, chat_id, "upload_photo")
                photo_file = io.BytesIO(image_bytes)
                photo_file.name = "sandy_generated.png"

                # نحفظ المصدر في الـ callback_data نفسه عشان ما يضيع بين الجلسات
                src_tag = {
                    "camera_snapshot": "s",
                    "camera_room_scan": "r",
                }.get(source, "g")
                markup = tgtypes.InlineKeyboardMarkup()
                markup.row(
                    tgtypes.InlineKeyboardButton("✏️ تعديل", callback_data=f"img_edit:{src_tag}"),
                    tgtypes.InlineKeyboardButton(
                        "🔄 نسخة ثانية", callback_data=f"img_variation:{src_tag}"
                    ),
                    tgtypes.InlineKeyboardButton(
                        "🔍 وصف", callback_data=f"img_describe:{src_tag}"
                    ),
                )
                telegram_bot.send_photo(
                    chat_id,
                    photo_file,
                    caption=caption or None,
                    reply_markup=markup,
                    timeout=120,
                )
            except Exception as e:
                print(f"[Telegram] send_photo failed: {e}")
                _log_handler_exception(
                    e,
                    "telegram_handlers._send_photo_async",
                    chat_id=chat_id,
                    extra={"caption": caption},
                )

        sandy_executor.submit(_worker)

    def _task_callback_to_message(callback_data: str, chat_id: Any = None) -> str:
        try:
            from app.agent.executor.task_handlers import (
                _task_callback_to_message as _resolve_task_callback,
            )
        except Exception as exc:
            _log_handler_exception(
                exc,
                "telegram_handlers._task_callback_to_message",
                extra={"callback_data": callback_data},
            )
            return ""

        sess = _get_chat_session(chat_id) if chat_id is not None else {}
        task_aliases = sess.get("task_aliases", {})
        completed_aliases = sess.get("completed_task_aliases", {})
        message = _resolve_task_callback(callback_data, task_aliases)
        if message:
            return message
        return _resolve_task_callback(callback_data, completed_aliases)

    @telegram_bot.message_handler(commands=["start", "help"])
    def handle_start(message):
        onboarding_text = _onboard_or_capture_name(message)
        if onboarding_text:
            telegram_bot.reply_to(message, onboarding_text)
            return

        response = _graph_respond(
            "مستخدم جديد فتح البوت، رحبي فيه بأسلوبك الطبيعي",
            message.chat.id,
            message.from_user.id,
        )
        text = response.get("text", "") if isinstance(response, dict) else str(response)
        _send_voice_reply(message.chat.id, text, reply_to_message_id=message.message_id)

    # ── Phase 4: تسجيل/إدارة بصمة الصوت (المالك فقط) ──────────────────────────
    @telegram_bot.message_handler(commands=["enroll_voice"])
    def handle_enroll_voice(message):
        from app.utils.user_profiles import is_owner_chat_id
        from app.features import speaker_id

        chat_id = message.chat.id
        if not is_owner_chat_id(chat_id):
            telegram_bot.reply_to(message, "تسجيل بصمة الصوت للمالك فقط.")
            return
        if not speaker_id.is_available():
            telegram_bot.reply_to(message, "تمييز الصوت مش مفعّل حالياً على السيرفر.")
            return
        _voice_enroll_sessions[chat_id] = []
        telegram_bot.reply_to(
            message,
            "تمام، خلّينا نسجّل صوتك 🎙️\n"
            f"ابعتلي {_ENROLL_MIN_SAMPLES}-5 تسجيلات صوتية، كل وحدة ١٠-١٥ ثانية، "
            "احكي طبيعي بمكان هادي.\n"
            "لما تخلّص اكتب /enroll_done — أو /enroll_cancel للإلغاء.",
        )

    @telegram_bot.message_handler(commands=["enroll_done"])
    def handle_enroll_done(message):
        from app.features import speaker_id

        chat_id = message.chat.id
        samples = _voice_enroll_sessions.get(chat_id)
        if samples is None:
            telegram_bot.reply_to(message, "ما في جلسة تسجيل شغّالة. ابدأ بـ /enroll_voice.")
            return
        if len(samples) < _ENROLL_MIN_SAMPLES:
            telegram_bot.reply_to(
                message,
                f"لسا بدّي تسجيلات أكثر — وصلني {len(samples)} وبدّي على الأقل {_ENROLL_MIN_SAMPLES}.",
            )
            return
        _chat_action(telegram_bot, chat_id, "typing")
        ok, n, msg = speaker_id.enroll_speaker(chat_id, samples)
        _voice_enroll_sessions.pop(chat_id, None)
        telegram_bot.reply_to(message, msg)

    @telegram_bot.message_handler(commands=["enroll_cancel"])
    def handle_enroll_cancel(message):
        existed = _voice_enroll_sessions.pop(message.chat.id, None) is not None
        telegram_bot.reply_to(
            message, "ألغيت جلسة التسجيل." if existed else "ما في جلسة تسجيل شغّالة."
        )

    @telegram_bot.message_handler(commands=["forget_voice"])
    def handle_forget_voice(message):
        from app.utils.user_profiles import is_owner_chat_id
        from app.features import speaker_id

        chat_id = message.chat.id
        if not is_owner_chat_id(chat_id):
            telegram_bot.reply_to(message, "هاد الأمر للمالك فقط.")
            return
        deleted = speaker_id.delete_profile(chat_id)
        telegram_bot.reply_to(
            message, "مسحت بصمة صوتك." if deleted else "ما في بصمة صوت محفوظة أصلاً."
        )

    @telegram_bot.message_handler(commands=["image", "img"])
    def handle_image_command(message):
        try:
            if _is_duplicate_telegram_message(message):
                return
            onboarding_text = _onboard_or_capture_name(message)
            if onboarding_text:
                telegram_bot.reply_to(message, onboarding_text)
                return
            if is_rate_limited(message.chat.id):
                telegram_bot.reply_to(
                    message, "كثير رسائل، خلّيني أتنفس ي حبيبي مالك عليا😅"
                )
                return

            chat_id = message.chat.id
            prompt = extract_image_prompt_fn(message.text or "")
            if not prompt:
                telegram_bot.reply_to(
                    message,
                    "اكتب وصف الصورة بعد الأمر. مثال: /image قطة كرتونية تلبس نظارات",
                )
                return

            _chat_action(telegram_bot, chat_id, "upload_photo")
            image_bytes = generate_image_with_azure_fn(
                prompt,
                azure_openai_client=azure_openai_client,
                azure_openai_image_deployment=azure_openai_image_deployment,
            )
            if not image_bytes:
                telegram_bot.reply_to(
                    message,
                    "ما قدرت أولد الصورة. تأكد من إعداد AZURE_OPENAI_API_KEY و AZURE_FLUX_ENDPOINT.",
                )
                return

            photo_file = io.BytesIO(image_bytes)
            photo_file.name = "sandy_generated.png"
            telegram_bot.send_photo(chat_id, photo_file, caption=prompt)

            if persist_agent_session_fn is not None:
                persist_agent_session_fn()

        except Exception as e:
            print(f"[Error] Image command handler: {e}")
            _log_handler_exception(
                e, "telegram_handlers.handle_image_command", chat_id=message.chat.id
            )
            telegram_bot.reply_to(message, "صار خلل أثناء توليد الصورة.")

    # buffer لتجميع ألبوم الصور قبل تحويلها لـ PDF
    _pdf_group_buffer: Dict[str, Dict] = {}  # media_group_id → {images, chat_id, timer}
    _pdf_group_lock = threading.Lock()

    def _flush_pdf_group(group_id: str):
        with _pdf_group_lock:
            entry = _pdf_group_buffer.pop(group_id, None)
        if not entry:
            return
        chat_id = entry["chat_id"]
        images_bytes = entry["images"]
        try:
            import io as _io
            from PIL import Image as _PILImage
            pages = []
            for img_bytes in images_bytes:
                img = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
                pages.append(img)
            if not pages:
                return
            pdf_buf = _io.BytesIO()
            pages[0].save(pdf_buf, format="PDF", save_all=True, append_images=pages[1:])
            pdf_buf.seek(0)
            _chat_action(telegram_bot, chat_id, "upload_document")
            telegram_bot.send_document(
                chat_id,
                document=pdf_buf,
                visible_file_name=f"images_{len(pages)}.pdf",
                caption=f"تفضّل {len(pages)} صور في PDF واحد ✅",
            )
        except Exception as exc:
            print(f"[Album->PDF] {exc}")
            telegram_bot.send_message(chat_id, "ما قدرت أجمع الصور في PDF.", parse_mode=None)

    @telegram_bot.message_handler(content_types=["photo"])
    def handle_photo(message):
        try:
            if _is_duplicate_telegram_message(message):
                return
            onboarding_text = _onboard_or_capture_name(message)
            if onboarding_text:
                telegram_bot.reply_to(message, onboarding_text)
                return
            if is_rate_limited(message.chat.id):
                telegram_bot.reply_to(
                    message, "كثير رسائل، خلّيني أتنفس ي حبيبي مالك عليا😅"
                )
                return

            chat_id = message.chat.id
            _chat_action(telegram_bot, chat_id, "typing")

            photo = message.photo[-1] if message.photo else None
            if not photo:
                telegram_bot.reply_to(message, "ما وصلتني الصورة بشكل صحيح.")
                return

            downloaded = download_telegram_file_bytes_fn(telegram_bot, photo.file_id)
            if not downloaded:
                telegram_bot.reply_to(message, "ما قدرت أحمّل الصورة من تيليجرام.")
                return

            image_bytes, _ = downloaded
            caption = (message.caption or "").strip()

            # خزّن الصورة في session دائماً
            from app.features.image_agent import (
                ensure_image_state,
                is_photo_edit_caption,
            )

            image_state = ensure_image_state(_get_chat_session(chat_id))
            image_state["active_image_bytes"] = image_bytes
            image_state["active_image_uid"] = getattr(photo, "file_unique_id", None)
            image_state["active_image"] = {
                "user_request": caption or "صورة مرسلة",
                "short_caption_ar": caption or "صورة مرسلة",
                "action": "uploaded",
            }

            # ألبوم صور + PDF (media_group)
            _pdf_keywords = ("pdf", "بي دي اف", "بيدياف", "بي دي", "حول لـ pdf", "حولها pdf", "حوليها pdf")
            group_id = getattr(message, "media_group_id", None)
            is_pdf_request = caption and any(kw in caption.lower() for kw in _pdf_keywords)
            if group_id and is_pdf_request:
                with _pdf_group_lock:
                    if group_id not in _pdf_group_buffer:
                        _pdf_group_buffer[group_id] = {"images": [], "chat_id": chat_id, "timer": None}
                    _pdf_group_buffer[group_id]["images"].append(image_bytes)
                    old_timer = _pdf_group_buffer[group_id]["timer"]
                    if old_timer:
                        old_timer.cancel()
                    t = threading.Timer(2.5, _flush_pdf_group, args=[group_id])
                    _pdf_group_buffer[group_id]["timer"] = t
                    t.start()
                return

            # صورة واحدة → PDF
            if is_pdf_request:
                try:
                    import io as _io
                    from PIL import Image as _PILImage
                    img = _PILImage.open(_io.BytesIO(image_bytes)).convert("RGB")
                    pdf_buf = _io.BytesIO()
                    img.save(pdf_buf, format="PDF")
                    pdf_buf.seek(0)
                    _chat_action(telegram_bot, chat_id, "upload_document")
                    telegram_bot.send_document(
                        chat_id,
                        document=pdf_buf,
                        visible_file_name="image.pdf",
                        caption="تفضّل الصورة كملف PDF ✅",
                        reply_to_message_id=message.message_id,
                    )
                except Exception as _exc:
                    print(f"[Photo->PDF] {_exc}")
                    telegram_bot.send_message(chat_id, "ما قدرت أحوّل الصورة لـ PDF.", reply_to_message_id=message.message_id, parse_mode=None)
                return

            # إذا الكابشن يطلب تعديل → عدّل الصورة مباشرة
            if caption and is_photo_edit_caption(caption) and edit_image_with_azure_fn:
                _chat_action(telegram_bot, chat_id, "upload_photo")
                edit_prompt = (
                    f"Edit this image as requested: {caption}. "
                    f"Preserve all people, faces, expressions, clothing, and background details exactly. "
                    f"Only apply the specific change the user asked for."
                )
                edited_bytes = edit_image_with_azure_fn(
                    image_bytes,
                    edit_prompt,
                    azure_openai_image_deployment=azure_openai_image_deployment,
                )
                if edited_bytes:
                    image_state["active_image_bytes"] = edited_bytes
                    image_state["active_image"]["action"] = "edited"
                    _send_photo_async(chat_id, edited_bytes, caption)
                    if persist_agent_session_fn:
                        persist_agent_session_fn()
                    return
                # لو التعديل فشل، حلّل الصورة وأخبر المستخدم
                telegram_bot.send_message(
                    chat_id,
                    "ما قدرت أعدّل الصورة حالياً — بحللها بدل هيك.",
                    reply_to_message_id=message.message_id,
                    parse_mode=None,
                )

            # تحليل عادي
            analysis_prompt = caption or "حللي الصورة باختصار وقدمي أهم الملاحظات."
            analysis = analyze_image_with_azure_fn(
                image_bytes,
                analysis_prompt,
                create_chat_completion_fn=create_chat_completion_fn,
                azure_openai_vision_deployment=azure_openai_vision_deployment,
                azure_openai_chat_deployment=azure_openai_chat_deployment,
                openai_model=openai_model,
            )
            telegram_bot.send_message(
                chat_id,
                analysis,
                reply_to_message_id=message.message_id,
                parse_mode=None,
            )
            # Phase 8: خلّيها "فاكرة" إنه المستخدم شارك صورة (بدون حفظها بالألبوم).
            # نستعمل الوصف المُولّد أصلاً للتحليل → صفر كلفة إضافية، بدون بايتات.
            try:
                from app.agent.graph.graph import _stm_save

                shared_note = "[شاركتك صورة]" + (f": {caption}" if caption else "")
                _stm_save(str(chat_id), str(chat_id), shared_note, analysis)
            except Exception as _stm_exc:  # noqa: BLE001
                print(f"[photo_album] mention STM skipped: {_stm_exc}")
            if persist_agent_session_fn:
                persist_agent_session_fn()
        except Exception as e:
            print(f"[Error] Photo handler: {e}")
            _log_handler_exception(
                e, "telegram_handlers.handle_photo", chat_id=message.chat.id
            )
            telegram_bot.reply_to(message, "صار خلل أثناء تحليل الصورة.")

    @telegram_bot.message_handler(content_types=["video"])
    def handle_video(message):
        try:
            if _is_duplicate_telegram_message(message):
                return
            onboarding_text = _onboard_or_capture_name(message)
            if onboarding_text:
                telegram_bot.reply_to(message, onboarding_text)
                return
            if is_rate_limited(message.chat.id):
                telegram_bot.reply_to(
                    message, "كثير رسائل، خلّيني أتنفس ي حبيبي مالك عليا😅"
                )
                return

            chat_id = message.chat.id
            _chat_action(telegram_bot, chat_id, "typing")

            thumb = getattr(message.video, "thumbnail", None) or getattr(
                message.video, "thumb", None
            )
            if not thumb:
                telegram_bot.send_message(
                    chat_id,
                    "وصل الفيديو، لكن بدون Thumbnail للتحليل البصري. ابعته كصورة أو فيديو فيه معاينة.",
                    reply_to_message_id=message.message_id,
                    parse_mode=None,
                )
                return

            downloaded = download_telegram_file_bytes_fn(telegram_bot, thumb.file_id)
            if not downloaded:
                telegram_bot.reply_to(message, "ما قدرت أحمّل معاينة الفيديو.")
                return

            image_bytes, _ = downloaded
            prompt = (
                message.caption
                or "حللي محتوى الفيديو اعتماداً على لقطة المعاينة وقدمي وصف مختصر."
            )
            analysis = analyze_image_with_azure_fn(
                image_bytes,
                prompt,
                create_chat_completion_fn=create_chat_completion_fn,
                azure_openai_vision_deployment=azure_openai_vision_deployment,
                azure_openai_chat_deployment=azure_openai_chat_deployment,
                openai_model=openai_model,
            )
            telegram_bot.send_message(
                chat_id,
                analysis,
                reply_to_message_id=message.message_id,
                parse_mode=None,
            )
        except Exception as e:
            print(f"[Error] Video handler: {e}")
            _log_handler_exception(
                e, "telegram_handlers.handle_video", chat_id=message.chat.id
            )
            telegram_bot.reply_to(message, "صار خلل أثناء تحليل الفيديو.")

    @telegram_bot.message_handler(content_types=["document"])
    def handle_document(message):
        try:
            if _is_duplicate_telegram_message(message):
                return
            onboarding_text = _onboard_or_capture_name(message)
            if onboarding_text:
                telegram_bot.reply_to(message, onboarding_text)
                return
            if is_rate_limited(message.chat.id):
                telegram_bot.reply_to(
                    message, "كثير رسائل، خلّيني أتنفس ي حبيبي مالك عليا😅"
                )
                return

            chat_id = message.chat.id
            _chat_action(telegram_bot, chat_id, "typing")

            doc = message.document
            filename = doc.file_name or "ملف"
            caption = (message.caption or "").strip()

            # تحميل الملف
            downloaded = download_telegram_file_bytes_fn(telegram_bot, doc.file_id)
            if not downloaded:
                telegram_bot.reply_to(message, "ما قدرت أحمّل الملف.")
                return
            file_bytes, _ = downloaded

            # استخراج النص
            from app.utils.document_reader import extract_text
            text, err = extract_text(file_bytes, filename)
            if err:
                telegram_bot.send_message(
                    chat_id, f"⚠️ {err}",
                    reply_to_message_id=message.message_id,
                    parse_mode=None,
                )
                return

            # حفظ في session
            session = _get_chat_session(chat_id)
            session["active_document"] = {"filename": filename, "content": text}

            # بناء الـ prompt
            if caption:
                user_prompt = f"[ملف: {filename}]\n\n{text}\n\n---\nالطلب: {caption}"
            else:
                user_prompt = f"[ملف: {filename}]\n\n{text}\n\n---\nلخّصي هذا الملف باختصار."

            # معالجة عبر LLM مباشرة
            _chat_action(telegram_bot, chat_id, "typing")
            try:
                response = create_chat_completion_fn(
                    temperature=0.4,
                    max_tokens=1000,
                    prefer_azure=True,
                    messages=[
                        {"role": "system", "content": "أنتِ ساندي. حلّلي أو عدّلي أو لخّصي الملف حسب طلب المستخدم. ردي بالعربية بشكل واضح ومنظّم."},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                reply_text = (response.choices[0].message.content or "").strip()
            except Exception as exc:
                print(f"[Document] LLM error: {exc}")
                reply_text = "ما قدرت أعالج الملف حالياً."

            if not reply_text:
                reply_text = "ما قدرت أستخرج رد."

            # إذا الرد طويل → ابعته كملف TXT
            if len(reply_text) > 3000:
                import io as _io
                out_buf = _io.BytesIO(reply_text.encode("utf-8"))
                out_name = f"sandy_reply_{filename}.txt"
                telegram_bot.send_document(
                    chat_id,
                    document=out_buf,
                    visible_file_name=out_name,
                    caption="النتيجة طويلة، بعثتها كملف.",
                )
            else:
                telegram_bot.send_message(
                    chat_id,
                    reply_text,
                    reply_to_message_id=message.message_id,
                    parse_mode="Markdown",
                )

        except Exception as e:
            print(f"[Error] Document handler: {e}")
            telegram_bot.reply_to(message, "صار خلل أثناء معالجة الملف.")

    @telegram_bot.message_handler(content_types=["voice", "audio"])
    def handle_voice_or_audio(message):
        try:
            if _is_duplicate_telegram_message(message):
                return
            onboarding_text = _onboard_or_capture_name(message)
            if onboarding_text:
                telegram_bot.reply_to(message, onboarding_text)
                return
            if is_rate_limited(message.chat.id):
                telegram_bot.reply_to(
                    message, "كثير رسائل، خلّيني أتنفس ي حبيبي مالك عليا😅"
                )
                return

            chat_id = message.chat.id
            _chat_action(telegram_bot, chat_id, "typing")

            media_obj = (
                message.voice if message.content_type == "voice" else message.audio
            )
            if not media_obj:
                telegram_bot.reply_to(message, "ما قدرت أقرأ الملف الصوتي.")
                return

            downloaded = download_telegram_file_bytes_fn(
                telegram_bot, media_obj.file_id
            )
            if not downloaded:
                telegram_bot.reply_to(message, "ما قدرت أحمّل الصوت من تيليجرام.")
                return

            audio_bytes, file_path = downloaded

            # Phase 4: لو في جلسة تسجيل بصمة شغّالة، هاد التسجيل للبصمة مش للمحادثة.
            if chat_id in _voice_enroll_sessions:
                from app.integrations.azure_speech import audio_to_pcm16

                pcm = audio_to_pcm16(audio_bytes)
                if not pcm:
                    telegram_bot.reply_to(message, "ما قدرت أعالج هالتسجيل، جرّب كمان مرة.")
                    return
                session = _voice_enroll_sessions[chat_id]
                session.append(pcm)
                hint = (
                    "بعتلي كمان أو اكتب /enroll_done"
                    if len(session) >= _ENROLL_MIN_SAMPLES
                    else f"بعتلي {_ENROLL_MIN_SAMPLES - len(session)} تسجيلات كمان"
                )
                telegram_bot.reply_to(message, f"أخذت تسجيل {len(session)} ✅ — {hint}")
                return

            transcript = transcribe_audio_with_azure_fn(
                audio_bytes,
                azure_speech_available=azure_speech_available,
                azure_speech_key=azure_speech_key,
                azure_speech_region=azure_speech_region,
                file_name=file_path,
            )
            if not transcript:
                telegram_bot.reply_to(
                    message, "ما قدرت أحول الصوت لنص. تأكد من إعداد Azure STT."
                )
                return

            print(f"[Telegram] Voice transcript: {transcript}")
            response = _graph_respond(transcript, chat_id, message.from_user.id)

            text = (
                response.get("text", "")
                if isinstance(response, dict)
                else str(response)
            )
            image_bytes = (
                response.get("image_bytes") if isinstance(response, dict) else None
            )
            image_source = (
                response.get("image_source") if isinstance(response, dict) else None
            )
            caption = response.get("caption", "") if isinstance(response, dict) else ""

            if image_bytes:
                # بعت الصورة أولاً، بعدين الرد النصي/الصوتي إذا في شي يقال
                _send_photo_async(chat_id, image_bytes, caption, source=image_source or "generated")
                if text:
                    _send_voice_reply(
                        chat_id, text, reply_to_message_id=message.message_id
                    )
            else:
                _send_voice_reply(chat_id, text, reply_to_message_id=message.message_id)

        except Exception as e:
            print(f"[Error] Voice handler: {e}")
            _log_handler_exception(
                e, "telegram_handlers.handle_voice_or_audio", chat_id=message.chat.id
            )
            telegram_bot.reply_to(message, "صار خلل أثناء تحليل الصوت.")

    @telegram_bot.message_handler(content_types=["text"])
    def handle_message(message):
        try:
            if _is_duplicate_telegram_message(message):
                print(
                    f"[Telegram] Duplicate ignored: chat={message.chat.id}, msg={message.message_id}"
                )
                return

            user_message = (message.text or "").strip()
            chat_id = message.chat.id

            # إذا المستخدم عامل reply على رسالة، أضف محتواها للسياق
            replied_text = ""
            if message.reply_to_message:
                if message.reply_to_message.text:
                    replied_text = message.reply_to_message.text.strip()
                elif message.reply_to_message.caption:
                    replied_text = message.reply_to_message.caption.strip()

            if replied_text:
                user_message = f'[سياق مهم - المستخدم يرد على رسالتك التالية تحديداً: "{replied_text}"]\n[رد المستخدم]: {user_message}'

            onboarding_text = _onboard_or_capture_name(message)
            if onboarding_text:
                telegram_bot.reply_to(message, onboarding_text)
                return

            if is_sensitive_domain_request(user_message):
                profile = find_user_profile(
                    chat_id, mongo_db=getattr(agent, "mongo_db", None)
                )
                permissions = (
                    str((profile or {}).get("permissions", "chat-only") or "chat-only")
                    .strip()
                    .lower()
                )
                if permissions != "all":
                    telegram_bot.reply_to(message, "هذا خاص بنبيل 😊")
                    return
            if is_rate_limited(chat_id):
                telegram_bot.reply_to(message, "كثير رسائل، خلّيني أتنفس 😅")
                return

            if not user_message:
                telegram_bot.reply_to(message, "ما وصلني نص الرسالة.")
                return

            _chat_action(telegram_bot, chat_id, "typing")
            print(
                f"[Telegram] Message from {message.from_user.first_name}: {user_message}"
            )

            # Progress indicator مع حذف الرسالة بعد الرد
            _msg_lower = user_message.lower()
            _is_img = is_image_generation_request_fn(user_message)
            _is_research = any(
                k in _msg_lower
                for k in [
                    "ابحث",
                    "ابحثي",
                    "ابحثو",
                    "دوري",
                    "دورلي",
                    "دور لي",
                    "search for",
                    "find me",
                    "أخبار",
                    "اخبار",
                    "آخر أخبار",
                    "اخر اخبار",
                    "لخصي",
                    "لخصيها",
                    "لخصها",
                    "جامعات",
                    "ماجستير",
                ]
            )

            # تعديل صورة محفوظة عبر نص
            from app.features.image_agent import (
                ensure_image_state,
                is_photo_edit_caption,
            )

            _image_state = ensure_image_state(_get_chat_session(chat_id))
            _stored_bytes = _image_state.get("active_image_bytes")
            # لو في pending edit action من زر "تعديل"، أي رسالة تالية تعتبر تعليمات للتعديل
            _pending_edit = (
                isinstance(_image_state.get("pending_image_action"), dict)
                and (_image_state["pending_image_action"].get("action") == "edit_last")
            )
            _is_edit_request = (
                _stored_bytes is not None
                and edit_image_with_azure_fn is not None
                and not _is_img
                and (_pending_edit or is_photo_edit_caption(user_message))
            )
            if _is_edit_request:
                # امسح الـ pending عشان ما يضل عالق
                _image_state["pending_image_action"] = None
                _chat_action(telegram_bot, chat_id, "upload_photo")
                _progress_msg = telegram_bot.send_message(
                    chat_id, "⏳ عم أعدّل الصورة..."
                )
                edit_prompt = (
                    f"Edit this image as requested: {user_message}. "
                    f"Preserve all people, faces, expressions, clothing, and background details exactly. "
                    f"Only apply the specific change the user asked for."
                )
                edited_bytes = edit_image_with_azure_fn(
                    _stored_bytes,
                    edit_prompt,
                    azure_openai_image_deployment=azure_openai_image_deployment,
                )
                try:
                    telegram_bot.delete_message(chat_id, _progress_msg.message_id)
                except Exception:
                    pass
                if edited_bytes:
                    _image_state["active_image_bytes"] = edited_bytes
                    _image_state["active_image"] = {
                        **(_image_state.get("active_image") or {}),
                        "user_request": user_message,
                        "action": "edited",
                    }
                    _send_photo_async(chat_id, edited_bytes, user_message)
                    if persist_agent_session_fn:
                        persist_agent_session_fn()
                    return
                # Edit API not available, so fall through to _graph_respond which handles it via prompt

            _progress_msg = None
            if _is_img:
                _progress_msg = telegram_bot.send_message(
                    chat_id, "⏳ عم أولّد الصورة..."
                )
                _chat_action(telegram_bot, chat_id, "upload_photo")
            elif _is_research:
                _progress_msg = telegram_bot.send_message(chat_id, "⏳ عم أبحث...")
                _chat_action(telegram_bot, chat_id, "typing")
            else:
                _chat_action(telegram_bot, chat_id, "typing")

            if _is_img and handle_image_message_fn is not None:
                image_result = handle_image_message_fn(
                    user_message,
                    session=_get_chat_session(chat_id),
                    create_chat_completion_fn=create_chat_completion_fn,
                    generate_image_with_azure_fn=generate_image_with_azure_fn,
                    azure_openai_client=azure_openai_client,
                    azure_openai_image_deployment=azure_openai_image_deployment,
                )
                if image_result and image_result.get("handled"):
                    if _progress_msg:
                        try:
                            telegram_bot.delete_message(
                                chat_id, _progress_msg.message_id
                            )
                        except Exception:
                            pass

                    image_bytes = image_result.get("image_bytes")
                    reply_text = (
                        image_result.get("reply_text", "")
                        if isinstance(image_result, dict)
                        else ""
                    )
                    caption = (
                        image_result.get("caption", "")
                        if isinstance(image_result, dict)
                        else ""
                    )

                    if image_bytes:
                        _send_photo_async(
                            chat_id, image_bytes, caption or reply_text or ""
                        )
                    elif reply_text:
                        _send_voice_reply(
                            chat_id, reply_text, reply_to_message_id=message.message_id
                        )
                    return

            # Streaming setup, chat messages only.
            from app.agent.nodes.execute import set_stream_hooks, clear_stream_hooks
            _stream_msg_id = [None]
            _last_edit_at = [0.0]

            if not _is_img and not _is_research:
                def _on_stream_start():
                    try:
                        _m = telegram_bot.send_message(chat_id, "▌", parse_mode=None)
                        _stream_msg_id[0] = _m.message_id
                    except Exception:
                        pass

                def _on_stream_chunk(partial):
                    if not _stream_msg_id[0]:
                        return
                    now = _time.time()
                    if _last_edit_at[0] > 0 and now - _last_edit_at[0] < 0.5:
                        return
                    try:
                        telegram_bot.edit_message_text(
                            partial + "▌", chat_id, _stream_msg_id[0], parse_mode=None
                        )
                        _last_edit_at[0] = now
                    except Exception:
                        pass

                set_stream_hooks(_on_stream_start, _on_stream_chunk)

            try:
                response = _graph_respond(user_message, chat_id, message.from_user.id)
            finally:
                clear_stream_hooks()

            if _progress_msg:
                try:
                    telegram_bot.delete_message(chat_id, _progress_msg.message_id)
                except Exception:
                    pass

            text = (
                response.get("text", "")
                if isinstance(response, dict)
                else str(response)
            )
            image_bytes = (
                response.get("image_bytes") if isinstance(response, dict) else None
            )
            image_source = (
                response.get("image_source") if isinstance(response, dict) else None
            )
            caption = response.get("caption", "") if isinstance(response, dict) else ""
            reply_markup = (
                response.get("reply_markup") if isinstance(response, dict) else None
            )

            if image_bytes:
                # صورة + صوت Sandy بيقول "جهزت الصورة" (تجربة روبوت كاملة)
                _send_photo_async(chat_id, image_bytes, caption or "", source=image_source or "generated")
                if text and text.strip():
                    _send_voice_reply(chat_id, text, skip_text=True)
            elif _stream_msg_id[0]:
                # Streaming was used, so do a final edit to drop the cursor, then TTS only
                try:
                    telegram_bot.edit_message_text(
                        text, chat_id, _stream_msg_id[0], parse_mode=None
                    )
                except Exception:
                    pass
                _send_voice_reply(
                    chat_id, text, skip_text=True, reply_markup=reply_markup
                )
            else:
                _send_voice_reply(
                    chat_id,
                    text,
                    reply_to_message_id=message.message_id,
                    reply_markup=reply_markup,
                )

        except Exception as e:
            import traceback

            print(f"[Error] Telegram handler: {e}")
            traceback.print_exc()
            _log_handler_exception(
                e,
                "telegram_handlers.handle_message",
                chat_id=chat_id,
                extra={"message_id": getattr(message, "message_id", None)},
            )
            telegram_bot.reply_to(message, f"حدث خطأ: {str(e)}")

    @telegram_bot.callback_query_handler(
        func=lambda call: call.data in {"confirm_yes", "confirm_no"}
    )
    def handle_confirm_callback(call):
        """Inline yes/no buttons for confirming a pending action."""
        try:
            chat_id = call.message.chat.id
            find_user_profile(chat_id, mongo_db=getattr(agent, "mongo_db", None))
            if not _profile_allows_full_access(chat_id):
                telegram_bot.answer_callback_query(call.id, "هذا خاص بنبيل 😊")
                return
            answer = "اه" if call.data == "confirm_yes" else "لا"

            # Remove the buttons from the original message
            try:
                telegram_bot.edit_message_reply_markup(
                    chat_id, call.message.message_id, reply_markup=None
                )
            except Exception:
                pass

            telegram_bot.answer_callback_query(call.id)
            _chat_action(telegram_bot, chat_id, "typing")

            response = _graph_respond(answer, chat_id, call.from_user.id)
            text = (
                response.get("text", "")
                if isinstance(response, dict)
                else str(response)
            )
            reply_markup = (
                response.get("reply_markup") if isinstance(response, dict) else None
            )
            _send_voice_reply(chat_id, text, reply_markup=reply_markup)
        except Exception as e:
            print(f"[Error] Confirm callback: {e}")
            _log_handler_exception(
                e,
                "telegram_handlers.handle_confirm_callback",
                chat_id=getattr(call.message.chat, "id", None),
                extra={"callback_data": call.data},
            )
            telegram_bot.answer_callback_query(call.id, "حدث خطأ")

    @telegram_bot.callback_query_handler(
        func=lambda call: str(call.data or "")
        in {"email_send", "email_draft", "email_edit", "email_cancel"}
    )
    def handle_email_callback(call):
        """أزرار ارسل / مسودة / تعديل / الغاء للإيميل."""
        try:
            chat_id = call.message.chat.id
            if not _profile_allows_full_access(chat_id):
                telegram_bot.answer_callback_query(call.id, "هذا خاص بنبيل 😊")
                return

            sess = _get_chat_session(chat_id)
            pending = sess.get("pending_action") or {}
            is_email_pending = (
                isinstance(pending, dict)
                and pending.get("type") == "email"
                and pending.get("action") in {"confirm_send", "await_body"}
            )

            try:
                telegram_bot.edit_message_reply_markup(
                    chat_id, call.message.message_id, reply_markup=None
                )
            except Exception:
                pass

            if call.data == "email_cancel":
                sess["pending_action"] = None
                if persist_agent_session_fn and str(chat_id) == str(sandy_user_chat_id):
                    persist_agent_session_fn()
                telegram_bot.answer_callback_query(call.id)
                _send_voice_reply(chat_id, "تمام، ألغيت الإيميل.")
                return

            if not is_email_pending:
                telegram_bot.answer_callback_query(call.id, "انتهت صلاحية هذا الإيميل")
                return

            if call.data == "email_edit":
                telegram_bot.answer_callback_query(call.id)
                _send_voice_reply(
                    chat_id,
                    "شو بدك تعدّل؟ (مثال: الموضوع: عنوان جديد، أو غيّر المحتوى إلى ...)",
                )
                return

            if call.data == "email_send":
                telegram_bot.answer_callback_query(call.id)
                _chat_action(telegram_bot, chat_id, "typing")
                response = _graph_respond("ارسل", chat_id, call.from_user.id)
                text = (
                    response.get("text", "")
                    if isinstance(response, dict)
                    else str(response)
                )
                _send_voice_reply(chat_id, text)
                return

            if call.data == "email_draft":
                # Convert pending confirm_send → draft action
                if is_email_pending:
                    pending_copy = dict(pending)
                    pending_copy["action"] = "draft"
                    sess["pending_action"] = pending_copy
                    if persist_agent_session_fn and str(chat_id) == str(
                        sandy_user_chat_id
                    ):
                        persist_agent_session_fn()
                telegram_bot.answer_callback_query(call.id)
                _chat_action(telegram_bot, chat_id, "typing")
                response = _graph_respond("احفظ مسودة", chat_id, call.from_user.id)
                text = (
                    response.get("text", "")
                    if isinstance(response, dict)
                    else str(response)
                )
                _send_voice_reply(chat_id, text)
                return

        except Exception as e:
            print(f"[Error] Email callback: {e}")
            _log_handler_exception(
                e,
                "telegram_handlers.handle_email_callback",
                chat_id=getattr(call.message.chat, "id", None),
                extra={"callback_data": call.data},
            )
            telegram_bot.answer_callback_query(call.id, "حدث خطأ")

    @telegram_bot.callback_query_handler(
        func=lambda call: str(call.data or "").startswith("conflict:")
    )
    def handle_conflict_callback(call):
        process_conflict_resolution_callback(
            telegram_bot=telegram_bot,
            agent=agent,
            call=call,
            profile_allows_full_access_fn=_profile_allows_full_access,
            log_handler_exception_fn=_log_handler_exception,
            persist_agent_session_fn=persist_agent_session_fn,
        )

    @telegram_bot.callback_query_handler(
        func=lambda call: call.data.startswith("task:")
    )
    def handle_task_callback(call):
        try:
            chat_id = call.message.chat.id
            find_user_profile(chat_id, mongo_db=getattr(agent, "mongo_db", None))

            if not _profile_allows_full_access(chat_id):
                telegram_bot.answer_callback_query(call.id, "هذا خاص بنبيل 😊")
                return

            # زر "أضف مهمة" → اسأل المستخدم مباشرة
            if call.data == "task:add":
                try:
                    telegram_bot.edit_message_reply_markup(
                        chat_id, call.message.message_id, reply_markup=None
                    )
                except Exception:
                    pass
                telegram_bot.answer_callback_query(call.id)
                from app.agent.pending import create_pending_action as _make_pending

                _await_pending = _make_pending(
                    {
                        "type": "task",
                        "action": "await_name",
                        "confirmation_status": "clarification",
                    }
                )
                _get_chat_session(chat_id)["pending_action"] = _await_pending
                if persist_agent_session_fn and str(chat_id) == str(sandy_user_chat_id):
                    persist_agent_session_fn()
                _send_voice_reply(chat_id, "شو اسم المهمة الجديدة؟")
                return

            # أزرار الإكمال والحذف بمعرف مباشر → bypass AI planner
            _parts = str(call.data).split(":")
            if len(_parts) == 4 and _parts[0] == "task" and _parts[2] == "id":
                _btn_action = _parts[1].strip().lower()  # complete | delete
                _task_id = _parts[3].strip()
                if _btn_action in {"complete", "delete"} and _task_id:
                    try:
                        import telebot.types as _tg
                        from app.features.google_tasks import load_tasks as _load_tasks
                        from app.agent.pending import (
                            create_pending_action as _make_pending,
                        )

                        _profile, _ = _current_profile(chat_id)
                        _mongo = getattr(agent, "mongo_db", None)
                        with active_user_profile_context(_profile):
                            _all_tasks = _load_tasks(mongo_db=_mongo, tasks_file=None)
                        _task = next(
                            (
                                t
                                for t in _all_tasks
                                if str(t.get("id", "")).strip() == _task_id
                            ),
                            None,
                        )
                        if not _task:
                            telegram_bot.answer_callback_query(
                                call.id, "المهمة غير موجودة أو تم حذفها"
                            )
                            return
                        _task_text = str(_task.get("text", "")).strip()
                        _paction = (
                            "complete" if _btn_action == "complete" else "delete_one"
                        )
                        _pending = _make_pending(
                            {
                                "type": "task",
                                "action": _paction,
                                "task_id": _task_id,
                                "text": _task_text,
                                "confirmation_status": "pending",
                            }
                        )
                        _get_chat_session(chat_id)["pending_action"] = _pending
                        if persist_agent_session_fn and str(chat_id) == str(
                            sandy_user_chat_id
                        ):
                            persist_agent_session_fn()
                        _confirm_markup = _tg.InlineKeyboardMarkup()
                        _confirm_markup.row(
                            _tg.InlineKeyboardButton(
                                "✅ نعم", callback_data="confirm_yes"
                            ),
                            _tg.InlineKeyboardButton(
                                "❌ لا", callback_data="confirm_no"
                            ),
                        )
                        if _btn_action == "complete":
                            _msg = f"متأكد بدك أعلّم المهمة كمكتملة؟\n- {_task_text}"
                        else:
                            _msg = f"متأكد بدك تحذف المهمة؟\n- {_task_text}"
                        try:
                            telegram_bot.edit_message_reply_markup(
                                chat_id, call.message.message_id, reply_markup=None
                            )
                        except Exception:
                            pass
                        telegram_bot.answer_callback_query(call.id)
                        _send_voice_reply(chat_id, _msg, reply_markup=_confirm_markup)
                    except Exception as _e:
                        print(f"[Error] Direct task button: {_e}")
                        _log_handler_exception(
                            _e,
                            "telegram_handlers.handle_task_callback.direct",
                            chat_id=chat_id,
                            extra={"callback_data": call.data},
                        )
                        telegram_bot.answer_callback_query(call.id, "حدث خطأ")
                    return

            synthetic_message = _task_callback_to_message(call.data, chat_id=chat_id)
            if not synthetic_message:
                telegram_bot.answer_callback_query(call.id, "ما قدرت أفهم الزر")
                return

            try:
                telegram_bot.edit_message_reply_markup(
                    chat_id, call.message.message_id, reply_markup=None
                )
            except Exception:
                pass

            telegram_bot.answer_callback_query(call.id)
            _chat_action(telegram_bot, chat_id, "typing")

            response = _graph_respond(synthetic_message, chat_id, call.from_user.id)
            text = (
                response.get("text", "")
                if isinstance(response, dict)
                else str(response)
            )
            reply_markup = (
                response.get("reply_markup") if isinstance(response, dict) else None
            )
            _send_voice_reply(chat_id, text, reply_markup=reply_markup)
        except Exception as e:
            print(f"[Error] Task callback: {e}")
            _log_handler_exception(
                e,
                "telegram_handlers.handle_task_callback",
                chat_id=getattr(call.message.chat, "id", None),
                extra={"callback_data": call.data},
            )
            telegram_bot.answer_callback_query(call.id, "حدث خطأ")

    @telegram_bot.callback_query_handler(func=lambda call: call.data.startswith("img_"))
    def handle_image_callback(call):
        try:
            chat_id = call.message.chat.id
            find_user_profile(chat_id, mongo_db=getattr(agent, "mongo_db", None))
            # callback_data: "img_variation:r" → نستخرج action ومصدر الصورة
            raw = call.data
            if ":" in raw:
                action_data, src_tag = raw.split(":", 1)
            else:
                # backward compat لزر قديم بدون tag
                action_data, src_tag = raw, "g"
            tag_to_source = {"s": "camera_snapshot", "r": "camera_room_scan", "g": "generated"}
            last_source = tag_to_source.get(src_tag, "generated")
            # نضبط call.data للـ logic القديم
            call.data = action_data
            print(f"[img_btn] {action_data} source={last_source!r}")

            # نسخة ثانية لصورة كاميرا → نتجاوز الـ LLM ونستدعي الكاميرا مباشرة
            if call.data == "img_variation" and last_source == "camera_snapshot":
                telegram_bot.answer_callback_query(call.id, "🔄")
                _chat_action(telegram_bot, chat_id, "upload_photo")
                try:
                    from app.integrations.esp32_camera import get_camera_client
                    snap = get_camera_client().capture_snapshot(timeout_sec=20.0)
                except Exception as exc:
                    snap = None
                    print(f"[cam button] direct snapshot failed: {exc}")
                if snap:
                    _send_photo_async(chat_id, snap, "", source="camera_snapshot")
                else:
                    telegram_bot.send_message(chat_id, "ما قدرت ألتقط الصورة — ESP-CAM ما رد.")
                return

            if call.data == "img_variation" and last_source == "camera_room_scan":
                telegram_bot.answer_callback_query(call.id, "🔄")
                _chat_action(telegram_bot, chat_id, "upload_photo")
                try:
                    from app.agent.executor.dispatch import capture_room_panorama
                    pano = capture_room_panorama()
                except Exception as exc:
                    pano = None
                    print(f"[cam button] room_scan failed: {exc}")
                if pano:
                    _send_photo_async(chat_id, pano, "", source="camera_room_scan")
                else:
                    telegram_bot.send_message(chat_id, "ما قدرت أمسح الغرفة — ESP-CAM ما رد.")
                return

            if call.data == "img_variation":
                synthetic_message = "اعمل variation نسخة ثانية من نفس الصورة الأخيرة"
            else:
                action_map = {
                    "img_edit": "عدّل نفس الصورة الأخيرة",
                    "img_describe": "اوصف الصورة الأخيرة، شو فيها",
                }
                synthetic_message = action_map.get(call.data, "")
            if not synthetic_message:
                telegram_bot.answer_callback_query(call.id)
                return

            # progress message للأزرار
            _progress_msg = None
            if call.data == "img_edit":
                telegram_bot.answer_callback_query(call.id, "✏️")
                _send_voice_reply(
                    chat_id, "شو التعديل اللي بدك ياه بالصورة؟ احكيلي وبعدّلها 🎨"
                )
                image_state = _get_chat_session(chat_id).setdefault("image_state", {})
                image_state.setdefault("active_image", None)
                image_state.setdefault("history", [])
                image_state["pending_image_action"] = {
                    "action": "edit_last",
                    "followup_question": "شو التعديل اللي بدك ياه بالصورة؟",
                    "last_user_message": "",
                    "asked_at": __import__("datetime").datetime.now().isoformat(),
                }
                return
            elif call.data == "img_variation":
                telegram_bot.answer_callback_query(call.id, "🔄")
                _progress_msg = telegram_bot.send_message(
                    chat_id, "🔄 عم أولّد نسخة جديدة..."
                )
            elif call.data == "img_describe":
                telegram_bot.answer_callback_query(call.id, "🔍")
                _progress_msg = telegram_bot.send_message(
                    chat_id, "🔍 عم أحلل الصورة..."
                )

            _chat_action(telegram_bot, chat_id, "typing")

            if call.data == "img_describe":
                last_bytes = _get_chat_session(chat_id).get("last_image_bytes")
                if last_bytes:
                    analysis = analyze_image_with_azure_fn(
                        last_bytes,
                        "اوصف هالصورة بشكل طبيعي وباختصار بالعربي",
                        create_chat_completion_fn=create_chat_completion_fn,
                        azure_openai_vision_deployment=azure_openai_vision_deployment,
                        azure_openai_chat_deployment=azure_openai_chat_deployment,
                        openai_model=openai_model,
                    )
                    if _progress_msg:
                        try:
                            telegram_bot.delete_message(
                                chat_id, _progress_msg.message_id
                            )
                        except Exception:
                            pass
                    _send_voice_reply(chat_id, analysis)
                    return

            response = _graph_respond(synthetic_message, chat_id, call.from_user.id)

            if _progress_msg:
                try:
                    telegram_bot.delete_message(chat_id, _progress_msg.message_id)
                except Exception:
                    pass

            text = (
                response.get("text", "")
                if isinstance(response, dict)
                else str(response)
            )
            image_bytes = (
                response.get("image_bytes") if isinstance(response, dict) else None
            )
            image_source = (
                response.get("image_source") if isinstance(response, dict) else None
            )
            caption = response.get("caption", "") if isinstance(response, dict) else ""

            if image_bytes:
                _send_photo_async(chat_id, image_bytes, caption or text or "", source=image_source or "generated")
            else:
                _send_voice_reply(chat_id, text)

        except Exception as e:
            print(f"[Error] Image callback: {e}")
            _log_handler_exception(
                e,
                "telegram_handlers.handle_image_callback",
                chat_id=getattr(call.message.chat, "id", None),
                extra={"callback_data": call.data},
            )
            telegram_bot.answer_callback_query(call.id, "صار خلل.")
