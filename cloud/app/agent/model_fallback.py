"""موديلات احتياطية لما الـ API يفشل.

maestro: Gemini Flash، وبعدها GPT-4o للـ routing، وبعدها default آمن.
execute: Azure GPT، وبعدها OpenAI مباشرة، وبعدها persona_snippet بس.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GPT_ROUTING_SYSTEM = """أنت مساعد يحدد نوع طلب المستخدم.
أجب بـ JSON فقط: {"intent": "<نوع الطلب>"}
الأنواع: task | reminder | calendar | email | chat | search | other"""

_openai_direct_client: Any = None


def _get_openai_direct_client() -> Optional[Any]:
    global _openai_direct_client
    if _openai_direct_client is None:
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not openai_key:
            return None
        from openai import OpenAI
        _openai_direct_client = OpenAI(api_key=openai_key)
    return _openai_direct_client


def route_with_gpt(message: str) -> Optional[str]:
    """يطلع الـ intent بـ GPT لما Gemini يفشل."""
    try:
        from app.agent.nodes.execute import _get_chat_completion_fn
        import json

        fn = _get_chat_completion_fn()
        if fn is None:
            return None

        resp = fn(
            messages=[
                {"role": "system", "content": _GPT_ROUTING_SYSTEM},
                {"role": "user", "content": message[:500]},
            ],
            max_tokens=60,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.lstrip().lower().startswith("json"):
                raw = raw.lstrip()[4:]
            raw = raw.strip()
        try:
            data = json.loads(raw)
            intent = str(data.get("intent") or "chat").lower()
        except (ValueError, AttributeError):
            # Malformed reply: degrade to plain chat instead of giving up.
            intent = "chat"
        logger.info(f"[model_fallback] GPT routing fallback, intent={intent}")
        return intent
    except Exception as exc:
        logger.debug(f"[model_fallback] GPT routing failed: {exc}")
        return None


def chat_with_fallback(
    messages: list,
    primary_fn,
    *,
    max_tokens: int = 500,
    temperature: float = 0.7,
) -> Optional[Any]:
    """يجرّب primary، وبعدها OpenAI مباشرة، وإلا يرجّع None."""
    try:
        return primary_fn(messages=messages, max_tokens=max_tokens, temperature=temperature)
    except Exception as exc:
        logger.warning(f"[model_fallback] primary chat failed: {exc}, trying OpenAI direct")

    try:
        client = _get_openai_direct_client()
        if client is None:
            return None
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o")
        return client.chat.completions.create(
            model=openai_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:
        logger.error(f"[model_fallback] OpenAI direct also failed: {exc}")
        return None
