"""Tests for the deterministic TTS shortener in features/voice.py.

It's the fallback used when the LLM summary (`_summarize_for_tts`) fails —
sentence-aware, replacing the old hard `text[:120]` cut.
"""
from app.features.voice import _SENTENCE_SPLIT, _TTS_VOICE_BUDGET, _shorten_for_tts


def test_short_text_unchanged():
    s = "جملة قصيرة."
    assert _shorten_for_tts(s) == s


def test_empty_and_none():
    assert _shorten_for_tts("") == ""
    assert _shorten_for_tts(None) == ""


def test_long_text_capped_at_budget():
    long = "الجملة الأولى مهمة جداً. " * 30
    out = _shorten_for_tts(long)
    assert 0 < len(out) <= _TTS_VOICE_BUDGET


def test_keeps_whole_sentences():
    text = "الأولى هنا. الثانية كمان. " + "حشو طويل " * 60
    out = _shorten_for_tts(text)
    # First sentence is preserved intact (no mid-sentence cut on the common path).
    assert out.startswith("الأولى هنا.")
    assert len(out) <= _TTS_VOICE_BUDGET


def test_single_long_sentence_hard_cut():
    # No sentence punctuation, no spaces → must still respect the budget.
    assert len(_shorten_for_tts("ا" * 500)) <= _TTS_VOICE_BUDGET
    # Long but word-separated → cut on a word boundary, no trailing partial word.
    out = _shorten_for_tts("كلمة " * 200)
    assert len(out) <= _TTS_VOICE_BUDGET
    assert not out.endswith("كلم")


def test_sentence_split_handles_arabic_punctuation():
    parts = [p for p in _SENTENCE_SPLIT.split("سؤال؟ جواب! تمام.") if p.strip()]
    assert len(parts) == 3
