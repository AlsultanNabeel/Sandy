"""تشفير حقول الذاكرة الحساسة بـ Fernet.

يشتغل فقط لو SANDY_LTM_KEY مضبوط في البيئة. لو مش مضبوط، encrypt_field
يرجّع النص زي ما هو. decrypt_field يعرف المشفّر من البادئة "enc:".

    enc = encrypt_field("نبيل يحب القهوة")  # "enc:gAAAAAB..."
    dec = decrypt_field(enc)                # "نبيل يحب القهوة"
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_PREFIX = "enc:"
_fernet: Optional[object] = None
_init_attempted = False


def _get_fernet():
    """يبني Fernet أول مرة فقط. يرجّع None لو الـ key مش متوفّر."""
    global _fernet, _init_attempted
    if _init_attempted:
        return _fernet
    _init_attempted = True

    key = os.getenv("SANDY_LTM_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        # الـ key لازم يفك لـ 32 بايت عشان يكون Fernet صالح
        try:
            decoded = base64.urlsafe_b64decode(key.encode())
            if len(decoded) != 32:
                raise ValueError("key must decode to 32 bytes")
        except Exception:
            logger.warning("[ltm_crypto] SANDY_LTM_KEY مش Fernet key صالح، التشفير معطّل")
            return None

        _fernet = Fernet(key.encode())
        logger.info("[ltm_crypto] LTM encryption enabled")
        return _fernet
    except ImportError:
        logger.warning("[ltm_crypto] cryptography package not installed, التشفير معطّل")
        return None


def is_enabled() -> bool:
    """True لو التشفير شغّال."""
    return _get_fernet() is not None


def encrypt_field(value: str) -> str:
    """يشفّر النص، أو يرجّعه زي ما هو لو التشفير معطّل."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    try:
        token = f.encrypt(value.encode("utf-8")).decode("ascii")  # type: ignore[attr-defined]
        return f"{_PREFIX}{token}"
    except Exception as exc:
        # Warn, not debug: a failed encrypt means plaintext gets stored.
        logger.warning(f"[ltm_crypto] encrypt failed, storing plaintext: {exc}")
        return value


def decrypt_field(value: str) -> str:
    """يفك التشفير، أو يرجّع النص زي ما هو لو مش مشفّر."""
    if not value or not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    f = _get_fernet()
    if f is None:
        return value
    try:
        token = value[len(_PREFIX):].encode("ascii")
        return f.decrypt(token).decode("utf-8")  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug(f"[ltm_crypto] decrypt failed: {exc}")
        return value


def generate_key_for_setup() -> str:
    """يولّد Fernet key جديد للـ env var. بتستدعيه مرة وقت إعداد المشروع."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")
