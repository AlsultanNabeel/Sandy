"""execute_node: جسر للـ ToolDispatcher.

لو في function_call و tool مسجل → ToolDispatcher. غير هيك → رد دردشة.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from app.agent.graph.state import SandyState, merge_state
from app.utils.session import build_session_from_state as _build_session_from_state

logger = logging.getLogger(__name__)

_stream_tls = threading.local()


def set_stream_hooks(on_start, on_chunk):
    """يحدد callbacks للـ streaming — thread-local، يُستدعى من Telegram handler."""
    _stream_tls.on_start = on_start
    _stream_tls.on_chunk = on_chunk


def clear_stream_hooks():
    _stream_tls.on_start = None
    _stream_tls.on_chunk = None


def _get_stream_hooks():
    on_start = getattr(_stream_tls, "on_start", None)
    on_chunk = getattr(_stream_tls, "on_chunk", None)
    return (on_start, on_chunk) if on_start and on_chunk else None

# tools تُعالَج عبر routing logic — ليس عبر ToolDispatcher
_META_TOOL_NAMES = frozenset({
    "ask_clarification", "request_confirmation",
    "chat_respond", "chat_emotional",
    "pending_confirm", "pending_reject", "pending_select",
})

# prefixes تتطلب صلاحية owner — أي tool يبدأ بأحد هذه لا ينفَّذ إلا للأونر
# chat/weather/web_search/image لا تحتاج صلاحية خاصة لأنها بيانات عامة
_OWNER_ONLY_PREFIXES = (
    "calendar_", "task_", "email_", "reminder_",
    "github_", "hardware_", "heroku_", "memory_", "cost_",
)


def _requires_owner(tool_name: str) -> bool:
    return any(tool_name.startswith(p) for p in _OWNER_ONLY_PREFIXES)

_CHAT_INTENTS = frozenset(
    {
        "chat.general",
        "chat.emotional_support",
        "chat.proactive_engage",
    }
)

# pending.* بدون pending_state نشط → يُعامل كـ chat
_PENDING_INTENTS = frozenset(
    {
        "pending.confirm",
        "pending.reject",
        "pending.select_option",
    }
)

# Singleton — يُنشأ مرة واحدة عند أول استخدام
_chat_completion_fn = None


def _get_chat_completion_fn():
    """Singleton: يبني الـ client مرة واحدة فقط طول عمر البروسيس."""
    global _chat_completion_fn
    if _chat_completion_fn is not None:
        return _chat_completion_fn

    from openai import AzureOpenAI, OpenAI
    from app.integrations.openai_client import make_chat_completion_fn
    from app.config import (
        AZURE_OPENAI_ENDPOINT as azure_endpoint,
        AZURE_OPENAI_API_KEY as azure_key,
        AZURE_OPENAI_API_VERSION as azure_version,
        AZURE_OPENAI_CHAT_DEPLOYMENT as azure_deployment,
        OPENAI_API_KEY as openai_key,
        OPENAI_MODEL as openai_model,
    )

    azure_client = None
    if azure_endpoint and azure_key and azure_deployment:
        azure_client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=azure_key,
            api_version=azure_version,
        )

    openai_client = OpenAI(api_key=openai_key) if openai_key else None

    if not openai_client and not azure_client:
        raise RuntimeError("[execute_node] No OpenAI/Azure credentials configured")

    _chat_completion_fn = make_chat_completion_fn(
        openai_client=openai_client or azure_client,
        azure_openai_client=azure_client,
        openai_model=openai_model,
        azure_chat_deployment=azure_deployment,
    )
    return _chat_completion_fn




def _noop_save(*args, **kwargs) -> None:
    pass


def _get_current_time_context() -> str:
    """يبني سياق الوقت الحالي لحقنه في system prompt."""
    try:
        from datetime import datetime
        from app.utils.time import USER_TZ

        now = datetime.now(USER_TZ)
        day_names = {
            0: "الاثنين",
            1: "الثلاثاء",
            2: "الأربعاء",
            3: "الخميس",
            4: "الجمعة",
            5: "السبت",
            6: "الأحد",
        }
        day_ar = day_names.get(now.weekday(), "")
        return (
            f"[الوقت الحالي: {now.strftime('%I:%M %p').replace('AM', 'ص').replace('PM', 'م')} | "
            f"التاريخ: {now.strftime('%d/%m/%Y')} ({day_ar})]"
        )
    except Exception:
        return ""


_CHAT_BEHAVIOR_RULES = """
قواعد الرد:
- ما تنهي كل رد بسؤال متابعة (مثل "شو رأيك؟"، "بدك تطلع؟"، "ماشي؟"). أضيفي السؤال فقط لو السياق فعلاً يستدعيه (الموضوع ناقص أو المستخدم سأل رأيك).
- ما تفترضي حالة المستخدم (سهران/متعب/زعلان/مشغول) من الوقت أو التحية لحالها. ردي على اللي قاله بالضبط بس. لو حاب يشاركك حالته، رح يقولها.
- جواب التحيات (مرحبا/كيفك/صباح الخير) قصير ومباشر — بدون افتراضات وبدون سؤال متابعة إجباري.
- إذا قلت قصة أو معلومة، اتركيها تنتهي عند آخر جملة فيها — مش لازم تسألي "شو رأيك" بعدها.
"""


def _handle_chat(state: SandyState, create_chat_completion_fn) -> str:
    """يستدعي Azure LLM لـ chat intents ويرجع الرد كـ string."""

    from app.config import SANDY_PERSONALITY
    sandy_personality = SANDY_PERSONALITY

    persona_snippet = state.get("persona_snippet") or ""
    time_ctx = _get_current_time_context()
    system = sandy_personality or (
        "أنتِ ساندي (Sandy)، شريكة تقنية ذكية ومساعدة شخصية. "
        "ردودك طبيعية وموجزة بالعامية. لا تشرحي نفسك."
    )
    system += f"\n{_CHAT_BEHAVIOR_RULES}"
    if time_ctx:
        system += f"\n{time_ctx}"
    if persona_snippet:
        system += f"\n\nنبرة هذا الرد: {persona_snippet}"

    messages = [{"role": "system", "content": system}]
    for msg in (state.get("conversation_history") or [])[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": state["message"]})

    # Streaming path — thread-local hooks set by Telegram handler
    hooks = _get_stream_hooks()
    if hooks:
        on_start, on_chunk = hooks
        on_start()
        try:
            stream = create_chat_completion_fn(
                messages=messages, max_tokens=400, temperature=0.7, stream=True
            )
            full_text = ""
            for chunk in stream:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                full_text += delta
                if delta:
                    on_chunk(full_text)
            return full_text.strip()
        except Exception as exc:
            logger.warning(f"[execute_node] streaming failed, falling back: {exc}")

    # #4: Graceful Degradation — Azure → OpenAI direct → persona_snippet فقط
    try:
        from app.agent.model_fallback import chat_with_fallback
        response = chat_with_fallback(
            messages, create_chat_completion_fn, max_tokens=400, temperature=0.7
        )
        if response is not None:
            return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error(f"[execute_node] chat LLM failed after fallback: {exc}")

    return state.get("persona_snippet") or ""



def _get_mongo_db():
    """Lazy mongo_db getter."""
    try:
        from app.agent.facade.agent import mongo_db
        return mongo_db
    except Exception:
        return None


def execute_node(state: SandyState) -> SandyState:
    """LangGraph node: ينفذ الـ intent عبر الـ planner والـ handlers الموجودة.

    لو في function_calls (2+) → نفّذ كل tool بالترتيب.
    لو في function_call واحد + tool مسجل → ToolDispatcher.
    غير هيك → الـ planner القديم (للحذف في Phase 7).
    """
    # عدة tools برسالة وحدة
    multi_fcs = state.get("function_calls") or []
    if len(multi_fcs) >= 2:
        from app.agent.tools.registry import get_registry
        from app.agent.tools.dispatcher import ToolDispatcher, DispatchContext
        from app.utils.nlp_normalizer import normalize_user_message
        from app.utils.user_profiles import is_owner_chat_id

        try:
            create_chat_completion_fn = _get_chat_completion_fn()
        except Exception as exc:
            logger.error(f"[execute_node] multi-tool: chat fn failed: {exc}")
            create_chat_completion_fn = None

        session = _build_session_from_state(state)
        normalized = normalize_user_message(state["message"])
        registry = get_registry()
        dispatcher = ToolDispatcher()

        context = DispatchContext(
            user_message=state["message"],
            normalized_message=normalized,
            session=session,
            state=state,
            mongo_db=_get_mongo_db(),
            create_chat_completion_fn=create_chat_completion_fn,
        )

        replies: list = []
        any_handled = False
        for fc_item in multi_fcs:
            t_name = fc_item.get("name", "")
            t_args = fc_item.get("args") or {}
            if t_name in _META_TOOL_NAMES:
                continue
            tool = registry.get_tool(t_name)
            if not tool:
                continue
            if _requires_owner(t_name) and not is_owner_chat_id(state.get("chat_id")):
                logger.warning(f"[execute_node] multi: blocked {t_name} for non-owner")
                continue
            try:
                r = dispatcher.dispatch(t_name, t_args, context)
                if r.get("reply"):
                    replies.append(r["reply"])
                if r.get("handled"):
                    any_handled = True
                logger.info(f"[execute_node] multi: {t_name} → handled={r.get('handled')}")
            except Exception as exc:
                logger.error(f"[execute_node] multi: {t_name} failed: {exc}")

        combined = "\n".join(replies) if replies else "تم."
        updates: Dict[str, Any] = {
            "execution_result": {
                "handled": any_handled,
                "reply": combined,
                "source": "execute_node_multi",
            },
        }
        if combined:
            updates["final_response"] = combined
        return merge_state(state, updates)

    # tool واحد
    fc = state.get("function_call")
    if fc:
        from app.agent.tools.registry import get_registry
        from app.agent.tools.dispatcher import ToolDispatcher, DispatchContext
        from app.utils.nlp_normalizer import normalize_user_message

        tool_name = fc.get("name", "")
        tool_args = fc.get("args") or {}
        registry = get_registry()
        tool = registry.get_tool(tool_name)

        if tool and tool_name not in _META_TOOL_NAMES:
            # حماية: أدوات شخصية لا تُنفَّذ إلا للأونر — بغض النظر عن صياغة الطلب
            if _requires_owner(tool_name):
                from app.utils.user_profiles import is_owner_chat_id
                if not is_owner_chat_id(state.get("chat_id")):
                    logger.warning(
                        f"[execute_node] blocked tool={tool_name} for non-owner chat_id={state.get('chat_id')}"
                    )
                    return merge_state(state, {
                        "execution_result": {"handled": True, "reply": "هذا خاص بنبيل 😊"},
                    })

            try:
                create_chat_completion_fn = _get_chat_completion_fn()
                session = _build_session_from_state(state)
                normalized = normalize_user_message(state["message"])

                context = DispatchContext(
                    user_message=state["message"],
                    normalized_message=normalized,
                    session=session,
                    state=state,
                    mongo_db=_get_mongo_db(),
                    create_chat_completion_fn=create_chat_completion_fn,
                )
                result = ToolDispatcher().dispatch(tool_name, tool_args, context)
            except Exception as exc:
                import traceback
                logger.error(f"[execute_node] FC dispatch failed: {exc}")
                print(f"[execute_node] ERROR tool={tool_name}: {type(exc).__name__}: {exc}", flush=True)
                print(traceback.format_exc(), flush=True)
                try:
                    from app.integrations.sentry_config import capture_exception
                    capture_exception(exc, context={"node": "execute_node", "tool": tool_name})
                except Exception:
                    pass
                result = {"handled": False, "reply": "حصل خطأ، حاول مرة ثانية."}

            handled = result.get("handled", False)
            reply = result.get("reply") or ""
            new_pending = session.get("pending_action")
            # consumed pending → treat as cleared so it doesn't leak into next request
            if isinstance(new_pending, dict) and new_pending.get("consumed_at"):
                new_pending = None
            pending_archived = session.get("archived_pending") or []

            reply_markup = result.get("reply_markup")
            _bulk_actions = {"delete_all", "complete_all"}
            if (
                new_pending
                and isinstance(new_pending, dict)
                and new_pending.get("confirmation_status") == "pending"
                and new_pending.get("action") not in _bulk_actions
                and reply_markup is None
            ):
                try:
                    import telebot.types as _tg
                    markup = _tg.InlineKeyboardMarkup()
                    markup.row(
                        _tg.InlineKeyboardButton("✅ نعم", callback_data="confirm_yes"),
                        _tg.InlineKeyboardButton("❌ لا", callback_data="confirm_no"),
                    )
                    reply_markup = markup
                except Exception:
                    pass

            updates: Dict[str, Any] = {
                "pending_state": new_pending,
                "pending_archived": pending_archived,
                "execution_result": {
                    "handled": handled,
                    "reply": reply,
                    "reply_markup": reply_markup,
                    "image_bytes": result.get("image_bytes"),
                    "image_source": result.get("image_source"),
                    "caption": result.get("caption", ""),
                    "source": "execute_node_fc",
                },
            }
            if reply:
                updates["final_response"] = reply
            return merge_state(state, updates)

    # request_confirmation بدون pending_state:
    # Gemini طلب تأكيد بس لسا ما في pending → اعرض السؤال مباشرة
    if fc and fc.get("name") == "request_confirmation" and not state.get("pending_state"):
        summary = (fc.get("args") or {}).get("summary", "هذه العملية")
        reply = f"متأكد تريد: {summary}؟"
        try:
            import telebot.types as _tg
            markup = _tg.InlineKeyboardMarkup()
            markup.row(
                _tg.InlineKeyboardButton("✅ نعم", callback_data="confirm_yes"),
                _tg.InlineKeyboardButton("❌ لا", callback_data="confirm_no"),
            )
        except Exception:
            markup = None
        return merge_state(state, {
            "execution_result": {
                "handled": True, "reply": reply,
                "reply_markup": markup, "source": "execute_node_confirm",
            },
            "final_response": reply,
        })

    # الـ planner القديم
    intent = state.get("intent") or "chat.general"

    # pending.* بدون pending_state نشط (router مسحه) → ردّ طبيعي كـ chat
    if intent in _PENDING_INTENTS and not state.get("pending_state"):
        intent = "chat.general"

    if intent in _CHAT_INTENTS:
        reply = ""
        try:
            create_chat_completion_fn = _get_chat_completion_fn()
            _t_chat = time.perf_counter()
            reply = _handle_chat(state, create_chat_completion_fn)
            logger.info(f"[execute] handle_chat: {(time.perf_counter()-_t_chat)*1000:.0f}ms")
        except Exception as exc:
            logger.error(f"[execute_node] chat setup failed: {exc}")
            reply = ""
        if not reply:
            reply = "وينك؟ 😄"
        return merge_state(
            state,
            {
                "execution_result": {
                    "handled": bool(reply),
                    "reply": reply,
                    "source": "execute_node_chat",
                },
                "final_response": reply or None,
            },
        )

    # FC غير مفعّل أو tool غير مسجل → ردّ دردشة fallback
    session: Dict[str, Any] = {}
    try:
        create_chat_completion_fn = _get_chat_completion_fn()
        reply = _handle_chat(state, create_chat_completion_fn)
        result = {"handled": bool(reply), "reply": reply or "وينك؟ 😄"}
    except Exception as exc:
        logger.error(f"[execute_node] fallback chat failed: {exc}")
        try:
            from app.integrations.sentry_config import capture_exception

            capture_exception(exc, context={"node": "execute_node", "intent": intent})
        except Exception:
            pass
        result = {"handled": False, "reply": "حصل خطأ، حاول مرة ثانية."}

    handled = result.get("handled", False)
    reply = result.get("reply") or ""
    reply_markup = result.get("reply_markup")
    image_bytes = result.get("image_bytes")
    image_source = result.get("image_source")
    caption = result.get("caption", "")

    # Propagate pending_state set by action handlers back to graph state
    new_pending = session.get("pending_action")
    pending_archived = session.get("archived_pending") or []
    if isinstance(pending_archived, dict):
        pending_archived = [pending_archived]

    # Add confirm/reject buttons when a pending action needs confirmation
    if (
        new_pending
        and isinstance(new_pending, dict)
        and new_pending.get("confirmation_status") == "pending"
        and reply_markup is None
    ):
        try:
            import telebot.types as tgtypes

            markup = tgtypes.InlineKeyboardMarkup()
            markup.row(
                tgtypes.InlineKeyboardButton("✅ نعم", callback_data="confirm_yes"),
                tgtypes.InlineKeyboardButton("❌ لا", callback_data="confirm_no"),
            )
            reply_markup = markup
        except Exception:
            pass

    # Propagate gmail_last_list if updated by email handlers
    new_gmail_list = session.get("gmail_last_list")
    if new_gmail_list is not None:
        updates_extra: Dict[str, Any] = {"gmail_list_state": new_gmail_list}
    else:
        updates_extra = {}

    updates: Dict[str, Any] = {
        "pending_state": new_pending,
        "pending_archived": pending_archived,
        **updates_extra,
        "execution_result": {
            "handled": handled,
            "reply": reply,
            "reply_markup": reply_markup,
            "image_bytes": image_bytes,
            "image_source": image_source,
            "caption": caption,
            "source": "execute_node",
        },
    }
    if reply:
        updates["final_response"] = reply

    return merge_state(state, updates)
