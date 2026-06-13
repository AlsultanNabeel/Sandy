"""Voice TTS-prep tests: the summarize hop and emoji stripping.

The summarize step calls the model once; it MUST fall back to the deterministic
shortener when the model is unavailable or returns junk — that fallback is what
keeps spoken replies working when Gemini/Azure is down. The model call is mocked
here, so these run with no network and no keys.
"""
import importlib.util
from unittest.mock import MagicMock, patch

from app.features.voice import _remove_emojis, _shorten_for_tts, _summarize_for_tts

_CLIENT_PATH = "app.integrations.azure_intent_client.AzureIntentClient"


def _client_returning(text):
    client = MagicMock()
    client.generate_text.return_value = text
    return client


def test_summarize_uses_model_output_when_good():
    good = "ساندي لخّصت الخبر بجملة مفيدة وكاملة للنطق الصوتي."
    with patch(_CLIENT_PATH, return_value=_client_returning(good)):
        out = _summarize_for_tts("نص أصلي طويل ...", mood="happy", user_message="شو الأخبار؟")
    assert out == good


def test_summarize_falls_back_when_output_too_short():
    original = "هذا نص أصلي طويل بما يكفي ليُقصَّر بشكل حتمي عند فشل التلخيص."
    # "قصير" is under the 10-char floor, so the model output is rejected.
    with patch(_CLIENT_PATH, return_value=_client_returning("قصير")):
        out = _summarize_for_tts(original)
    assert out == _shorten_for_tts(original)


def test_summarize_falls_back_on_model_error():
    original = "نص أصلي للرجوع إليه عند رمي استثناء من النموذج."
    client = MagicMock()
    client.generate_text.side_effect = RuntimeError("model down")
    with patch(_CLIENT_PATH, return_value=client):
        out = _summarize_for_tts(original)
    assert out == _shorten_for_tts(original)


def test_remove_emojis_keeps_plain_text():
    assert _remove_emojis("مرحبا كيف حالك") == "مرحبا كيف حالك"


def test_remove_emojis_strips_when_lib_available():
    if importlib.util.find_spec("emoji") is None:
        return  # emoji lib not installed in this env — nothing to assert
    assert _remove_emojis("تمام 😄👍") == "تمام "
