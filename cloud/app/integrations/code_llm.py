"""Unified LLM client for coding workflows — Claude primary, Azure GPT fallback.

This module wraps multiple providers behind one API so coding-related flows
can try Claude Sonnet first and fall back to an Azure OpenAI coding deployment
if Claude is unavailable.

This avoids coupling higher-level orchestrators directly to a specific LLM
provider.

    1. Try Claude Sonnet (via claude_vertex) first.
    2. If Claude is unavailable, fails, or returns an error, fall back to a
       dedicated Azure OpenAI coding deployment.

The fallback uses its OWN Azure deployment — separate from the chat
deployment used by ``route_with_fc`` — so we can wire a stronger model (gpt-4o,
o3, etc.) just for code generation without affecting chat latency/cost.

Env vars:
    AZURE_OPENAI_ENDPOINT
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_API_VERSION
    AZURE_OPENAI_CODE_DEPLOYMENT  — e.g. 'gpt-4o' or 'o3'. **Distinct from
                                    AZURE_OPENAI_CHAT_DEPLOYMENT.**

The public API mirrors ``claude_vertex`` exactly (``complete``,
``complete_json``, ``is_available``) so existing call sites can just swap the
import.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.integrations import claude_vertex

logger = logging.getLogger(__name__)


_AZURE_CODE_DEPLOYMENT = os.getenv("AZURE_OPENAI_CODE_DEPLOYMENT", "").strip()


def _azure_client() -> Optional[Any]:
    """Lazy Azure client init. Returns None if Azure isn't configured."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()
    if not endpoint or not api_key:
        return None
    try:
        from openai import AzureOpenAI
        return AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
        )
    except Exception as exc:
        logger.warning("[code_llm] Azure client init failed: %s", exc)
        return None


def _azure_available() -> bool:
    return bool(_AZURE_CODE_DEPLOYMENT) and _azure_client() is not None


def is_available() -> bool:
    """True if at least one provider is reachable."""
    return claude_vertex.is_available() or _azure_available()


def _messages_to_azure(messages: List[Dict[str, str]], system: str) -> List[Dict[str, Any]]:
    """Anthropic-style messages → Azure OpenAI chat format.

    Anthropic puts the system prompt outside the messages list; Azure expects
    it as the first message with role='system'.
    """
    converted: List[Dict[str, Any]] = []
    if system:
        converted.append({"role": "system", "content": system})
    for msg in messages or []:
        role = msg.get("role") or "user"
        content = msg.get("content") or ""
        if role not in ("user", "assistant", "system"):
            role = "user"
        converted.append({"role": role, "content": content})
    return converted


# بنحفظ أي API نجح آخر مرة لكل deployment عشان ما نحاول الـ chat كل مرة لو
# الـ deployment أصلاً responses-only (مثل gpt-5.1-codex).
_API_PREFERENCE: Dict[str, str] = {}  # deployment_name → "chat" | "responses"


def _is_unsupported_error(exc: Exception) -> bool:
    """Detect Azure's 400 'operation unsupported' which means we need a
    different API surface."""
    msg = str(exc).lower()
    return "unsupported" in msg and "400" in msg


def _azure_complete_chat(
    client: Any,
    *,
    system: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """Standard Chat Completions API call."""
    try:
        resp = client.chat.completions.create(
            model=_AZURE_CODE_DEPLOYMENT,
            messages=_messages_to_azure(messages, system),
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:
        return {"ok": False, "text": "", "error": str(exc), "_exc": exc}

    try:
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        usage_dict = (
            {
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
            }
            if usage
            else {}
        )
        return {
            "ok": True,
            "text": text,
            "stop_reason": getattr(resp.choices[0], "finish_reason", None),
            "usage": usage_dict,
        }
    except Exception as exc:
        return {"ok": False, "text": "", "error": f"chat parse error: {exc}"}


def _azure_complete_responses(
    client: Any,
    *,
    system: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
) -> Dict[str, Any]:
    """Responses API call — required for gpt-5.x-codex models.

    Responses API merges system + messages into a single 'input' string.
    """
    # Build the combined prompt — Responses API takes plain text or structured items
    parts: List[str] = []
    if system:
        parts.append(system)
    for msg in messages or []:
        role = (msg.get("role") or "user").lower()
        content = msg.get("content") or ""
        prefix = {"user": "User", "assistant": "Assistant", "system": "System"}.get(
            role, "User"
        )
        parts.append(f"{prefix}: {content}")
    combined_input = "\n\n".join(parts)

    try:
        resp = client.responses.create(
            model=_AZURE_CODE_DEPLOYMENT,
            input=combined_input,
            max_output_tokens=max_tokens,
        )
    except Exception as exc:
        return {"ok": False, "text": "", "error": str(exc), "_exc": exc}

    # Responses returns .output_text (convenience) or .output array
    try:
        text = (getattr(resp, "output_text", "") or "").strip()
        if not text:
            # Fallback — walk the output array
            output = getattr(resp, "output", None) or []
            collected: List[str] = []
            for item in output:
                content = getattr(item, "content", None) or []
                for block in content:
                    block_text = getattr(block, "text", None)
                    if block_text:
                        collected.append(block_text)
            text = "".join(collected).strip()
        usage = getattr(resp, "usage", None)
        usage_dict = (
            {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
            }
            if usage
            else {}
        )
        return {
            "ok": True,
            "text": text,
            "stop_reason": getattr(resp, "status", None),
            "usage": usage_dict,
        }
    except Exception as exc:
        return {"ok": False, "text": "", "error": f"responses parse error: {exc}"}


def _azure_complete(
    *,
    system: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """Call the Azure coding deployment. Tries Chat Completions first; on a
    400 'unsupported' error, retries via the Responses API and caches that
    preference for the deployment."""
    client = _azure_client()
    if client is None or not _AZURE_CODE_DEPLOYMENT:
        return {
            "ok": False,
            "text": "",
            "error": "Azure code deployment not configured",
        }

    # Use cached preference if we already learned which API works
    preference = _API_PREFERENCE.get(_AZURE_CODE_DEPLOYMENT)
    if preference == "responses":
        result = _azure_complete_responses(
            client,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )
        if result.get("ok"):
            return result
        logger.warning("[code_llm] Azure responses call failed: %s", result.get("error"))
        return {"ok": False, "text": "", "error": result.get("error", "")}

    # Try chat first (works for gpt-4o, gpt-5-codex configured for chat, etc.)
    result = _azure_complete_chat(
        client,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if result.get("ok"):
        _API_PREFERENCE[_AZURE_CODE_DEPLOYMENT] = "chat"
        return result

    # If chat failed with "unsupported", fall through to responses
    exc = result.get("_exc")
    if exc is not None and _is_unsupported_error(exc):
        logger.info(
            "[code_llm] %s does not support chat.completions — switching to responses API",
            _AZURE_CODE_DEPLOYMENT,
        )
        result = _azure_complete_responses(
            client,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )
        if result.get("ok"):
            _API_PREFERENCE[_AZURE_CODE_DEPLOYMENT] = "responses"
            return result
        logger.warning("[code_llm] Azure responses also failed: %s", result.get("error"))
        return {"ok": False, "text": "", "error": result.get("error", "")}

    logger.warning("[code_llm] Azure chat call failed: %s", result.get("error"))
    return {"ok": False, "text": "", "error": result.get("error", "")}


# Tool-use translation (Anthropic <-> OpenAI)
# The Project Builder agent loop is written against Anthropic's tool_use/tool_result
# shape. Azure GPT (chat.completions) uses OpenAI's tool_calls / role=tool
# shape. These helpers translate so we can fall back to Azure when Claude is
# unreachable without rewriting the agent.

def _tools_anthropic_to_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools or []
    ]


def _messages_anthropic_to_openai(
    system: str, messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Flatten Anthropic content-block messages into OpenAI chat messages.

    - text + tool_use blocks in an assistant message → one assistant message
      with `content` and `tool_calls`.
    - tool_result blocks in a user message → one `role: "tool"` message per
      result (OpenAI requires that shape).
    """
    out: List[Dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages or []:
        role = msg.get("role") or "user"
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            out.append({"role": role, "content": str(content or "")})
            continue
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "text":
                text_parts.append(block.get("text", "") or "")
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(
                            block.get("input") or {}, ensure_ascii=False
                        ),
                    },
                })
            elif btype == "tool_result":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id") or "",
                    "content": str(block.get("content") or ""),
                })
        if role == "assistant":
            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            assistant_msg["content"] = "".join(text_parts) if text_parts else None
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
        elif role == "user":
            if tool_results:
                out.extend(tool_results)
            if text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})
        else:
            out.append({"role": role, "content": "".join(text_parts)})
    return out


def _response_openai_to_anthropic(resp: Any) -> Dict[str, Any]:
    """Convert chat.completions response → Anthropic-style content_blocks."""
    try:
        choice = resp.choices[0]
        msg = choice.message
        blocks: List[Dict[str, Any]] = []
        text_content = getattr(msg, "content", None)
        if text_content:
            blocks.append({"type": "text", "text": text_content})
        for tc in getattr(msg, "tool_calls", None) or []:
            args: Dict[str, Any] = {}
            raw_args = getattr(tc.function, "arguments", "") or ""
            try:
                args = json.loads(raw_args) if raw_args else {}
            except Exception:
                args = {"_raw": raw_args}
            blocks.append({
                "type": "tool_use",
                "id": getattr(tc, "id", "") or "",
                "name": getattr(tc.function, "name", "") or "",
                "input": args if isinstance(args, dict) else {"_raw": args},
            })
        usage = getattr(resp, "usage", None)
        usage_dict = (
            {
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
            }
            if usage
            else {}
        )
        return {
            "ok": True,
            "content_blocks": blocks,
            "stop_reason": getattr(choice, "finish_reason", None),
            "usage": usage_dict,
        }
    except Exception as exc:
        return {"ok": False, "content_blocks": [], "error": f"openai parse: {exc}"}


def _tools_anthropic_to_responses(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Anthropic schemas → Responses API tool format (flat, not nested)."""
    return [
        {
            "type": "function",
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }
        for t in tools or []
    ]


def _input_anthropic_to_responses(
    system: str, messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Anthropic messages → Responses API `input` array.

    Maps:
      tool_use   → function_call (name, arguments, call_id)
      tool_result → function_call_output (call_id, output)
    System prompt is passed separately via the `instructions` field.
    """
    items: List[Dict[str, Any]] = []
    for msg in messages or []:
        role = msg.get("role") or "user"
        content = msg.get("content")
        if isinstance(content, str):
            items.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            items.append({"role": role, "content": str(content or "")})
            continue
        text_parts: List[str] = []
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "text":
                text_parts.append(block.get("text", "") or "")
            elif btype == "tool_use":
                if text_parts and role == "assistant":
                    items.append({"role": "assistant", "content": "".join(text_parts)})
                    text_parts = []
                items.append({
                    "type": "function_call",
                    "name": block.get("name") or "",
                    "arguments": json.dumps(
                        block.get("input") or {}, ensure_ascii=False
                    ),
                    "call_id": block.get("id") or "",
                })
            elif btype == "tool_result":
                if text_parts and role == "user":
                    items.append({"role": "user", "content": "".join(text_parts)})
                    text_parts = []
                items.append({
                    "type": "function_call_output",
                    "call_id": block.get("tool_use_id") or "",
                    "output": str(block.get("content") or ""),
                })
        if text_parts:
            items.append({"role": role, "content": "".join(text_parts)})
    return items


def _response_responses_to_anthropic(resp: Any) -> Dict[str, Any]:
    """Convert Responses API output → Anthropic-style content_blocks.

    Codex/reasoning models return a mix of `reasoning` items (internal), plus
    `message` items (visible text) and `function_call` items (tool calls). We
    only care about the latter two — reasoning items get summarized for the
    diagnostic if no actionable output landed.
    """
    try:
        blocks: List[Dict[str, Any]] = []
        seen_types: List[str] = []
        output = getattr(resp, "output", None) or []
        for item in output:
            itype = getattr(item, "type", None) or "(unknown)"
            seen_types.append(itype)
            if itype == "message":
                # Inner content blocks: usually `output_text`, sometimes `text`.
                for c in getattr(item, "content", None) or []:
                    ctype = getattr(c, "type", None)
                    text = getattr(c, "text", None)
                    if text and ctype in ("output_text", "text"):
                        blocks.append({"type": "text", "text": text})
                    elif ctype == "refusal":
                        blocks.append({
                            "type": "text",
                            "text": f"[refusal] {getattr(c, 'refusal', '')}",
                        })
            elif itype == "function_call":
                args: Dict[str, Any] = {}
                raw_args = getattr(item, "arguments", "") or ""
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    args = {"_raw": raw_args}
                blocks.append({
                    "type": "tool_use",
                    "id": getattr(item, "call_id", "")
                    or getattr(item, "id", "")
                    or "",
                    "name": getattr(item, "name", "") or "",
                    "input": args if isinstance(args, dict) else {"_raw": args},
                })

        usage = getattr(resp, "usage", None)
        usage_dict = (
            {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
            }
            if usage
            else {}
        )

        if not blocks:
            # Empty visible output — log the structure so we can diagnose.
            logger.warning(
                "[code_llm] responses returned 0 actionable blocks. "
                "output item types=%s, status=%s, usage=%s",
                seen_types, getattr(resp, "status", None), usage_dict,
            )

        return {
            "ok": True,
            "content_blocks": blocks,
            "stop_reason": getattr(resp, "status", None),
            "usage": usage_dict,
        }
    except Exception as exc:
        logger.warning("[code_llm] responses parse exception: %s", exc)
        return {"ok": False, "content_blocks": [], "error": f"responses parse: {exc}"}


def _azure_complete_with_tools(
    *,
    system: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """Azure tool-use call. Tries chat.completions first; on the 400
    'unsupported' error (codex/reasoning deployments) falls back to the
    Responses API. Caches the working surface per deployment."""
    client = _azure_client()
    if client is None or not _AZURE_CODE_DEPLOYMENT:
        return {
            "ok": False,
            "content_blocks": [],
            "error": "Azure code deployment not configured",
        }

    preference = _API_PREFERENCE.get(_AZURE_CODE_DEPLOYMENT)

    if preference != "responses":
        openai_tools = _tools_anthropic_to_openai(tools)
        openai_messages = _messages_anthropic_to_openai(system, messages)
        try:
            resp = client.chat.completions.create(
                model=_AZURE_CODE_DEPLOYMENT,
                messages=openai_messages,
                tools=openai_tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            _API_PREFERENCE[_AZURE_CODE_DEPLOYMENT] = "chat"
            return _response_openai_to_anthropic(resp)
        except Exception as exc:
            if not _is_unsupported_error(exc):
                logger.warning("[code_llm] Azure tool-use chat failed: %s", exc)
                return {"ok": False, "content_blocks": [], "error": str(exc)}
            logger.info(
                "[code_llm] %s tool-use: chat unsupported → switching to Responses API",
                _AZURE_CODE_DEPLOYMENT,
            )

    # Responses API path (codex / reasoning deployments)
    responses_tools = _tools_anthropic_to_responses(tools)
    responses_input = _input_anthropic_to_responses(system, messages)
    # Cap reasoning to leave room for actual function_calls. Without this,
    # gpt-5-codex burns the whole budget thinking and produces zero output.
    create_kwargs: Dict[str, Any] = {
        "model": _AZURE_CODE_DEPLOYMENT,
        "input": responses_input,
        "instructions": system or None,
        "tools": responses_tools,
        "max_output_tokens": max(max_tokens, 16000),
        "reasoning": {"effort": "low"},
    }
    try:
        resp = client.responses.create(**create_kwargs)
    except Exception as exc:
        # Some Azure deployments reject `reasoning` — retry without it.
        if "reasoning" in str(exc).lower() or "unsupported" in str(exc).lower():
            create_kwargs.pop("reasoning", None)
            try:
                resp = client.responses.create(**create_kwargs)
            except Exception as exc2:
                logger.warning("[code_llm] Azure tool-use responses retry failed: %s", exc2)
                return {"ok": False, "content_blocks": [], "error": str(exc2)}
        else:
            logger.warning("[code_llm] Azure tool-use responses failed: %s", exc)
            return {"ok": False, "content_blocks": [], "error": str(exc)}
    result = _response_responses_to_anthropic(resp)
    if result.get("ok"):
        _API_PREFERENCE[_AZURE_CODE_DEPLOYMENT] = "responses"
    return result


# Public API
def complete(
    *,
    system: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 4096,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Try Claude first, then Azure code deployment as fallback.

    Returns the same dict shape ``claude_vertex.complete`` returns:
        {ok, text, stop_reason?, usage?, error?, provider}
    """
    if claude_vertex.is_available():
        result = claude_vertex.complete(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )
        if result.get("ok"):
            result["provider"] = "claude"
            return result
        logger.warning(
            "[code_llm] Claude failed (%s) — falling back to Azure",
            result.get("error", "no detail"),
        )

    fallback = _azure_complete(
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    fallback["provider"] = "azure" if fallback.get("ok") else "none"
    return fallback


def complete_with_tools(
    *,
    system: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    max_tokens: int = 8000,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    """Tool-use call. Tries Claude first, falls back to Azure chat.completions.

    Returns the same shape as ``claude_vertex.complete_with_tools``:
        {ok, content_blocks, stop_reason?, usage?, error?, provider}
    """
    if claude_vertex.is_available():
        result = claude_vertex.complete_with_tools(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if result.get("ok"):
            result["provider"] = "claude"
            return result
        logger.warning(
            "[code_llm] Claude tool-use failed (%s) — falling back to Azure",
            result.get("error", "no detail"),
        )

    fallback = _azure_complete_with_tools(
        system=system,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    fallback["provider"] = "azure" if fallback.get("ok") else "none"
    return fallback


def complete_json(
    *,
    system: str,
    user_message: str,
    schema_hint: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> Dict[str, Any]:
    """Call the active provider expecting a JSON object response.

    Mirrors ``claude_vertex.complete_json`` — strips markdown fences, parses
    JSON, returns {ok, json, raw_text, error?, provider?}.
    """
    sys_prompt = system
    if schema_hint:
        sys_prompt = (
            f"{system}\n\nRespond ONLY with a valid JSON object matching: "
            f"{schema_hint}\nNo markdown, no commentary."
        )

    result = complete(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    raw_text = result.get("text") or ""
    if not result.get("ok"):
        return {
            "ok": False,
            "json": None,
            "raw_text": raw_text,
            "error": result.get("error", "complete failed"),
            "provider": result.get("provider", "none"),
        }

    cleaned = raw_text.strip()
    # Strip ```json ... ``` fence
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    if not cleaned:
        return {
            "ok": False,
            "json": None,
            "raw_text": raw_text,
            "error": "LLM رجع نص فاضي (غالباً reasoning tokens استهلكت كل الـ budget)",
            "provider": result.get("provider", "none"),
        }

    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            return {
                "ok": False,
                "json": None,
                "raw_text": raw_text,
                "error": "JSON root is not an object",
                "provider": result.get("provider", "claude"),
            }
        return {
            "ok": True,
            "json": parsed,
            "raw_text": raw_text,
            "provider": result.get("provider", "claude"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "json": None,
            "raw_text": raw_text,
            "error": f"json parse: {exc}",
            "provider": result.get("provider", "claude"),
        }
