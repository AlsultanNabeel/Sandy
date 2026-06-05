"""Azure Speech integration with circuit breaker and ffmpeg retry."""

import concurrent.futures
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Optional

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

_stt_cb = CircuitBreaker(name="azure_stt", failure_threshold=5, recovery_timeout=60.0)
_tts_cb = CircuitBreaker(name="azure_tts", failure_threshold=5, recovery_timeout=60.0)

# A corrupt clip won't pass on a second attempt — extra retries only add latency.
_FFMPEG_MAX_RETRIES = 1

# Bound recognition so a hung call fails fast and the caller degrades gracefully.
_STT_TIMEOUT_SEC = 12.0

# Bound synthesis so a hung fallback can't stall the background TTS thread; on
# timeout we return None and the caller degrades to the next provider.
_TTS_TIMEOUT_SEC = 10.0

# Raw PCM output format ffmpeg pipes to us and the recognizer is told to expect.
_PCM_SAMPLE_RATE = 16000
_PCM_BITS_PER_SAMPLE = 16
_PCM_CHANNELS = 1

# Cache SpeechConfig at module scope (keyed by key+region+language/voice) so it
# isn't rebuilt on every utterance. Recognizers/synthesizers stay per-call.
_speech_config_cache: dict = {}


def _get_stt_speech_config(
    azure_speech_key: str, azure_speech_region: str, recognition_language: str
):
    import azure.cognitiveservices.speech as speechsdk

    cache_key = ("stt", azure_speech_key, azure_speech_region, recognition_language)
    cfg = _speech_config_cache.get(cache_key)
    if cfg is None:
        cfg = speechsdk.SpeechConfig(
            subscription=azure_speech_key, region=azure_speech_region
        )
        cfg.speech_recognition_language = recognition_language
        _speech_config_cache[cache_key] = cfg
    return cfg


def _get_tts_speech_config(
    azure_speech_key: str, azure_speech_region: str, azure_speech_voice: str
):
    import azure.cognitiveservices.speech as speechsdk

    cache_key = ("tts", azure_speech_key, azure_speech_region, azure_speech_voice)
    cfg = _speech_config_cache.get(cache_key)
    if cfg is None:
        cfg = speechsdk.SpeechConfig(
            subscription=azure_speech_key, region=azure_speech_region
        )
        cfg.speech_synthesis_voice_name = azure_speech_voice
        cfg.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
        )
        _speech_config_cache[cache_key] = cfg
    return cfg


def audio_to_pcm16(audio_bytes: bytes) -> Optional[bytes]:
    """Public: encoded audio (ogg/opus/mp3/wav...) → raw 16kHz mono 16-bit PCM.

    Used by speaker enrollment/verification (features/speaker_id) which needs raw
    PCM, reusing the same in-memory ffmpeg pipe as STT."""
    return _convert_audio_to_pcm(audio_bytes)


def _convert_audio_to_pcm(audio_bytes: bytes) -> Optional[bytes]:
    """Convert encoded audio bytes to raw 16kHz mono 16-bit PCM via an in-memory
    ffmpeg pipe (stdin -> stdout, no temp files). Returns PCM bytes or None."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ac",
        str(_PCM_CHANNELS),
        "-ar",
        str(_PCM_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    for attempt in range(1, _FFMPEG_MAX_RETRIES + 1):
        try:
            proc = subprocess.run(  # nosec B603
                cmd, input=audio_bytes, capture_output=True
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            print(f"[Azure STT] ffmpeg pipe attempt {attempt} failed: {err[:200]}")
        except Exception as e:
            print(f"[Azure STT] ffmpeg pipe attempt {attempt} error: {e}")
    return None


def _convert_audio_to_wav(input_path: str, output_path: str) -> bool:
    """Convert audio file to 16kHz mono WAV (fallback path, temp files).

    Retries up to _FFMPEG_MAX_RETRIES times.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        output_path,
    ]
    for attempt in range(1, _FFMPEG_MAX_RETRIES + 1):
        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )  # nosec B603
            return True
        except subprocess.CalledProcessError as e:
            print(f"[Azure STT] ffmpeg attempt {attempt} failed: {e}")
    return False


def _build_recognizer(
    speechsdk,
    speech_config,
    pcm_bytes: Optional[bytes],
    temp_wav: Optional[str],
):
    """Build a SpeechRecognizer fed either from in-memory PCM (push stream) or a
    WAV file. The push stream keeps a reference to the audio for its lifetime, so
    the recognizer must outlive a single utterance only (which it does)."""
    if pcm_bytes is not None:
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=_PCM_SAMPLE_RATE,
            bits_per_sample=_PCM_BITS_PER_SAMPLE,
            channels=_PCM_CHANNELS,
        )
        push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        push_stream.write(pcm_bytes)
        push_stream.close()
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    else:
        audio_config = speechsdk.audio.AudioConfig(filename=temp_wav)
    return speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_config
    )


def _do_transcribe(
    azure_speech_key: str,
    azure_speech_region: str,
    recognition_language: str,
    pcm_bytes: Optional[bytes] = None,
    temp_wav: Optional[str] = None,
) -> Optional[str]:
    import azure.cognitiveservices.speech as speechsdk

    speech_config = _get_stt_speech_config(
        azure_speech_key, azure_speech_region, recognition_language
    )
    recognizer = _build_recognizer(speechsdk, speech_config, pcm_bytes, temp_wav)

    # Run recognition in a worker thread with a bounded wait so a hung backend
    # call fails fast; on timeout we return None and the caller degrades.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: recognizer.recognize_once_async().get())
            result = future.result(timeout=_STT_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        print(f"[Azure STT] recognition timed out after {_STT_TIMEOUT_SEC}s")
        return None

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        transcript = (result.text or "").strip()
        if transcript:
            print(f"[Azure STT] transcript: {transcript[:80]}")
            return transcript

    if result.reason == speechsdk.ResultReason.NoMatch:
        print("[Azure STT] no speech recognized")
        return None

    if result.reason == speechsdk.ResultReason.Canceled:
        details = speechsdk.CancellationDetails(result)
        print(
            f"[Azure STT] canceled: reason={details.reason}, details={details.error_details}"
        )
        return None

    print(f"[Azure STT] unexpected result: {result.reason}")
    return None


def transcribe_audio_with_azure(
    audio_bytes: bytes,
    azure_speech_available: bool,
    azure_speech_key: str,
    azure_speech_region: str,
    file_name: str = "voice.ogg",
    recognition_language: str = "ar-EG",
) -> Optional[str]:
    """Transcribe audio bytes using Azure Speech SDK. Protected by circuit breaker."""
    if not audio_bytes:
        return None
    if not azure_speech_available or not azure_speech_key or not azure_speech_region:
        print("[Azure STT] Speech SDK/key/region not configured")
        return None

    try:
        import azure.cognitiveservices.speech  # noqa: F401
    except ImportError:
        print("[Azure STT] Azure Speech SDK not installed")
        return None

    suffix = Path(file_name).suffix or ".ogg"
    temp_input = None
    temp_wav = None

    try:
        # Fast path: convert via an in-memory ffmpeg pipe (no temp files) and feed
        # raw PCM to the recognizer through a push stream.
        pcm_bytes = _convert_audio_to_pcm(audio_bytes)

        if pcm_bytes is None:
            # Safe fallback: original temp-file ffmpeg + file-based AudioConfig, in
            # case in-memory piping is unreliable on this platform/clip.
            print("[Azure STT] PCM pipe unavailable, falling back to temp files")
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(audio_bytes)
                temp_input = tmp.name

            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_wav:
                temp_wav = tmp_wav.name

            if not _convert_audio_to_wav(temp_input, temp_wav):
                print("[Azure STT] ffmpeg conversion failed after retries")
                return None

        try:
            return _stt_cb.call(
                _do_transcribe,
                azure_speech_key,
                azure_speech_region,
                recognition_language,
                pcm_bytes,
                temp_wav,
            )
        except CircuitOpenError:
            print("[Azure STT] circuit open, skipping transcription")
            return None

    except Exception as e:
        print(f"[Azure STT] transcription failed: {e}")
        return None

    finally:
        for p in [temp_input, temp_wav]:
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except Exception:
                    pass


def _do_synthesize(
    text: str,
    temp_path: str,
    azure_speech_key: str,
    azure_speech_region: str,
    azure_speech_voice: str,
) -> Optional[bytes]:
    import azure.cognitiveservices.speech as speechsdk

    speech_config = _get_tts_speech_config(
        azure_speech_key, azure_speech_region, azure_speech_voice
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=temp_path)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_config
    )

    # speak_text_async().get() has no native timeout, so run it in a worker thread
    # with a bounded wait; on timeout we return None and the caller degrades.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: synthesizer.speak_text_async(text).get())
            result = future.result(timeout=_TTS_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        print(f"[Azure TTS] synthesis timed out after {_TTS_TIMEOUT_SEC}s")
        return None

    if (
        result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted
        and Path(temp_path).exists()
    ):
        with open(temp_path, "rb") as f:
            return f.read()

    print(f"[Azure TTS] synthesis failed: {result.reason}")
    return None


def synthesize_voice_with_azure(
    text: str,
    azure_speech_available: bool,
    azure_speech_key: str,
    azure_speech_region: str,
    azure_speech_voice: str,
) -> Optional[bytes]:
    """Synthesize text to WAV using Azure Speech. Protected by circuit breaker."""
    if not text:
        return None
    if not azure_speech_available or not azure_speech_key or not azure_speech_region:
        print("[Azure TTS] Speech SDK/key/region not configured")
        return None

    try:
        import azure.cognitiveservices.speech  # noqa: F401
    except ImportError:
        print("[Azure TTS] Azure Speech SDK not installed")
        return None

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            temp_path = tmp.name

        try:
            result = _tts_cb.call(
                _do_synthesize,
                text,
                temp_path,
                azure_speech_key,
                azure_speech_region,
                azure_speech_voice,
            )
        except CircuitOpenError:
            print("[Azure TTS] circuit open, skipping TTS")
            return None

        if result:
            print("[Azure TTS] voice generated")
        return result

    except Exception as e:
        print(f"[Azure TTS] error: {e}")
        return None

    finally:
        if temp_path and Path(temp_path).exists():
            try:
                Path(temp_path).unlink()
            except Exception:
                pass
