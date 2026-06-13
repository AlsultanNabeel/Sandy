"""Azure OpenAI adapter for intent routing (FC mode) — يحلّ محل GeminiFlashClient.

يستخدم Azure GPT-4o-mini مع JSON mode، بنفس الواجهة العامة للـ GeminiFlashClient
ليكون الاستبدال drop-in في maestro.py و gift_tools.py.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

DEFAULT_AZURE_INTENT_DEPLOYMENT = os.getenv(
    "AZURE_OPENAI_CHAT_DEPLOYMENT", "sandy-chat"
)

# Per-request timeout (seconds) so a hung intent/router call fails fast into the
# existing fallbacks instead of blocking the whole turn. Upper bound for hangs,
# not a latency target — intent calls normally return in <2s.
AZURE_INTENT_TIMEOUT_S = float(os.getenv("AZURE_INTENT_TIMEOUT_S", "12"))

# Singleton client — يُبنى مرة واحدة عند أول استدعاء ويُعاد استخدامه.
# منع memory leak من إنشاء AzureOpenAI client + httpx pool كل رسالة.
_CACHED_AZURE_CLIENT: Any = None
_CACHED_CLIENT_KEY: tuple = ()


def _get_azure_client(api_key: str, api_version: str, endpoint: str) -> Any:
    """يعيد client مُخزّن أو يبنيه أول مرة.

    لو مفاتيح Langfuse متوفّرة → نستورد من `langfuse.openai` بحيث الـ SDK
    يعمل auto-instrumentation لكل chat.completions.create. هاي الطريقة
    الرسمية من Langfuse وأكثر مرونة عبر نسخ الـ SDK.
    """
    global _CACHED_AZURE_CLIENT, _CACHED_CLIENT_KEY
    key = (api_key, api_version, endpoint)
    if _CACHED_AZURE_CLIENT is not None and _CACHED_CLIENT_KEY == key:
        return _CACHED_AZURE_CLIENT

    AzureOpenAICls = None
    if os.getenv("LANGFUSE_PUBLIC_KEY", "").strip():
        try:
            from langfuse.openai import AzureOpenAI as LangfuseAzureOpenAI  # type: ignore
            AzureOpenAICls = LangfuseAzureOpenAI
            print("[Azure] using langfuse.openai for auto-tracing", flush=True)
        except ImportError:
            pass

    if AzureOpenAICls is None:
        from openai import AzureOpenAI
        AzureOpenAICls = AzureOpenAI

    _CACHED_AZURE_CLIENT = AzureOpenAICls(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint,
    )
    _CACHED_CLIENT_KEY = key
    return _CACHED_AZURE_CLIENT


class AzureIntentClient:
    """Azure OpenAI adapter لتحليل النية وعمليات JSON-mode الأخرى.

    واجهة متوافقة مع GeminiFlashClient القديمة:
    - ``_generate_with_gemini(prompt, response_mime_type, system_instruction, ...)``
        احتُفظ بالاسم رغم أنه Azure الآن — لتقليل تغييرات الـ call sites.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        model: Any = None,
    ):
        self.api_key = (
            api_key if api_key is not None else os.getenv("AZURE_OPENAI_API_KEY", "")
        ).strip()
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()
        self.model_name = (
            model_name if model_name is not None else DEFAULT_AZURE_INTENT_DEPLOYMENT
        ).strip()
        self._model = model  # for test mocks

    def generate_text(self, prompt: str, **kwargs) -> str:
        """Public one-shot text generation. New code should call this instead of
        the legacy ``_generate_with_gemini`` name (kept for backward compat)."""
        return self._generate_with_gemini(prompt, **kwargs)

    def _generate_with_gemini(
        self,
        prompt: str,
        *,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        response_schema: dict | None = None,
        langfuse_name: str | None = None,
        langfuse_metadata: dict | None = None,
        langfuse_user_id: str | None = None,
        langfuse_session_id: str | None = None,
        langfuse_tags: list | None = None,
    ) -> str:
        """يولّد ردّ من Azure OpenAI — اسم الميثود مُحتفظ به للتوافق الخلفي.

        - ``response_mime_type='application/json'`` → JSON mode عبر response_format
        - ``system_instruction`` → system role message
        - ``response_schema`` يُتجاهل (gpt-4o-mini يستخدم json_object العام)
        """
        if self._model:
            # test mock — يحاكي السلوك القديم
            response = self._model.generate_content(prompt)
            text = getattr(response, "text", None)
            return str(text).strip() if text else ""

        if not (self.api_key and self.endpoint):
            raise RuntimeError(
                "Azure OpenAI not configured: AZURE_OPENAI_API_KEY/ENDPOINT missing"
            )

        try:
            client = _get_azure_client(self.api_key, self.api_version, self.endpoint)
        except ImportError as exc:
            raise RuntimeError(
                "openai package required: pip install openai"
            ) from exc

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else 0,
            "max_tokens": max_output_tokens or 600,
            # Fail fast on a hung upstream — openai SDK supports per-request timeout.
            "timeout": AZURE_INTENT_TIMEOUT_S,
        }
        if (response_mime_type or "application/json") == "application/json":
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        if os.getenv("LANGFUSE_PUBLIC_KEY", "").strip() and (
            langfuse_name or langfuse_metadata or langfuse_user_id
            or langfuse_session_id or langfuse_tags
        ):
            try:
                from langfuse import get_client  # type: ignore
                lf = get_client()
                upd = getattr(lf, "update_current_generation", None)
                if callable(upd):
                    enrich: Dict[str, Any] = {}
                    if langfuse_name:
                        enrich["name"] = langfuse_name
                    meta = dict(langfuse_metadata or {})
                    if langfuse_user_id:
                        meta["user_id"] = langfuse_user_id
                    if langfuse_session_id:
                        meta["session_id"] = langfuse_session_id
                    if langfuse_tags:
                        meta["tags"] = langfuse_tags
                    if meta:
                        enrich["metadata"] = meta
                    if enrich:
                        upd(**enrich)
            except Exception:
                pass  # never break LLM flow on trace enrichment failure

        # log الـ usage بدون نص الرسالة أو الرد — للمراقبة فقط (يبقى للـ Heroku logs)
        usage = getattr(response, "usage", None)
        if usage:
            in_tok = getattr(usage, "prompt_tokens", 0)
            out_tok = getattr(usage, "completion_tokens", 0)
            # R3: Azure بيعمل prompt caching تلقائياً للـ prompts ≥1024 token.
            # الـ cached tokens تتحاسب بنص السعر. نقرأها من prompt_tokens_details.
            cached = 0
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            non_cached_in = max(in_tok - cached, 0)
            # Rates default to gpt-4o-mini ($/1M); override via env if the
            # deployment points at another model so the logged estimate stays right.
            rate_in = float(os.getenv("AZURE_COST_IN_PER_1M", "0.15"))
            rate_cached = float(os.getenv("AZURE_COST_CACHED_PER_1M", "0.075"))
            rate_out = float(os.getenv("AZURE_COST_OUT_PER_1M", "0.60"))
            cost = (
                non_cached_in * rate_in + cached * rate_cached + out_tok * rate_out
            ) / 1_000_000
            if cached:
                pct = (cached / in_tok * 100) if in_tok else 0
                print(
                    f"[Azure] in={in_tok} (cached={cached} {pct:.0f}%) "
                    f"out={out_tok} ~${cost:.5f}",
                    flush=True,
                )
            else:
                print(f"[Azure] in={in_tok} out={out_tok} ~${cost:.5f}", flush=True)

        choice = response.choices[0] if response.choices else None
        if not choice:
            return ""
        content = getattr(choice.message, "content", None)
        return str(content).strip() if content else ""
