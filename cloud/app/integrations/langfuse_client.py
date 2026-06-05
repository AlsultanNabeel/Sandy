"""Langfuse observability client — traces every LLM call + tool dispatch.

طبقة الـ observability الأساسية لـ Sandy. كل LLM call (Azure, Anthropic, Gemini)
يتسجل tokens + cost + latency + cache hit في Langfuse Cloud.

التهيئة:
- LANGFUSE_PUBLIC_KEY  (pk-lf-...)
- LANGFUSE_SECRET_KEY  (sk-lf-...)
- LANGFUSE_HOST        (default: https://cloud.langfuse.com)

لو المفاتيح مش متوفّرة → جميع الـ helpers تصير no-op (الكود يكمّل بدون أعطال).
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

_LANGFUSE: Any = None
_INIT_TRIED: bool = False


def _is_configured() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    )


def get_langfuse() -> Any:
    """Returns singleton Langfuse client or None لو مش مُكوّن."""
    global _LANGFUSE, _INIT_TRIED
    if _LANGFUSE is not None:
        return _LANGFUSE
    if _INIT_TRIED:
        return None
    _INIT_TRIED = True

    if not _is_configured():
        logger.debug("[langfuse] keys not configured — observability disabled")
        return None

    # نفضّل get_client() — يرجع نفس الـ singleton اللي langfuse.openai بيستخدمه.
    # هاد مهم: spans + الـ LLM auto-traces بيتشاركوا نفس الـ client + flush cycle،
    # وإلا spans الـ tool dispatch بتتخزّن بـ client منفصل وما تنبعت أبداً.
    try:
        from langfuse import get_client  # type: ignore
        _LANGFUSE = get_client()
        if _LANGFUSE is not None:
            logger.info("[langfuse] using shared client (get_client)")
            return _LANGFUSE
    except Exception as exc:
        logger.debug("[langfuse] get_client unavailable: %s — fallback to Langfuse()", exc)

    # Fallback لنسخ أقدم ما عندها get_client
    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError:
        logger.warning("[langfuse] package not installed — pip install langfuse")
        return None

    try:
        _LANGFUSE = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip(),
        )
        logger.info("[langfuse] initialized via Langfuse() fallback")
        return _LANGFUSE
    except Exception as exc:
        logger.warning("[langfuse] init failed: %s — continuing without traces", exc)
        return None


@contextlib.contextmanager
def _observation(
    name: str,
    as_type: str,
    input_data: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Iterator[Any]:
    """Context manager موحّد للـ Langfuse v3 observations (span | generation).

    v3 SDK يستخدم `start_as_current_observation(name=, as_type=)` كـ context
    manager — auto-ends عند الخروج. الـ input/metadata بنحطهم عبر `.update()`
    داخل الـ context (أكثر أماناً عبر النسخ).
    """
    lf = get_langfuse()
    if lf is None:
        yield None
        return

    starter = getattr(lf, "start_as_current_observation", None)
    if not callable(starter):
        # نسخة قديمة ما عندها الـ API — no-op بدل crash
        logger.debug("[langfuse] start_as_current_observation unavailable")
        yield None
        return

    try:
        with starter(name=name, as_type=as_type) as obs:
            if obs is not None and (input_data is not None or metadata):
                try:
                    obs.update(input=input_data, metadata=metadata or {})
                except Exception:
                    pass
            yield obs
    except Exception as exc:
        logger.debug("[langfuse] %s observation failed: %s", as_type, exc)
        yield None


@contextlib.contextmanager
def trace_generation(
    name: str,
    model: str,
    input_data: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Iterator[Any]:
    """Context manager لـ LLM generation (v3 API).

    الاستخدام:
        with trace_generation("claude-call", model="...", input_data=messages) as gen:
            resp = client.messages.create(...)
            if gen:
                gen.update(output=resp.content, usage_details={...})
    """
    meta = dict(metadata or {})
    if model:
        meta.setdefault("model", model)
    if user_id:
        meta.setdefault("user_id", user_id)
    with _observation(name, "generation", input_data, meta) as gen:
        yield gen


@contextlib.contextmanager
def trace_span(
    name: str,
    input_data: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Iterator[Any]:
    """Span عام (non-LLM) للـ tool dispatch، DB query، إلخ. (v3 API)"""
    with _observation(name, "span", input_data, metadata) as span:
        yield span


def flush() -> None:
    """يبعت كل الـ traces المعلّقة. استدعيه قبل process exit."""
    lf = get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception as exc:
            logger.debug("[langfuse] flush failed: %s", exc)


def shutdown() -> None:
    """Graceful shutdown."""
    lf = get_langfuse()
    if lf is not None:
        try:
            lf.shutdown()
        except Exception as exc:
            logger.debug("[langfuse] shutdown failed: %s", exc)
