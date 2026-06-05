"""ToolDispatcher — ينفذ tool calls من Gemini.

يأخذ اسم الـ tool والـ args، يبحث في الـ registry،
ويستدعي الـ Python handler أو MCPHub.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.agent.tool_health import record_call as _record_health
from app.agent.tools.registry import ToolRegistry, get_registry

logger = logging.getLogger(__name__)


@dataclass
class DispatchContext:
    """السياق اللازم لكل tool handler."""
    user_message: str
    normalized_message: str
    session: Dict[str, Any]
    state: Any = None                          # SandyState — تجنب circular import
    mongo_db: Any = None
    create_chat_completion_fn: Optional[Callable] = None


class ToolDispatcher:
    """ينفذ tool بالاسم — Python handler أو MCP server."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        mcp_hub: Any = None,
    ) -> None:
        self.registry = registry or get_registry()
        if mcp_hub is None:
            try:
                from app.integrations.mcp_client import get_mcp_hub
                mcp_hub = get_mcp_hub()
            except Exception:
                pass
        self.mcp_hub = mcp_hub

    def dispatch(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: DispatchContext,
    ) -> Dict[str, Any]:
        """نفّذ tool وارجع {handled, reply, ...}.

        كل استدعاء (نجح أو فشل) يتسجّل في tool_health حتى نعرف الأداة
        المتذبذبة بدون ما نرجع نقرأ اللوقات.
        وكل dispatch يفتح Langfuse span (no-op لو Langfuse مش مفعّل) فيه
        اسم الأداة + args + result + latency.
        """
        tool = self.registry.get_tool(tool_name)
        if not tool:
            logger.warning(f"[Dispatcher] unknown tool: {tool_name}")
            return {"handled": False, "reply": f"أداة غير معروفة: {tool_name}"}

        try:
            from app.integrations.langfuse_client import trace_span as _trace_span
        except ImportError:
            _trace_span = None  # type: ignore

        start = time.perf_counter()
        result: Dict[str, Any]
        error_for_health: Optional[str] = None

        span_ctx = (
            _trace_span(
                name=f"tool:{tool_name}",
                input_data=args or {},
                metadata={
                    "tool_type": tool.tool_type,
                    "mcp_server": getattr(tool, "mcp_server", None),
                },
            )
            if _trace_span is not None
            else _noop_ctx()
        )

        with span_ctx as span:
            try:
                logger.info(f"[Tool] {tool_name} | type={tool.tool_type} | args={list((args or {}).keys())}")
                if tool.tool_type == "python":
                    if not tool.handler:
                        result = {"handled": False, "reply": f"handler غير مسجل: {tool_name}"}
                        error_for_health = "handler missing"
                    else:
                        result = tool.handler(args or {}, context)
                elif tool.tool_type == "mcp":
                    if not self.mcp_hub:
                        logger.warning(f"[Dispatcher] MCP not ready: {tool_name}")
                        result = {"handled": False, "reply": "MCP غير متاح حالياً."}
                        error_for_health = "mcp hub down"
                    else:
                        result = self.mcp_hub.call(
                            tool.mcp_server, tool.mcp_method, args or {}
                        )
                else:
                    result = {"handled": False, "reply": "نوع أداة غير معروف."}
                    error_for_health = f"unknown tool_type={tool.tool_type}"
            except Exception as exc:
                logger.error(f"[Dispatcher] {tool_name} failed: {exc}")
                try:
                    from app.integrations.sentry_config import capture_exception
                    capture_exception(exc, context={"tool": tool_name})
                except Exception:
                    pass
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                try:
                    _record_health(
                        tool_name, ok=False, latency_ms=elapsed_ms, error=str(exc),
                    )
                except Exception:
                    pass
                _safe_span_update(span, output={"error": str(exc)}, level="ERROR")
                return {"handled": False, "reply": f"خطأ في تنفيذ {tool_name}."}

            # handled=True يعتبر نجاح
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            ok = bool(result.get("handled")) and error_for_health is None
            try:
                _record_health(
                    tool_name,
                    ok=ok,
                    latency_ms=elapsed_ms,
                    error=error_for_health or (None if ok else "handled=False"),
                )
            except Exception:
                pass
            _safe_span_update(
                span,
                output={
                    "handled": result.get("handled"),
                    "reply_len": len(str(result.get("reply", ""))),
                    "keys": sorted(k for k in result.keys() if k != "reply"),
                },
                level="DEFAULT" if ok else "WARNING",
            )
            return result


import contextlib as _contextlib  # noqa: E402


@_contextlib.contextmanager
def _noop_ctx():
    yield None


def _safe_span_update(span: Any, **kwargs: Any) -> None:
    """يحدّث الـ span لو بيدعم، وما يرمي استثناء أبداً."""
    if span is None:
        return
    upd = getattr(span, "update", None)
    if not callable(upd):
        return
    try:
        upd(**kwargs)
    except Exception:
        pass
