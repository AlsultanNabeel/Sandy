"""Speaker Verification (تمييز صوت المتكلّم).

تتأكد ساندي إنّ اللي بيحكي هو صاحبها قبل تنفيذ أمر حسّاس. نستعمل نموذج
CAM++ (عبر sherpa-onnx) بيشتغل محلياً على السيرفر، ONNX بدون torch،
مجاني وبدون أي حساب أو مفتاح.

التدفّق:
  1. التسجيل (enroll): المالك يبعت كم تسجيل صوتي ونطلّع منهم بصمة صوت
     (متّجه أرقام) ونخزّنها مشفّرة في Mongo (sandy_voiceprints).
  2. التحقّق (verify): ناخد مقطع، نطلّع بصمته، ونقارنها بالمخزّنة بالـ cosine
     لنحصل على (تطابق؟، درجة 0..1).

النموذج (~28 ميجا) بيتنزّل مرة وحدة وقت أول استخدام وبيتخزّن محلياً.
لو sherpa-onnx مش منصّبة أو ما قدر ينزّل النموذج بتتجاهَل الميزة بهدوء.

التشفير: بصمة الصوت بيانات حيوية فبتتشفّر بـ Fernet عبر مفتاح مستقل
SANDY_BIO_KEY. بدون المفتاح تُخزَّن base64 خام مع تحذير.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_COLLECTION = "sandy_voiceprints"
_ENC_PREFIX = "enc:"

# درجة التطابق الدنيا للقبول (cosine بين البصمتين، 0..1). قابلة للضبط.
_MATCH_THRESHOLD = float(os.getenv("SANDY_SPEAKER_THRESHOLD", "0.5"))
# أقل طول مقطع مقبول (نصف ثانية عند 16kHz) — أقصر من هيك ما بيطلّع بصمة موثوقة.
_MIN_SAMPLES = 8000

# النموذج: CAM++ (English VoxCeleb) — بصمة الصوت لغة-مستقلة فالعربي تمام.
_DEFAULT_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
)
_DEFAULT_MODEL_PATH = "/tmp/sandy_speaker_campplus.onnx"  # nosec B108

_mongo_db = None
_fernet = None
_fernet_init = False
_extractor = None
_extractor_init = False
_model_lock = threading.Lock()


# إعداد المخزن
def init_speaker_store(mongo_db) -> None:
    """يُستدعى مرّة عند الإقلاع (زي init_mongo_memory)."""
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is not None:
        try:
            mongo_db[_COLLECTION].create_index([("chat_id", 1)], background=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("[speaker_id] index create skipped: %s", e)
    # تسخين النموذج بالخلفية عشان أول تحقّق ما ينتظر التنزيل (لا يعطّل الإقلاع).
    if is_available():
        threading.Thread(target=_get_extractor, daemon=True).start()


def is_available() -> bool:
    """جاهزة لو مكتبة sherpa-onnx منصّبة (تنزيل النموذج يصير لاحقاً عند الحاجة)."""
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# النموذج
def _ensure_model() -> Optional[str]:
    """يرجّع مسار ملف النموذج، ينزّله مرّة وحدة لو مش موجود."""
    path = os.getenv("SANDY_SPEAKER_MODEL_PATH", _DEFAULT_MODEL_PATH)
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        return path
    url = os.getenv("SANDY_SPEAKER_MODEL_URL", _DEFAULT_MODEL_URL)
    with _model_lock:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            return path
        try:
            import requests
            logger.info("[speaker_id] downloading speaker model → %s", path)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".part"
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            os.replace(tmp, path)
            logger.info("[speaker_id] speaker model ready (%d bytes)", os.path.getsize(path))
            return path
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker_id] model download failed: %s", e)
            return None


def _get_extractor():
    """Lazy singleton: يبني مُستخرِج البصمات مرّة وحدة."""
    global _extractor, _extractor_init
    if _extractor_init:
        return _extractor
    _extractor_init = True
    try:
        import sherpa_onnx
        model = _ensure_model()
        if not model:
            return None
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model, num_threads=1, provider="cpu"
        )
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        logger.info("[speaker_id] embedding extractor ready (dim=%d)", _extractor.dim)
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker_id] extractor init failed: %s", e)
        _extractor = None
    return _extractor


# أدوات الصوت والمتّجهات
def _pcm_to_float(pcm_bytes: bytes):
    """PCM 16-bit little-endian (من ffmpeg) → numpy float32 في [-1, 1]."""
    import numpy as np
    if len(pcm_bytes) % 2:
        pcm_bytes = pcm_bytes[:-1]
    return np.frombuffer(pcm_bytes, dtype="<i2").astype("float32") / 32768.0


def _embed(pcm_bytes: bytes):
    """يطلّع بصمة صوت مُطبَّعة (unit vector) من مقطع PCM، أو None."""
    ext = _get_extractor()
    if ext is None:
        return None
    import numpy as np
    samples = _pcm_to_float(pcm_bytes)
    if samples.size < _MIN_SAMPLES:
        return None
    try:
        stream = ext.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples)
        stream.input_finished()
        if not ext.is_ready(stream):
            return None
        emb = np.asarray(ext.compute(stream), dtype="float32")
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker_id] embed failed: %s", e)
        return None
    return _normalize(emb)


def _normalize(vec):
    import numpy as np
    n = float(np.linalg.norm(vec))
    return vec / n if n > 0 else None


def _cosine(a, b) -> float:
    """تشابه جيب التمام بين متّجهين مُطبَّعين → جداء نقطي."""
    import numpy as np
    return float(np.dot(a, b))


# التسجيل (enroll)
def enroll_speaker(chat_id: int, pcm_samples: List[bytes]) -> Tuple[bool, int, str]:
    """يبني بصمة صوت من كم مقطع PCM (يأخذ المتوسط) ويخزّنها.

    يرجّع (تمّ التخزين؟، عدد المقاطع المقبولة، رسالة عربية للمستخدم).
    """
    if not is_available():
        return False, 0, "تمييز الصوت غير مفعّل (مكتبة sherpa-onnx ناقصة)."
    if not pcm_samples:
        return False, 0, "ما وصلني أي تسجيل."
    if _get_extractor() is None:
        return False, 0, "ما قدرت أجهّز نموذج الصوت (تنزيل/تحميل فشل)."

    import numpy as np
    embeddings = []
    for blob in pcm_samples:
        emb = _embed(blob)
        if emb is not None:
            embeddings.append(emb)

    if not embeddings:
        return False, 0, "التسجيلات قصيرة أو مش واضحة — جرّب تسجيلات أطول وأوضح."

    mean = _normalize(np.mean(embeddings, axis=0))
    if mean is None:
        return False, 0, "صار خلل ببناء البصمة."
    _save_profile(chat_id, mean.astype("float32").tobytes(), len(embeddings))
    return True, len(embeddings), f"تمام! صرت أعرف صوتك ✅ ({len(embeddings)} مقاطع)"


# التحقّق (verify)
def verify_speaker(chat_id: int, pcm_bytes: bytes) -> Tuple[bool, float]:
    """يقارن مقطع صوتي ببصمة المالك المخزّنة → (تطابق؟، درجة 0..1)."""
    if not is_available():
        return False, 0.0
    stored = get_profile_vector(chat_id)
    if stored is None:
        return False, 0.0
    emb = _embed(pcm_bytes)
    if emb is None:
        return False, 0.0
    score = _cosine(stored, emb)
    return score >= _MATCH_THRESHOLD, score


# التشفير والتخزين
def _get_fernet():
    global _fernet, _fernet_init
    if _fernet_init:
        return _fernet
    _fernet_init = True
    key = os.getenv("SANDY_BIO_KEY", "").strip()
    if not key:
        logger.warning("[speaker_id] SANDY_BIO_KEY غير مضبوط — بصمات الصوت غير مشفّرة")
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode())
        logger.info("[speaker_id] voiceprint encryption enabled")
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker_id] SANDY_BIO_KEY غير صالح — التشفير معطّل: %s", e)
        _fernet = None
    return _fernet


def _encode_profile(profile_bytes: bytes) -> str:
    f = _get_fernet()
    if f is None:
        return base64.b64encode(profile_bytes).decode("ascii")
    return _ENC_PREFIX + f.encrypt(profile_bytes).decode("ascii")


def _decode_profile(stored: str) -> Optional[bytes]:
    if not stored:
        return None
    try:
        if stored.startswith(_ENC_PREFIX):
            f = _get_fernet()
            if f is None:
                logger.warning("[speaker_id] بصمة مشفّرة بدون مفتاح متاح")
                return None
            return f.decrypt(stored[len(_ENC_PREFIX):].encode("ascii"))
        return base64.b64decode(stored.encode("ascii"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker_id] فشل فكّ بصمة الصوت: %s", e)
        return None


def _save_profile(chat_id: int, vector_bytes: bytes, n_samples: int) -> None:
    if _mongo_db is None:
        logger.warning("[speaker_id] Mongo غير متاح — لم تُحفظ البصمة")
        return
    doc = {
        "_id": str(chat_id),
        "chat_id": chat_id,
        "profile": _encode_profile(vector_bytes),
        "n_samples": n_samples,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _mongo_db[_COLLECTION].replace_one({"_id": str(chat_id)}, doc, upsert=True)


def get_profile_vector(chat_id: int):
    """يرجّع بصمة المالك كمتّجه numpy مُطبَّع، أو None."""
    if _mongo_db is None:
        return None
    try:
        doc = _mongo_db[_COLLECTION].find_one({"_id": str(chat_id)})
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker_id] Mongo find failed: %s", e)
        return None
    if not doc or not doc.get("profile"):
        return None
    raw = _decode_profile(doc["profile"])
    if not raw:
        return None
    import numpy as np
    return np.frombuffer(raw, dtype="float32").copy()


def has_profile(chat_id: int) -> bool:
    return get_profile_vector(chat_id) is not None


def delete_profile(chat_id: int) -> bool:
    if _mongo_db is None:
        return False
    try:
        res = _mongo_db[_COLLECTION].delete_one({"_id": str(chat_id)})
        return res.deleted_count > 0
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker_id] Mongo delete failed: %s", e)
        return False
