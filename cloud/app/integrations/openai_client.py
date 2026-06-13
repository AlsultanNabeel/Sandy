"""OpenAI / Azure OpenAI chat completion client with circuit breaker."""

import os
import time
from typing import Any, Callable, Dict, List, Optional

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.utils import metrics as metrics

_cb = CircuitBreaker(name="openai", failure_threshold=5, recovery_timeout=60.0)

# Default per-request timeout (seconds) when a caller doesn't pass one — so a
# hung upstream fails fast into the existing fallbacks instead of blocking the
# whole turn. Upper bound for hangs, not a latency target. An explicit `timeout=`
# from the caller always wins. openai SDK supports per-request `timeout=`.
DEFAULT_CHAT_TIMEOUT_S = float(os.getenv("OPENAI_CHAT_TIMEOUT_S", "15"))


def _chat_client_and_model(
    openai_client: Any,
    azure_openai_client: Optional[Any] = None,
    openai_model: Optional[str] = None,
    azure_chat_deployment: Optional[str] = None,
    prefer_azure: bool = True,
    model_hint: Optional[str] = None,
) -> tuple:
    """Select chat client/model with Azure-first strategy when configured."""
    if prefer_azure and azure_openai_client is not None:
        model_name = model_hint or azure_chat_deployment or openai_model
        return azure_openai_client, model_name

    model_name = model_hint or openai_model
    return openai_client, model_name


def create_chat_completion(
    messages: List[Dict[str, Any]],
    openai_client: Any,
    azure_openai_client: Optional[Any] = None,
    openai_model: Optional[str] = None,
    azure_chat_deployment: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 500,
    response_format: Optional[Dict[str, Any]] = None,
    prefer_azure: bool = True,
    model_hint: Optional[str] = None,
    timeout: Optional[float] = None,
    stream: bool = False,
) -> Any:
    """Unified chat completion with Azure-first routing and circuit breaker."""
    client, model_name = _chat_client_and_model(
        openai_client=openai_client,
        azure_openai_client=azure_openai_client,
        openai_model=openai_model,
        azure_chat_deployment=azure_chat_deployment,
        prefer_azure=prefer_azure,
        model_hint=model_hint,
    )

    kwargs: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    # Explicit caller timeout wins; otherwise apply the hang ceiling.
    kwargs["timeout"] = timeout if timeout is not None else DEFAULT_CHAT_TIMEOUT_S
    if stream:
        kwargs["stream"] = True

    try:
        started = time.perf_counter()
        success = False
        if stream:
            # Return stream directly — circuit breaker wraps only the initial call
            result = _cb.call(client.chat.completions.create, **kwargs)
            success = True
            return result
        result = _cb.call(client.chat.completions.create, **kwargs)
        success = True
        return result
    except CircuitOpenError:
        raise RuntimeError("[OpenAI] Circuit OPEN — AI service temporarily unavailable")
    finally:
        try:
            # On the stream path `started→here` is just the time to OPEN the
            # stream, not the full completion — recording it would skew the
            # latency metric, so only observe duration for non-stream calls.
            if not stream:
                metrics.observe_llm_completion(time.perf_counter() - started)
            if success:
                metrics.inc_llm_completion_success()
            else:
                metrics.inc_llm_completion_failure()
        except Exception:
            pass


def make_chat_completion_fn(
    openai_client: Any,
    azure_openai_client: Optional[Any] = None,
    openai_model: Optional[str] = None,
    azure_chat_deployment: Optional[str] = None,
) -> Callable[..., Any]:
    """Return a bound create_chat_completion function with pre-configured clients."""

    def _bound(
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 500,
        response_format: Optional[Dict[str, Any]] = None,
        prefer_azure: bool = True,
        model_hint: Optional[str] = None,
        timeout: Optional[float] = None,
        stream: bool = False,
    ) -> Any:
        return create_chat_completion(
            messages=messages,
            openai_client=openai_client,
            azure_openai_client=azure_openai_client,
            openai_model=openai_model,
            azure_chat_deployment=azure_chat_deployment,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            prefer_azure=prefer_azure,
            model_hint=model_hint,
            timeout=timeout,
            stream=stream,
        )

    return _bound
