"""Sandy web auth: JWT access control with a Telegram approval flow."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt  # PyJWT

_JWT_ALGO = "HS256"
OWNER_TOKEN_HOURS = 24 * 7   # 7 days
GUEST_TOKEN_HOURS = 48        # 2 days
_RATE_WINDOW = 900            # 15 minutes
_RATE_MAX = 5                 # max login attempts per window


def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "")


def make_token(role: str) -> str:
    hours = OWNER_TOKEN_HOURS if role == "owner" else GUEST_TOKEN_HOURS
    payload = {
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALGO)


def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def check_owner_password(password: str) -> bool:
    owner_pass = os.getenv("OWNER_PASSWORD", "")
    if not owner_pass:
        return False
    return hmac.compare_digest(
        hashlib.sha256(password.encode()).digest(),
        hashlib.sha256(owner_pass.encode()).digest(),
    )


def _redis():
    try:
        from app.utils.redis_stm import get_redis_stm_client
        c = get_redis_stm_client()
        return c.client if c.enabled else None
    except Exception:
        return None


def check_rate_limit(ip: str) -> Tuple[bool, int]:
    """Returns (allowed, attempts_remaining). Fails open if Redis unavailable."""
    r = _redis()
    if not r:
        return True, _RATE_MAX
    key = f"auth:rate:{ip}"
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _RATE_WINDOW)
        count = pipe.execute()[0]
        return count <= _RATE_MAX, max(0, _RATE_MAX - count)
    except Exception:
        return True, _RATE_MAX


def store_access_request(name: str, reason: str = "") -> str:
    """Store pending request in Redis, return request_id."""
    request_id = str(uuid.uuid4())[:8]
    r = _redis()
    if r:
        r.setex(
            f"access_req:{request_id}",
            3600,
            json.dumps({"name": name, "reason": reason, "status": "pending", "token": None}),
        )
    return request_id


def get_access_request(request_id: str) -> Optional[dict]:
    r = _redis()
    if not r:
        return None
    raw = r.get(f"access_req:{request_id}")
    return json.loads(raw) if raw else None


def approve_access_request(request_id: str) -> Optional[str]:
    """Approve the request, mint a guest token, and return it."""
    r = _redis()
    if not r:
        return None
    key = f"access_req:{request_id}"
    raw = r.get(key)
    if not raw:
        return None
    data = json.loads(raw)
    token = make_token("guest")
    data.update({"status": "approved", "token": token})
    r.setex(key, 3600, json.dumps(data))
    return token


def deny_access_request(request_id: str) -> bool:
    r = _redis()
    if not r:
        return False
    key = f"access_req:{request_id}"
    raw = r.get(key)
    if not raw:
        return False
    data = json.loads(raw)
    data["status"] = "denied"
    r.setex(key, 300, json.dumps(data))
    return True
