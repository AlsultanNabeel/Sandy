"""Sandy graph runner.

Pipeline:
  fc_router (single FC call) -> soul_node -> router_node
       -> route_after_router picks one of:
  pending_node | execute_node | clarify_node
       ->
  response_node -> SandyState with final_response ready

STM: loads conversation_history from Redis before the run, saves the user
message and reply after.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timezone
from typing import Any, Dict, List, Optional

from app.agent.graph.state import SandyState, create_initial_state, merge_state
from app.agent.nodes.soul import soul_node
from app.agent.nodes.router import router_node, route_after_router
from app.agent.nodes.pending import pending_node
from app.agent.nodes.execute import execute_node
from app.agent.nodes.clarify import clarify_node
from app.agent.nodes.response import response_node

logger = logging.getLogger(__name__)


# مزامنة STM


def load_stm(chat_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Public STM accessor for callers outside the graph (e.g. brainstorm)."""
    return _stm_load(chat_id, user_id)


def _stm_load(chat_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Read STM from Redis (synchronous)."""
    try:
        from app.utils.redis_stm import get_redis_stm_client

        client = get_redis_stm_client()
        if not client.enabled or not client.client:
            return []
        key = f"stm:{chat_id}:{user_id}"
        data = client.client.get(key)
        return json.loads(data) if data else []
    except Exception as exc:
        logger.warning(f"[graph] STM load failed: {exc}")
        return []


def _is_duplicate_memory(
    mongo_db, chat_id: str, embedding: Optional[List[float]], threshold: float = 0.92
) -> bool:
    """Return True if a near-identical summary already exists (cosine similarity ≥ threshold)."""
    if not embedding or mongo_db is None:
        return False
    try:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "sandy_vector_index",
                    "path": "embedding",
                    "queryVector": embedding,
                    "numCandidates": 10,
                    "limit": 1,
                    "filter": {
                        "chat_id": {"$eq": chat_id},
                        "label": "conversation_summary",
                    },
                }
            },
            {"$project": {"score": {"$meta": "vectorSearchScore"}}},
        ]
        results = list(mongo_db["sandy_memories"].aggregate(pipeline))
        return bool(results and results[0].get("score", 0) >= threshold)
    except Exception:
        return False


def _summarize_to_ltm(chat_id: str, user_id: str, messages: List[Dict[str, Any]]) -> None:
    """Summarize overflowing STM messages and save to MongoDB LTM (dedup-protected)."""
    try:
        from datetime import datetime, timezone
        from app.config import (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
                                AZURE_OPENAI_API_VERSION, AZURE_OPENAI_CHAT_DEPLOYMENT)
        from app.agent.facade.agent import mongo_db
        from openai import AzureOpenAI

        if mongo_db is None:
            logger.warning("[graph] STM→LTM skipped: mongo_db is None (facade not initialized?)")
            return
        if not AZURE_OPENAI_API_KEY:
            return

        turns = "\n".join(
            f"{'نبيل' if m['role'] == 'user' else 'Sandy'}: {m['content']}"
            for m in messages if m.get("content")
        )
        client = AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
        )
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "لخّص المحادثة التالية في جملتين أو ثلاث بالعربي. ركّز على القرارات والمعلومات المهمة فقط."},
                {"role": "user", "content": turns},
            ],
            max_tokens=200,
        )
        summary = resp.choices[0].message.content.strip()
        if not summary:
            return

        # Compute embedding first for deduplication check
        vec: Optional[List[float]] = None
        try:
            from app.agent.semantic_memory import _embed
            vec = _embed(summary)
        except Exception:
            pass

        if _is_duplicate_memory(mongo_db, chat_id, vec):
            logger.info(f"[graph] STM→LTM duplicate skipped for {chat_id}")
            return

        doc: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "label": "conversation_summary",
            "summary": summary,
            "source_turns": len(messages),
            "created_at": datetime.now(timezone.utc),
        }
        if vec:
            doc["embedding"] = vec
        mongo_db["sandy_memories"].insert_one(doc)
        logger.info(f"[graph] STM→LTM summary saved for {chat_id}")
    except Exception as exc:
        logger.debug(f"[graph] STM summarization failed: {exc}")


def _summarize_to_ltm_async(chat_id: str, user_id: str, messages: List[Dict[str, Any]]) -> None:
    import threading
    threading.Thread(
        target=_summarize_to_ltm, args=(chat_id, user_id, messages), daemon=True
    ).start()


def _stm_save(chat_id: str, user_id: str, user_msg: str, assistant_reply: str) -> None:
    """يحفظ رسالة المستخدم + رد ساندي في Redis بشكل آمن (Atomic Transaction)."""
    try:
        from app.utils.redis_stm import get_redis_stm_client, MAX_STM_MESSAGES, STM_TTL
        import datetime
        import redis

        client = get_redis_stm_client()
        if not client.enabled or not client.client:
            return

        key = f"stm:{chat_id}:{user_id}"
        now = datetime.datetime.now(timezone.utc).isoformat()

        with client.client.pipeline() as pipe:
            for _ in range(3):
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    history: List[Dict[str, Any]] = json.loads(raw) if raw else []

                    history.append({"role": "user", "content": user_msg, "timestamp": now})
                    if assistant_reply:
                        history.append({"role": "assistant", "content": assistant_reply, "timestamp": now})
                    if len(history) > MAX_STM_MESSAGES:
                        _summarize_to_ltm_async(chat_id, user_id, history[:-MAX_STM_MESSAGES])
                    history = history[-MAX_STM_MESSAGES:]

                    pipe.multi()
                    pipe.setex(key, STM_TTL, json.dumps(history))
                    pipe.execute()
                    break
                except redis.WatchError:
                    continue
    except Exception as exc:
        logger.warning(f"[graph] STM atomic save failed: {exc}")


# A1: حفظ اللحظة العاطفية في LTM (fire-and-forget)

_SIGNIFICANT_MOODS = {"stressed", "frustrated", "sad", "angry", "happy", "excited"}


def _save_emotional_async(state: "SandyState", message: str) -> None:
    """A1: يحفظ لحظة عاطفية + A3: يحفظ تصحيح أسلوبي — background thread."""
    import threading

    mood = state.get("mood") or ""
    chat_id = state.get("chat_id", "")
    user_id = state.get("user_id", "")

    def _do_save():
        try:
            from app.agent.facade.agent import mongo_db

            if mongo_db is None:
                logger.warning("[graph] LTM save skipped: mongo_db is None (facade not initialized?)")
                return

            # A1: ذاكرة عاطفية
            if mood in _SIGNIFICANT_MOODS:
                from app.agent.emotional_ltm import save_emotional_moment
                save_emotional_moment(chat_id, user_id, mood, message[:200], mongo_db)

            # A3: تصحيح أسلوبي
            from app.agent.style_memory import detect_style_correction, save_style_preference
            if detect_style_correction(message):
                save_style_preference(chat_id, user_id, message[:300], message, mongo_db)

            # #1: تسجيل وقت النشاط للصحة
            from app.agent.health_monitor import record_activity
            record_activity(chat_id, user_id, mongo_db)

            # B2: استخراج وحفظ العلاقات (أخوي محمد، صديقتي سارة، ...)
            from app.agent.relationships_memory import save_detected_relationships
            save_detected_relationships(chat_id, user_id, message, mongo_db)

            # D2: استخراج وحفظ الدروس المستفادة
            from app.agent.lessons_memory import save_detected_lesson
            save_detected_lesson(chat_id, user_id, message, mongo_db)

            # F7: استخراج وحفظ المعالم المهمة (تخرج، زواج، انتقال، ...)
            from app.agent.shared_history import save_detected_milestone
            save_detected_milestone(chat_id, user_id, message, mongo_db)

            # C2: تتبّع الاهتمامات (للمشاركة الذكية لاحقاً)
            from app.agent.interests_tracker import track_message_interests
            track_message_interests(chat_id, user_id, message, mongo_db)

        except Exception as exc:
            logger.debug(f"[graph] LTM save skipped: {exc}")

    threading.Thread(target=_do_save, daemon=True).start()


# التوجيه


def _route_intent(state: "SandyState") -> "SandyState":
    """توجيه بنداء FC واحد على كامل كتالوج الأدوات.

    استبدلنا التصميم القديم (RouterAgent classify + specialist filtering = نداءين
    LLM متسلسلين) بنداء ``route_with_fc`` واحد يرى كل الأدوات ويختار مباشرة:
      • أسرع — نداء واحد بدل اثنين (~0.8–1.5s أقل لكل رسالة غير-chat).
      • أدق — ما في خطر "سوء تصنيف" يحجب الأداة الصح عن الموديل.
      • مش أغلى — prompt caching بيغطّي كتالوج الأدوات الثابت؛ والـ chat نفسه
        بيختاره الـ FC كـ chat_respond بدل نداء تصنيف منفصل.

    (الـ RouterAgent القديم + الـ specialists انحذفوا — ما عاد إلهم دور.)
    """
    from app.agent.agents.fc_router import route_with_fc
    from app.agent.tools.registry import get_registry

    declarations = get_registry().get_function_declarations()
    logger.info("[router] single-call FC routing with %d tools", len(declarations))
    return route_with_fc(state, declarations, agent_name="router")


# الـ graph runner


def run_graph(
    message: str,
    user_id: str,
    chat_id: str,
    *,
    pending_state: Optional[Dict[str, Any]] = None,
    source: str = "user",
    image_state: Optional[Dict[str, Any]] = None,
    gmail_list_state: Optional[Dict[str, Any]] = None,
) -> SandyState:
    """ينفذ الـ Sandy pipeline كاملاً وتُرجع الـ SandyState النهائية.

    Args:
        message: رسالة المستخدم
        user_id: معرف المستخدم في Telegram
        chat_id: معرف المحادثة
        pending_state: pending action نشط (اختياري)
        source: مصدر الرسالة (user / proactive / hardware)

    Returns:
        SandyState مع final_response جاهز للإرسال
    """
    # 1. حمّل conversation history من Redis
    history = _stm_load(chat_id, user_id)

    # 2. ابنِ الـ initial state
    state = create_initial_state(
        message=message,
        user_id=user_id,
        chat_id=chat_id,
        source=source,
        pending_state=pending_state,
        image_state=image_state,
        gmail_list_state=gmail_list_state,
    )
    if history:
        state = merge_state(state, {"conversation_history": history})

    # 3. شغّل الـ pipeline — per-node latencies moved to Langfuse spans (R5).
    # Heroku logs نظيفة هلق؛ تفاصيل التوقيت موجودة في Langfuse traces.
    # نلفّ كل الـ pipeline بـ parent span واحد عشان كل شي (maestro LLM + tool
    # dispatch + ...) ينزل تحت trace واحد لكل رسالة تيليغرام بدل traces مبعثرة.
    rid = state["session_id"]
    t_total = time.perf_counter()

    try:
        from app.integrations.langfuse_client import trace_span as _trace_span
        _pipeline_ctx = _trace_span(
            name="sandy.pipeline",
            input_data={"message": str(message)[:500]},
            metadata={
                "session_id": str(state.get("chat_id") or ""),
                "user_id": str(state.get("user_id") or ""),
            },
        )
    except Exception:
        import contextlib as _ctxlib
        _pipeline_ctx = _ctxlib.nullcontext(None)

    try:
        with _pipeline_ctx:
            # Start soul MongoDB queries in parallel with routing (~1.5s savings)
            try:
                from app.agent.nodes.soul import start_soul_prefetch
                _prefetch = start_soul_prefetch(state["chat_id"], state["user_id"], message)
                state = merge_state(state, {"soul_prefetch": _prefetch})
            except Exception:
                pass

            # توجيه: نداء FC واحد على كامل الكتالوج
            state = _route_intent(state)

            state = soul_node(state)
            state = router_node(state)
            next_node = route_after_router(state)

            if next_node == "pending_node":
                state = pending_node(state)
            elif next_node == "clarify_node":
                state = clarify_node(state)
            else:
                state = execute_node(state)

            state = response_node(state)

    except Exception as exc:
        logger.error(
            f"[{rid}] pipeline failed ({(time.perf_counter()-t_total)*1000:.0f}ms): {exc}"
        )
        try:
            from app.integrations.sentry_config import capture_exception

            capture_exception(
                exc, context={"graph": "run_graph", "message": message[:100]}
            )
        except Exception:
            pass
        state = merge_state(
            state,
            {
                "final_response": "حصل خطأ، حاول مرة ثانية.",
                "error": str(exc),
            },
        )

    # 4. A1: احفظ لحظة عاطفية مهمة في LTM (background — لا يبطئ الرد)
    _save_emotional_async(state, message)

    # 5. احفظ في Redis STM
    final_reply = state.get("final_response") or ""
    _stm_save(chat_id, user_id, message, final_reply)

    # 6. حدّث الحالة المشتركة عبر المنصات (background)
    _update_session_state_async(state)

    return state


def _update_session_state_async(state: "SandyState") -> None:
    """Update cross-session state in background after every SA turn."""
    import threading

    chat_id = state.get("chat_id", "")
    mood = state.get("mood") or ""
    if not chat_id:
        return

    def _do():
        try:
            from app.agent.facade.agent import mongo_db
            from app.agent.session_state import update_session_state
            update_session_state(chat_id, mongo_db, mood=mood, platform="telegram")
        except Exception:
            pass

    threading.Thread(target=_do, daemon=True).start()


def get_final_reply(state: SandyState) -> Dict[str, Any]:
    """يستخرج الرد النهائي بشكل جاهز للإرسال عبر Telegram.

    Returns:
        {"text": str, "reply_markup": Optional[dict], "image_bytes": Optional[bytes], "caption": str}
    """
    _TG_LIMIT = 4096
    execution = state.get("execution_result") or {}
    text = state.get("final_response") or ""
    chunks = []
    while len(text) > _TG_LIMIT:
        split_at = text.rfind("\n", 0, _TG_LIMIT)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, _TG_LIMIT)
        if split_at <= 0:
            split_at = _TG_LIMIT
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    chunks.append(text)
    return {
        "text": chunks[0],
        "chunks": chunks,
        "reply_markup": execution.get("reply_markup"),
        "image_bytes": execution.get("image_bytes"),
        "image_source": execution.get("image_source"),
        "caption": execution.get("caption", ""),
    }
