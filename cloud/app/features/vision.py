import base64
from typing import Any, Callable, Optional


_VISION_CONTEXT = (
    "إنت الآن بتشوفي صورة من كاميرتك. علّقي عليها بشخصيتك الطبيعية — "
    "بإيجاز، بعفوية، باستخدام تعابيرك وضحكاتك وإيموجي وكل اللي بتسوّيه عادة في الحوار. "
    "لا تكتبي وصف بصري جاف ولا قائمة بنود؛ احكي زي صاحبة بتشوف وبتعلّق."
)


def _build_sandy_vision_system() -> str:
    """شخصية ساندي الموحدة (SANDY_PERSONALITY env) + سياق إنها بتشوف الصورة."""
    try:
        from app.config import SANDY_PERSONALITY
        persona = (SANDY_PERSONALITY or "").strip()
    except Exception:
        persona = ""
    if persona:
        return f"{persona}\n\n{_VISION_CONTEXT}"
    return _VISION_CONTEXT


def analyze_image_with_azure(
    image_bytes: bytes,
    prompt: str,
    *,
    create_chat_completion_fn: Callable[..., Any],
    azure_openai_vision_deployment: Optional[str] = None,
    azure_openai_chat_deployment: Optional[str] = None,
    openai_model: Optional[str] = None,
) -> str:
    """Analyze image bytes via Azure GPT-4o-mini Vision, in Sandy's voice."""
    if not image_bytes:
        return "[think] ما قدرت أحلل الصورة حالياً."

    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{image_b64}"
        model_hint = (
            azure_openai_vision_deployment
            or azure_openai_chat_deployment
            or openai_model
        )

        response = create_chat_completion_fn(
            messages=[
                {"role": "system", "content": _build_sandy_vision_system()},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0.95,
            max_tokens=120,
            prefer_azure=True,
            model_hint=model_hint,
        )
        return (
            response.choices[0].message.content
            or "[think] تم التحليل لكن ما في وصف واضح."
        ).strip()
    except Exception as e:
        print(f"[Azure Vision] analysis failed: {e}")
        return "[think] صار خلل أثناء تحليل الصورة. جرب مرة ثانية."


def generate_image_with_azure(
    prompt: str,
    *,
    azure_openai_image_deployment: Optional[str] = None,
    size: str = "1024x1024",
    **kwargs,
) -> Optional[bytes]:
    """Generate an image. Azure FLUX.2-pro first, Azure DALL-E only as fallback."""
    if not prompt:
        return None

    from app.integrations.azure_flux import generate_image_azure
    img = generate_image_azure(prompt, size=size)
    if img is not None:
        return img

    print("[Image] Azure FLUX generation failed, falling back to Azure DALL-E")
    from app.integrations.azure_image import generate_image_with_azure_dalle
    img = generate_image_with_azure_dalle(prompt, size=size)
    if img is None:
        print("[Image] all image generation methods failed")
    return img


def edit_image_with_azure(
    image_bytes: bytes,
    prompt: str,
    *,
    azure_openai_image_deployment: Optional[str] = None,
    size: str = "1024x1024",
) -> Optional[bytes]:
    """Edit an image. Azure FLUX.2-pro (image-to-image), Azure DALL-E fallback."""
    if not image_bytes or not prompt:
        return None

    from app.integrations.azure_flux import edit_image_azure
    edited = edit_image_azure(prompt, image_bytes, size=size)
    if edited is not None:
        return edited

    print("[Image Edit] Azure FLUX edit failed, falling back to Azure DALL-E")
    from app.integrations.azure_image import edit_image_with_azure_gptimg
    edited = edit_image_with_azure_gptimg(image_bytes, prompt, size=size)
    if edited is None:
        print("[Image Edit] all image edit methods failed")
    return edited
