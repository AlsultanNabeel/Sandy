"""Azure OpenAI image generation/editing — fallback لما Replicate Flux يفشل.

- توليد: DALL-E 3 (deployment name من AZURE_OPENAI_IMAGE_DEPLOYMENT)
- تعديل: gpt-image-1 (deployment name من AZURE_OPENAI_IMAGE_EDIT_DEPLOYMENT)

Env vars:
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_API_KEY
- AZURE_OPENAI_API_VERSION
- AZURE_OPENAI_IMAGE_DEPLOYMENT       (للتوليد، عادة "dall-e-3")
- AZURE_OPENAI_IMAGE_EDIT_DEPLOYMENT  (للتعديل، عادة "gpt-image-1")
"""

from __future__ import annotations

import base64
import io
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _client():
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview").strip()
    if not endpoint or not api_key:
        return None
    try:
        from openai import AzureOpenAI
        return AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
        )
    except Exception as e:
        logger.warning("[azure_image] AzureOpenAI client init failed: %s", e)
        return None


def _decode_image_response(response) -> Optional[bytes]:
    """Azure DALL-E/gpt-image-1 يرجع data[0].url أو data[0].b64_json."""
    try:
        item = response.data[0]
    except (AttributeError, IndexError):
        return None
    b64 = getattr(item, "b64_json", None)
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    url = getattr(item, "url", None)
    if url:
        try:
            r = requests.get(url, timeout=30)
            return r.content if r.status_code == 200 else None
        except requests.RequestException:
            return None
    return None


def generate_image_with_azure_dalle(prompt: str, *, size: str = "1024x1024") -> Optional[bytes]:
    if not prompt:
        return None
    deployment = os.getenv("AZURE_OPENAI_IMAGE_DEPLOYMENT", "").strip()
    if not deployment:
        logger.warning("[azure_image] AZURE_OPENAI_IMAGE_DEPLOYMENT missing")
        return None
    client = _client()
    if client is None:
        return None
    try:
        resp = client.images.generate(
            model=deployment,
            prompt=prompt,
            size=size,
            n=1,
        )
    except Exception as e:
        logger.warning("[azure_image] DALL-E generate failed: %s", e)
        return None
    return _decode_image_response(resp)


def edit_image_with_azure_gptimg(image_bytes: bytes, prompt: str, *, size: str = "1024x1024") -> Optional[bytes]:
    if not image_bytes or not prompt:
        return None
    # نفول back ع IMAGE_DEPLOYMENT لو ما في إديت منفصل (gpt-image-1 بيعمل التنين)
    deployment = (
        os.getenv("AZURE_OPENAI_IMAGE_EDIT_DEPLOYMENT", "").strip()
        or os.getenv("AZURE_OPENAI_IMAGE_DEPLOYMENT", "").strip()
    )
    if not deployment:
        logger.warning("[azure_image] AZURE_OPENAI_IMAGE_DEPLOYMENT missing")
        return None
    client = _client()
    if client is None:
        return None
    try:
        buf = io.BytesIO(image_bytes)
        buf.name = "input.png"
        resp = client.images.edit(
            model=deployment,
            image=buf,
            prompt=prompt,
            size=size,
            n=1,
        )
    except Exception as e:
        logger.warning("[azure_image] gpt-image-1 edit failed: %s", e)
        return None
    return _decode_image_response(resp)
