"""Google TTS guards — the main voice path.

Voice synthesis races Gemini and Google in parallel and Google does the real
work in practice, so its voice-selection logic is worth pinning down. These are
pure/guard tests: _select_voice is pure, and the empty-text short-circuit needs
no Google client. The live synth call (network) stays a manual check.
"""
from app.integrations.google_tts import _select_voice, synthesize_voice_with_google

_MOOD_VOICES = {
    "happy": "voice-happy",
    "sad": "voice-sad",
    "serious": "voice-serious",
    "romantic": "voice-romantic",
}


def test_select_voice_defaults_to_base_when_mood_unknown():
    assert _select_voice("whatever", "normal", "base-voice", {}) == "base-voice"


def test_select_voice_picks_mood_voice():
    assert _select_voice("happy", "normal", "base-voice", _MOOD_VOICES) == "voice-happy"


def test_select_voice_style_overrides_mood():
    # style "serious" maps to the serious voice even on a neutral mood
    assert _select_voice("neutral", "serious", "base-voice", _MOOD_VOICES) == "voice-serious"


def test_select_voice_romantic_style():
    assert _select_voice("neutral", "romantic", "base-voice", _MOOD_VOICES) == "voice-romantic"


def test_synthesize_empty_text_returns_none():
    assert synthesize_voice_with_google("") is None
