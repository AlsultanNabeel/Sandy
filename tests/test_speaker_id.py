"""Unit tests for the pure helpers in features/speaker_id.py.

These don't need sherpa-onnx or the model — they cover PCM→float conversion,
cosine/normalize, the voiceprint encode/decode round-trip, and graceful
degradation when the engine isn't available.
"""
import importlib
from array import array

import numpy as np

import app.features.speaker_id as sid


def test_pcm_to_float_roundtrip():
    pcm = array("h", [0, 32767, -32768, 16384]).tobytes()
    out = sid._pcm_to_float(pcm)
    assert out.dtype == np.float32
    assert abs(out[0]) < 1e-6
    assert abs(out[1] - (32767 / 32768.0)) < 1e-4
    assert abs(out[2] - (-1.0)) < 1e-6


def test_pcm_to_float_drops_odd_trailing_byte():
    out = sid._pcm_to_float(array("h", [5, 6]).tobytes() + b"\x01")
    assert out.size == 2


def test_normalize_makes_unit_vector():
    v = sid._normalize(np.array([3.0, 4.0], dtype="float32"))
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-6


def test_normalize_zero_vector_is_none():
    assert sid._normalize(np.zeros(4, dtype="float32")) is None


def test_cosine_identical_and_orthogonal():
    a = sid._normalize(np.array([1.0, 1.0], dtype="float32"))
    b = sid._normalize(np.array([1.0, 1.0], dtype="float32"))
    assert abs(sid._cosine(a, b) - 1.0) < 1e-6
    o1 = sid._normalize(np.array([1.0, 0.0], dtype="float32"))
    o2 = sid._normalize(np.array([0.0, 1.0], dtype="float32"))
    assert abs(sid._cosine(o1, o2)) < 1e-6


def test_profile_encode_decode_roundtrip_without_key(monkeypatch):
    monkeypatch.delenv("SANDY_BIO_KEY", raising=False)
    importlib.reload(sid)
    raw = np.array([0.1, -0.2, 0.3], dtype="float32").tobytes()
    encoded = sid._encode_profile(raw)
    assert not encoded.startswith("enc:")
    assert sid._decode_profile(encoded) == raw


def test_profile_encode_decode_roundtrip_with_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("SANDY_BIO_KEY", Fernet.generate_key().decode())
    importlib.reload(sid)
    raw = np.array([0.42, -0.99, 0.01], dtype="float32").tobytes()
    encoded = sid._encode_profile(raw)
    assert encoded.startswith("enc:")
    assert sid._decode_profile(encoded) == raw
    importlib.reload(sid)  # reset module crypto state for other tests


def test_graceful_when_engine_unavailable(monkeypatch):
    # Force the engine off (regardless of whether sherpa-onnx is installed in CI)
    # so enroll/verify degrade quietly — and so the test never hits the network
    # to download the model.
    monkeypatch.setattr(sid, "is_available", lambda: False)
    ok, n, msg = sid.enroll_speaker(123, [b"\x00\x01" * 10000])
    assert ok is False and n == 0 and isinstance(msg, str)
    match, score = sid.verify_speaker(123, b"\x00\x01" * 10000)
    assert match is False and score == 0.0
