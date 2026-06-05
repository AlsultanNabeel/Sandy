"""Claude Sonnet — primary code-generation LLM used by Project Builder.

Supports two providers, auto-detected from env:
- **Amazon Bedrock** (preferred when AWS creds present) — easier auth on Heroku
- **Google Vertex AI** (fallback when GCP creds present) — original integration

If no provider is configured, callers don't crash; they decide whether
to ship the change or not. (Sandy then falls back to Gemini Flash for
code generation.)

Note: the filename is `claude_vertex.py` for historical reasons, but it
supports both providers now. Renaming would touch import sites, so it's
deferred for later.

Env vars (Bedrock — preferred):
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY  — picked up by boto3 transparently
    AWS_REGION                                 — default 'us-east-1'
    CLAUDE_BEDROCK_MODEL                       — full inference profile ID
        default: 'us.anthropic.claude-sonnet-4-6'

Env vars (Vertex — fallback):
    GOOGLE_CLOUD_PROJECT       — GCP project id (or VERTEX_PROJECT_ID)
    VERTEX_REGION              — Vertex region, default 'us-east5' (Claude region)
    GOOGLE_APPLICATION_CREDENTIALS — service account JSON path
    CLAUDE_VERTEX_MODEL        — default 'claude-sonnet-4-5@20250929'
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Bedrock config
_BEDROCK_MODEL = os.getenv(
    "CLAUDE_BEDROCK_MODEL",
    "us.anthropic.claude-sonnet-4-6",
)
_BEDROCK_REGION = (
    os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
)

# Vertex config
_VERTEX_MODEL = os.getenv("CLAUDE_VERTEX_MODEL", "claude-sonnet-4-5@20250929")
_VERTEX_REGION = os.getenv("VERTEX_REGION", "us-east5")

_DEFAULT_MAX_TOKENS = 4096

# Per-request timeout (seconds) so a hung upstream fails fast instead of blocking.
# Generous because code-gen replies are long; this is a hang ceiling, not a
# latency target. The anthropic SDK supports per-request `timeout=`.
_CLAUDE_TIMEOUT_S = float(os.getenv("CLAUDE_TIMEOUT_S", "30"))

_cb = CircuitBreaker(
    name="claude_vertex",
    failure_threshold=3,
    recovery_timeout=120.0,
)

# Lazy singleton — first call creates client. _provider tells us which API to
# use (the SDK objects have the same `.messages.create` shape but the model id
# is provider-specific).
_client: Optional[Any] = None
_provider: str = ""  # 'bedrock' | 'vertex' | ''
_client_init_failed = False


def _get_project_id() -> str:
    return (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("GCLOUD_PROJECT")
        or ""
    ).strip()


def _detect_provider() -> str:
    """Pick Bedrock if AWS creds present; else Vertex if GCP project set."""
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        return "bedrock"
    if _get_project_id():
        return "vertex"
    return ""


def _active_model() -> str:
    return _BEDROCK_MODEL if _provider == "bedrock" else _VERTEX_MODEL


def _get_client():
    """Lazy-init Anthropic client. Returns None if no provider is configured."""
    global _client, _provider, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed:
        return None

    chosen = _detect_provider()
    if not chosen:
        logger.warning(
            "[claude] no provider configured — "
            "set AWS_ACCESS_KEY_ID/SECRET (Bedrock) or GOOGLE_CLOUD_PROJECT (Vertex)"
        )
        _client_init_failed = True
        return None

    # Try langfuse.anthropic auto-instrumentation when Langfuse is configured.
    # هاد بيخلي كل call Claude يطلع تلقائياً في Langfuse بدون wrapping يدوي.
    use_langfuse = bool(os.getenv("LANGFUSE_PUBLIC_KEY", "").strip())

    try:
        if chosen == "bedrock":
            BedrockCls = None
            if use_langfuse:
                try:
                    from langfuse.anthropic import AnthropicBedrock as _LFBedrock  # type: ignore
                    BedrockCls = _LFBedrock
                    logger.info("[claude] using langfuse.anthropic for auto-tracing")
                except ImportError:
                    pass
            if BedrockCls is None:
                from anthropic import AnthropicBedrock as _Bedrock  # type: ignore
                BedrockCls = _Bedrock
            # boto3 inside anthropic picks AWS_ACCESS_KEY_ID / SECRET from env
            _client = BedrockCls(aws_region=_BEDROCK_REGION)
            _provider = "bedrock"
            logger.info(
                "[claude] initialized — provider=bedrock region=%s model=%s",
                _BEDROCK_REGION, _BEDROCK_MODEL,
            )
        else:  # vertex
            VertexCls = None
            if use_langfuse:
                try:
                    from langfuse.anthropic import AnthropicVertex as _LFVertex  # type: ignore
                    VertexCls = _LFVertex
                    logger.info("[claude] using langfuse.anthropic for auto-tracing")
                except ImportError:
                    pass
            if VertexCls is None:
                from anthropic import AnthropicVertex as _Vertex  # type: ignore
                VertexCls = _Vertex
            project = _get_project_id()
            _client = VertexCls(region=_VERTEX_REGION, project_id=project)
            _provider = "vertex"
            logger.info(
                "[claude] initialized — provider=vertex project=%s region=%s model=%s",
                project, _VERTEX_REGION, _VERTEX_MODEL,
            )
        return _client
    except ImportError as exc:
        logger.warning(
            "[claude] anthropic %s import failed: %s — install 'anthropic[%s]'",
            chosen, exc, chosen,
        )
        _client_init_failed = True
        return None
    except Exception as exc:
        logger.warning("[claude] init failed (provider=%s): %s", chosen, exc)
        _client_init_failed = True
        return None


def is_available() -> bool:
    return _get_client() is not None


def complete(
    *,
    system: str,
    messages: List[Dict[str, str]],
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Call Claude Sonnet via the active provider (Bedrock or Vertex).

    Returns: {ok, text, stop_reason, usage, error?}
    Never raises.
    """
    client = _get_client()
    if client is None:
        return {
            "ok": False,
            "text": "",
            "error": "Claude غير متاح (لا Bedrock ولا Vertex configured)",
        }

    use_model = model or _active_model()

    # R3: Prompt Caching — لما الـ flag مفعّل، نحط cache_control على الـ system
    # prompt (عادة الأكبر والثابت). Claude بيخزّنه ويعيد استخدامه بخصم ~90%
    # على القراءة. نحوّل system من str لـ list of content blocks مع cache_control.
    system_param: Any = system
    try:
        from app.utils.feature_flags import use_prompt_caching
        if use_prompt_caching() and system and isinstance(system, str):
            system_param = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
    except Exception:
        system_param = system

    def _call_once() -> Any:
        return client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_param,
            messages=messages,
            timeout=_CLAUDE_TIMEOUT_S,
        )

    try:
        resp = _cb.call(_call_once)
    except CircuitOpenError:
        return {"ok": False, "text": "", "error": "Claude circuit open"}
    except Exception as exc:
        logger.warning("[claude_vertex] call failed: %s", exc)
        return {"ok": False, "text": "", "error": str(exc)}

    # Aggregate text blocks
    try:
        text_parts: List[str] = []
        for block in getattr(resp, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", "") or "")
        text = "".join(text_parts).strip()
        usage = getattr(resp, "usage", None)
        usage_dict = (
            {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                # R3: prompt caching stats — كم كُتب للـ cache + كم قُرئ منه
                "cache_creation_input_tokens": getattr(
                    usage, "cache_creation_input_tokens", 0
                ),
                "cache_read_input_tokens": getattr(
                    usage, "cache_read_input_tokens", 0
                ),
            }
            if usage
            else {}
        )
        cache_read = usage_dict.get("cache_read_input_tokens", 0)
        if cache_read:
            logger.info(
                "[claude] cache hit: read=%s created=%s in=%s out=%s",
                cache_read,
                usage_dict.get("cache_creation_input_tokens", 0),
                usage_dict.get("input_tokens", 0),
                usage_dict.get("output_tokens", 0),
            )
        return {
            "ok": True,
            "text": text,
            "stop_reason": getattr(resp, "stop_reason", None),
            "usage": usage_dict,
        }
    except Exception as exc:
        logger.warning("[claude_vertex] response parse failed: %s", exc)
        return {"ok": False, "text": "", "error": f"parse error: {exc}"}


def complete_with_tools(
    *,
    system: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Claude with tool use enabled — returns full content blocks (text + tool_use).

    Returns:
        {ok, content_blocks: List[Dict], stop_reason, usage, error?}

    `content_blocks` is the raw list from Anthropic's API in dict form (each
    item has `type`, plus type-specific fields). The caller is expected to
    iterate it, execute any `tool_use`, and append both the assistant message
    and a user `tool_result` message before calling again.
    """
    client = _get_client()
    if client is None:
        return {
            "ok": False,
            "content_blocks": [],
            "error": "Claude غير متاح (لا Bedrock ولا Vertex configured)",
        }

    use_model = model or _active_model()

    def _call_once() -> Any:
        return client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
            tools=tools,
            timeout=_CLAUDE_TIMEOUT_S,
        )

    try:
        resp = _cb.call(_call_once)
    except CircuitOpenError:
        return {"ok": False, "content_blocks": [], "error": "Claude circuit open"}
    except Exception as exc:
        logger.warning("[claude_vertex] tool-use call failed: %s", exc)
        return {"ok": False, "content_blocks": [], "error": str(exc)}

    try:
        blocks: List[Dict[str, Any]] = []
        for block in getattr(resp, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                blocks.append({"type": "text", "text": getattr(block, "text", "")})
            elif block_type == "tool_use":
                blocks.append({
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": dict(getattr(block, "input", {}) or {}),
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
        return {
            "ok": True,
            "content_blocks": blocks,
            "stop_reason": getattr(resp, "stop_reason", None),
            "usage": usage_dict,
        }
    except Exception as exc:
        logger.warning("[claude_vertex] tool-use parse failed: %s", exc)
        return {
            "ok": False,
            "content_blocks": [],
            "error": f"parse error: {exc}",
        }
