"""Azure FLUX.2-pro adapter — توليد وتعديل الصور عبر Azure AI Services.

Endpoint: https://<resource>.services.ai.azure.com/providers/blackforestlabs/v1/flux-2-pro
يعتمد على AZURE_API_KEY و AZURE_FLUX_ENDPOINT.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _azure_config():
    """جمع إعدادات Azure من البيئة."""
    return {
        "endpoint": os.getenv("AZURE_FLUX_ENDPOINT", "https://sandy-ai-azure.services.ai.azure.com").rstrip("/"),
        "api_key": os.getenv("AZURE_OPENAI_API_KEY", "").strip(),  # reuse OpenAI key
        "deployment": os.getenv("AZURE_FLUX_DEPLOYMENT", "sandy-flux").strip(),
        "api_version": "preview",
    }


def generate_image_azure(
    prompt: str,
    *,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "vivid",
    timeout: float = 60.0,
) -> Optional[bytes]:
    """توليد صورة عبر Azure FLUX.2-pro (text-to-image).

    Args:
        prompt: وصف الصورة
        size: حجم الصورة (e.g., "1024x1024")
        quality: جودة (standard/hd) — قد لا يدعمها FLUX
        style: أسلوب (vivid/natural) — قد لا يدعمها FLUX
        timeout: مهلة الانتظار

    Returns:
        bytes الصورة أو None إذا فشلت
    """
    config = _azure_config()
    if not config["api_key"]:
        logger.error("[azure_flux] Missing AZURE_OPENAI_API_KEY")
        return None

    # parse size
    try:
        w, h = map(int, size.split("x"))
    except (ValueError, AttributeError):
        w, h = 1024, 1024

    url = f"{config['endpoint']}/providers/blackforestlabs/v1/flux-2-pro?api-version={config['api_version']}"

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    payload = {
        "prompt": prompt,
        "width": w,
        "height": h,
        "n": 1,
        "model": config["deployment"],
    }

    try:
        t0 = time.perf_counter()
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            logger.error(f"[azure_flux] HTTP {resp.status_code}: {resp.text[:300]}")
            return None

        data = resp.json()
        image_data = data.get("data", [{}])[0]
        b64_image = image_data.get("b64_json", "")

        if not b64_image:
            logger.error("[azure_flux] No b64_json in response")
            return None

        image_bytes = base64.b64decode(b64_image)
        logger.info(f"[azure_flux] Generated {len(image_bytes)} bytes in {elapsed_ms:.0f}ms")
        return image_bytes

    except requests.RequestException as exc:
        logger.error(f"[azure_flux] Request failed: {exc}")
        return None
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error(f"[azure_flux] Parse error: {exc}")
        return None


def edit_image_azure(
    prompt: str,
    original_image: bytes,
    *,
    mask: Optional[bytes] = None,
    size: str = "1024x1024",
    timeout: float = 60.0,
) -> Optional[bytes]:
    """تعديل صورة عبر Azure FLUX.2-pro (image-to-image).

    Args:
        prompt: وصف التعديل
        original_image: الصورة الأصلية (bytes)
        mask: قناع (اختياري)
        size: حجم الصورة
        timeout: مهلة الانتظار

    Returns:
        bytes الصورة المعدلة أو None إذا فشلت
    """
    config = _azure_config()
    if not config["api_key"]:
        logger.error("[azure_flux] Missing AZURE_OPENAI_API_KEY")
        return None

    # parse size
    try:
        w, h = map(int, size.split("x"))
    except (ValueError, AttributeError):
        w, h = 1024, 1024

    url = f"{config['endpoint']}/providers/blackforestlabs/v1/flux-2-pro?api-version={config['api_version']}"

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    b64_image = base64.b64encode(original_image).decode("utf-8")

    payload = {
        "prompt": prompt,
        "image": b64_image,
        "width": w,
        "height": h,
        "n": 1,
        "model": config["deployment"],
    }

    if mask:
        b64_mask = base64.b64encode(mask).decode("utf-8")
        payload["mask"] = b64_mask

    try:
        t0 = time.perf_counter()
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            logger.error(f"[azure_flux] HTTP {resp.status_code}: {resp.text[:300]}")
            return None

        data = resp.json()
        image_data = data.get("data", [{}])[0]
        b64_image_out = image_data.get("b64_json", "")

        if not b64_image_out:
            logger.error("[azure_flux] No b64_json in response")
            return None

        image_bytes = base64.b64decode(b64_image_out)
        logger.info(f"[azure_flux] Edited image: {len(image_bytes)} bytes in {elapsed_ms:.0f}ms")
        return image_bytes

    except requests.RequestException as exc:
        logger.error(f"[azure_flux] Request failed: {exc}")
        return None
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error(f"[azure_flux] Parse error: {exc}")
        return None


