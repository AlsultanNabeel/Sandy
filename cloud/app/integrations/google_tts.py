"""Google Cloud Text-to-Speech integration with cached client and circuit breaker."""

import logging
import os
from typing import Any, Dict, Optional

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

_cb = CircuitBreaker(name="google_tts", failure_threshold=5, recovery_timeout=60.0)
_tts_client = None  # module-level cached client; created once

# Upper bound (seconds) on the synthesize request so a hung fallback can't stall
# the background TTS thread. Env-tunable; generous enough for legitimate synthesis.
_SYNTH_TIMEOUT_SEC = float(os.getenv("GOOGLE_TTS_TIMEOUT_SEC", "8.0"))


def _get_tts_client() -> Any:
    """Return a cached TextToSpeechClient, creating it on first call."""
    global _tts_client
    if _tts_client is None:
        from google.cloud import texttospeech

        _tts_client = texttospeech.TextToSpeechClient()
        logger.info("[Google TTS] client initialised")
    return _tts_client


def _select_voice(
    mood: str,
    style: str,
    google_tts_voice: str,
    mood_tts_voices: Dict[str, str],
) -> str:
    """Pick the best voice name for the given mood/style combination."""
    selected = mood_tts_voices.get(mood, google_tts_voice)
    style_map = {
        "romantic": "romantic",
        "caring": mood if mood in ("sad", "angry", "neutral") else None,
        "serious": "serious",
        "excited": "excited",
        "shy": "shy",
        "playful": "happy",
    }
    style_key = style_map.get(style)
    if style_key:
        selected = mood_tts_voices.get(style_key, selected)
    return selected


def synthesize_voice_with_google(
    text: str,
    mood: str = "neutral",
    style: str = "normal",
    google_tts_voice: str = "",
    google_tts_language_code: str = "en-US",
    mood_tts_voices: Optional[Dict[str, str]] = None,
) -> Optional[bytes]:
    """Synthesize speech with Google TTS; returns LINEAR16 WAV bytes or None."""
    if not text:
        return None

    try:
        from google.cloud import texttospeech
    except ImportError:
        logger.warning("[Google TTS] google-cloud-texttospeech not installed")
        return None

    _mood_voices = mood_tts_voices or {}
    selected_voice = _select_voice(mood, style, google_tts_voice, _mood_voices)

    def _synthesize() -> Optional[bytes]:
        client = _get_tts_client()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=google_tts_language_code,
            name=selected_voice,
            ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16
        )
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
            timeout=_SYNTH_TIMEOUT_SEC,
        )
        return response.audio_content

    try:
        return _cb.call(_synthesize)
    except CircuitOpenError:
        logger.warning("[Google TTS] circuit open, skipping TTS")
        return None
    except Exception as e:
        logger.error("[Google TTS] error: %s", e)
        # reset cached client so next call rebuilds it (handles credential expiry)
        global _tts_client
        _tts_client = None
        return None
