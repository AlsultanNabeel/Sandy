"""Gemini TTS — primary voice synthesis for Sandy.

Model:  gemini-3.1-flash-tts (Preview)
Voice:  Aoede (Female)
Language: Arabic (World)
Tone:   controlled via style instruction built from current mood
Output: LINEAR16 PCM @ 22050 Hz, wrapped in WAV
"""

import base64
import os
import wave
from io import BytesIO
from typing import Optional

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
import logging

logger = logging.getLogger(__name__)

_cb = CircuitBreaker(name="gemini_tts", failure_threshold=3, recovery_timeout=120.0)

_GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts")
_GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Aoede")

# Request timeout (ms) so a hung synthesis fails fast instead of blocking voice.
_GEMINI_TTS_TIMEOUT_MS = int(os.getenv("GEMINI_TTS_TIMEOUT_MS", "8000"))

# Module-level cached client (lazy singleton) — building genai.Client per call
# redoes TLS/handshake setup. Cached on first use and reused, keyed by api_key.
_genai_client = None
_genai_client_key: Optional[str] = None


def _get_genai_client(api_key: str):
    """Return a cached genai.Client, creating it on first call (or key change)."""
    global _genai_client, _genai_client_key
    if _genai_client is None or _genai_client_key != api_key:
        from google import genai

        _genai_client = genai.Client(api_key=api_key)
        _genai_client_key = api_key
        logger.info("[Gemini TTS] client initialised")
    return _genai_client

_SAMPLE_RATE = 22050
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # 16-bit / LINEAR16

# نبرة افتراضية محايدة عامة لكل المودات (override عبر SANDY_TTS_STYLE_BASE).
# المالك يضبط الذوق المطلوب من Heroku — الكود يبقى عاماً.
_DEFAULT_BASE = os.getenv("SANDY_TTS_STYLE_BASE", "Speak natural Palestinian Arabic dialect. ")

# يمكن تخصيص كل مود عبر env var: SANDY_TTS_STYLE_<MOOD>
# الـ defaults قصيرة وحيادية — الـ env override يعطيك المرونة الكاملة.
_MOOD_INSTRUCTIONS: dict = {
    "neutral": os.getenv("SANDY_TTS_STYLE_NEUTRAL") or (_DEFAULT_BASE + "Neutral mood."),
    "calm": os.getenv("SANDY_TTS_STYLE_CALM") or (_DEFAULT_BASE + "Calm mood."),
    "happy": os.getenv("SANDY_TTS_STYLE_HAPPY") or (_DEFAULT_BASE + "Happy mood."),
    "playful": os.getenv("SANDY_TTS_STYLE_PLAYFUL") or (_DEFAULT_BASE + "Playful mood."),
    "excited": os.getenv("SANDY_TTS_STYLE_EXCITED") or (_DEFAULT_BASE + "Excited mood."),
    "sad": os.getenv("SANDY_TTS_STYLE_SAD") or (_DEFAULT_BASE + "Sad mood."),
    "stressed": os.getenv("SANDY_TTS_STYLE_STRESSED") or (_DEFAULT_BASE + "Stressed mood."),
    "angry": os.getenv("SANDY_TTS_STYLE_ANGRY") or (_DEFAULT_BASE + "Angry mood."),
    "frustrated": os.getenv("SANDY_TTS_STYLE_FRUSTRATED") or (_DEFAULT_BASE + "Frustrated mood."),
    "empathetic": os.getenv("SANDY_TTS_STYLE_EMPATHETIC") or (_DEFAULT_BASE + "Empathetic mood."),
}


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(_SAMPLE_WIDTH)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _do_synthesize(text: str, mood: str, api_key: str) -> Optional[bytes]:
    from google.genai import types

    style = _MOOD_INSTRUCTIONS.get(mood) or _MOOD_INSTRUCTIONS["neutral"]
    client = _get_genai_client(api_key)

    # TTS models don't support system_instruction — style goes inside contents
    styled_text = f"{style}\n\n{text}"

    response = client.models.generate_content(
        model=_GEMINI_TTS_MODEL,
        contents=styled_text,
        config=types.GenerateContentConfig(
            # Per-request HTTP timeout (ms) — fail fast on a hung call.
            http_options=types.HttpOptions(timeout=_GEMINI_TTS_TIMEOUT_MS),
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_GEMINI_TTS_VOICE,
                    )
                ),
            ),
        ),
    )

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []
    if not parts:
        return None
    raw = getattr(parts[0], "inline_data", None)
    raw = getattr(raw, "data", None) if raw else None
    if not raw:
        return None
    if isinstance(raw, str):
        raw = base64.b64decode(raw)

    return _pcm_to_wav(raw)


def synthesize_voice_with_gemini(
    text: str,
    mood: str = "neutral",
    api_key: str = "",
) -> Optional[bytes]:
    """Synthesize speech via Gemini TTS. Returns WAV bytes or None on failure."""
    if not text:
        return None

    _api_key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not _api_key:
        logger.info("[Gemini TTS] no API key configured")
        return None

    try:
        result = _cb.call(_do_synthesize, text, mood, _api_key)
        if result:
            logger.info(f"[Gemini TTS] {len(result)} bytes, mood={mood}")
        return result
    except CircuitOpenError:
        logger.info("[Gemini TTS] circuit open, skipping to fallback")
        return None
    except Exception as e:
        logger.info(f"[Gemini TTS] {e}")
        return None
