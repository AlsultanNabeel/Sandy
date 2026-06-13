"""Sandy Device Client — MQTT-based (HiveMQ Cloud).

Architecture:
    Sandy ──publish──→  HiveMQ Cloud broker  ──subscribe──→  ESP32
    Sandy ←─subscribe──                       ←──publish──   ESP32

Topics:
    sandy/cmd/mood        — payload: "happy" / "sad" / إلخ
    sandy/cmd/servo       — payload: "90"
    sandy/cmd/buzzer      — payload: "alert"
    sandy/cmd/base        — payload: "forward"
    sandy/cmd/autonomous  — payload: "true" / "false"
    sandy/status          — ESP يبعث JSON status periodically
    sandy/event           — ESP يبعث أحداث (distance alert)

Sandy's own device-control client — the single body-control API.

Env vars (في .env — لا ترفع لـ git):
    SANDY_MQTT_HOST     — broker hostname (مثل abc123.s2.eu.hivemq.cloud)
    SANDY_MQTT_PORT     — عادة 8883 (TLS)
    SANDY_MQTT_USER     — username من HiveMQ
    SANDY_MQTT_PASS     — password
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt  # type: ignore
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

_TOPIC_CMD_MOOD       = "sandy/cmd/mood"
_TOPIC_CMD_SERVO      = "sandy/cmd/servo"
_TOPIC_CMD_BUZZER     = "sandy/cmd/buzzer"
_TOPIC_CMD_BASE       = "sandy/cmd/base"
_TOPIC_CMD_AUTONOMOUS = "sandy/cmd/autonomous"
_TOPIC_STATUS         = "sandy/status"
_TOPIC_EVENT          = "sandy/event"
# Camera
_TOPIC_CAM_REQUEST    = "sandy/cam/request"
_TOPIC_CAM_SNAPSHOT   = "sandy/cam/snapshot"
_TOPIC_CAM_STATUS     = "sandy/cam/status"
_TOPIC_CAM_EVENT      = "sandy/cam/event"

# Must match MOOD_MAP in firmware/brain-core/main/sandy_mqtt.c exactly —
# anything else is logged as "unknown mood" on the robot and ignored.
_VALID_MOODS = {
    "idle", "happy", "curious", "sad", "alert", "surprised", "big_happy",
    "focused", "bored", "excited", "love", "angry", "confused", "thinking",
    "sleepy", "shy", "proud", "worried", "playful", "calm", "grumpy",
    "hopeful", "grateful", "disappointed", "silly",
}

_REACTION_TO_MOOD = {
    "happy":     "happy",
    "sad":       "sad",
    "angry":     "angry",
    "surprise":  "surprised",
    "love":      "love",
    "neutral":   "idle",
    "thinking":  "thinking",
    "curious":   "curious",
}

# Must match _handle_buzzer in firmware/brain-core/main/sandy_mqtt.c.
_VALID_BUZZER = {
    "boot", "happy", "curious", "sad", "alert", "error",
    "focus_start", "focus_break", "focus_end",
}
VALID_BUZZER = frozenset(_VALID_BUZZER)   # public: callers pick a melody from this
_VALID_BASE   = {"forward", "backward", "left", "right", "stop"}


class SandyDeviceClient:
    """MQTT-based client for the robot body (mood/servo/buzzer/base)."""

    def __init__(self):
        self._host = os.getenv("SANDY_MQTT_HOST", "").strip()
        self._user = os.getenv("SANDY_MQTT_USER", "").strip()
        self._pass = os.getenv("SANDY_MQTT_PASS", "").strip()
        try:
            self._port = int(os.getenv("SANDY_MQTT_PORT", "8883"))
        except ValueError:
            self._port = 8883
        self._client: Optional[Any] = None
        self._connected = False
        self._lock = threading.RLock()  # re-entrant: _publish→_ensure_client may re-enter from sync_mood_async
        self._latest_status: dict = {}
        self._last_seen: float = 0.0
        # Camera snapshot buffers — keyed by request id
        # value: {"chunks": {seq: bytes}, "total": int, "complete": bool, "started": float}
        self._cam_buffers: dict = {}
        self._cam_lock = threading.RLock()
        self._cam_latest_status: dict = {}

    @property
    def available(self) -> bool:
        return MQTT_AVAILABLE and bool(self._host and self._user and self._pass)

    def _ensure_client(self) -> Optional[Any]:
        if not self.available:
            return None
        with self._lock:
            # Return existing client even if on_connect hasn't fired yet — paho buffers
            # qos=1 publishes until the link comes up. Recreating here would collide on
            # client_id and cause HiveMQ to flap the prior session.
            if self._client is not None:
                return self._client
            try:
                # Generate stable client_id per process
                client_id = f"sandy-backend-{os.getpid()}"
                c = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=client_id,
                    clean_session=False,
                )
                c.username_pw_set(self._user, self._pass)
                c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
                c.on_connect    = self._on_connect
                c.on_disconnect = self._on_disconnect
                c.on_message    = self._on_message
                c.connect(self._host, self._port, keepalive=60)
                c.loop_start()
                self._client = c
                return c
            except Exception as e:
                logger.warning("[sandy_device] MQTT connect failed: %s", e)
                self._client = None
                self._connected = False
                return None

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected = True
            client.subscribe(_TOPIC_STATUS, qos=1)
            client.subscribe(_TOPIC_EVENT, qos=1)
            # Camera topics
            client.subscribe(_TOPIC_CAM_SNAPSHOT, qos=1)
            client.subscribe(_TOPIC_CAM_STATUS, qos=1)
            client.subscribe(_TOPIC_CAM_EVENT, qos=1)
            logger.info("[sandy_device] MQTT connected to broker")
        else:
            self._connected = False
            logger.warning("[sandy_device] MQTT connect failed rc=%s", rc)

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        self._connected = False
        logger.warning("[sandy_device] MQTT disconnected")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        if msg.topic == _TOPIC_STATUS:
            try:
                data = json.loads(payload)
                self._latest_status.update(data)
                self._last_seen = time.time()
            except Exception:
                pass
        elif msg.topic == _TOPIC_EVENT:
            logger.info("[sandy_device] event: %s", payload)
        elif msg.topic == _TOPIC_CAM_STATUS:
            try:
                self._cam_latest_status.update(json.loads(payload))
            except Exception:
                pass
        elif msg.topic == _TOPIC_CAM_SNAPSHOT:
            self._handle_cam_chunk(payload)
        elif msg.topic == _TOPIC_CAM_EVENT:
            self._handle_cam_event(payload)

    # Camera chunk reassembly
    def _handle_cam_chunk(self, payload: str):
        try:
            data = json.loads(payload)
            id_ = data.get("id")
            seq = int(data.get("seq", 0))
            total = int(data.get("total", 0))
            b64 = data.get("data", "")
            if not id_ or not b64:
                return
            import base64
            chunk_bytes = base64.b64decode(b64)
        except Exception as e:
            logger.warning("[sandy_device] cam chunk parse failed: %s", e)
            return

        with self._cam_lock:
            buf = self._cam_buffers.get(id_)
            if buf is None:
                buf = {"chunks": {}, "total": total, "complete": False, "started": time.time()}
                self._cam_buffers[id_] = buf
            buf["chunks"][seq] = chunk_bytes
            if total > 0:
                buf["total"] = total

    def _handle_cam_event(self, payload: str):
        try:
            data = json.loads(payload)
            id_ = data.get("id")
            event = data.get("event")
            if id_ and event == "complete":
                with self._cam_lock:
                    buf = self._cam_buffers.get(id_)
                    if buf:
                        buf["complete"] = True
            elif data.get("error"):
                logger.warning("[sandy_device] cam error: %s", data.get("error"))
        except Exception:
            pass

    def _cleanup_old_buffers(self, max_age_sec: float = 60.0):
        now = time.time()
        with self._cam_lock:
            stale = [id_ for id_, buf in self._cam_buffers.items()
                     if now - buf.get("started", now) > max_age_sec]
            for id_ in stale:
                del self._cam_buffers[id_]

    def request_snapshot(self, timeout_sec: float = 15.0) -> Optional[bytes]:
        """طلب snapshot من ESP-CAM، ينتظر تجميع كل الـ chunks، ويرجّع JPEG bytes."""
        if not self.available:
            return None
        self._cleanup_old_buffers()
        import uuid as _uuid
        rid = _uuid.uuid4().hex[:12]
        with self._cam_lock:
            self._cam_buffers[rid] = {"chunks": {}, "total": 0, "complete": False, "started": time.time()}

        payload = json.dumps({"id": rid})
        if not self._publish(_TOPIC_CAM_REQUEST, payload):
            return None

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(0.15)
            with self._cam_lock:
                buf = self._cam_buffers.get(rid)
                if buf and buf["complete"]:
                    chunks = buf["chunks"]
                    total = buf["total"]
                    if total > 0 and len(chunks) >= total:
                        ordered = [chunks[i] for i in range(total) if i in chunks]
                        if len(ordered) == total:
                            return b"".join(ordered)
        logger.warning("[sandy_device] snapshot timeout for id=%s", rid)
        return None

    def _publish(self, topic: str, payload: str) -> bool:
        c = self._ensure_client()
        if c is None:
            return False
        try:
            result = c.publish(topic, payload, qos=1)
            return result.rc == 0
        except Exception as e:
            logger.warning("[sandy_device] publish failed (%s): %s", topic, e)
            return False

    # Public API
    def set_mood(self, mood: str) -> bool:
        mood = (mood or "").strip().lower()
        if mood not in _VALID_MOODS:
            mood = "idle"
        return self._publish(_TOPIC_CMD_MOOD, mood)

    def set_mood_from_reaction(self, reaction: str) -> bool:
        mood = _REACTION_TO_MOOD.get((reaction or "").strip().lower(), "idle")
        return self.set_mood(mood)

    def set_servo(self, angle: int) -> bool:
        try:
            angle = max(5, min(175, int(angle)))
        except (TypeError, ValueError):
            return False
        return self._publish(_TOPIC_CMD_SERVO, str(angle))

    def play_buzzer(self, sound: str) -> bool:
        sound = (sound or "").strip().lower()
        if sound not in _VALID_BUZZER:
            sound = "alert"
        return self._publish(_TOPIC_CMD_BUZZER, sound)

    def move_base(self, action: str) -> bool:
        action = (action or "").strip().lower()
        if action not in _VALID_BASE:
            return False
        return self._publish(_TOPIC_CMD_BASE, action)

    def set_autonomous(self, on: bool) -> bool:
        return self._publish(_TOPIC_CMD_AUTONOMOUS, "true" if on else "false")

    def get_distance(self) -> Optional[float]:
        try:
            val = self._latest_status.get("distance_cm")
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def get_status(self) -> Optional[str]:
        return self._latest_status.get("status_text")

    def get_full_status(self) -> dict:
        """Latest status fields received from ESP."""
        return dict(self._latest_status)

    def is_online(self, max_age_sec: float = 30.0) -> bool:
        return self._last_seen > 0 and (time.time() - self._last_seen) < max_age_sec

    def sync_mood_async(self, mood_or_reaction: str):
        """Fire-and-forget mood sync. paho-mqtt publish is thread-safe — no extra lock needed."""
        if not self.available:
            return

        def _run():
            try:
                key = (mood_or_reaction or "").strip().lower()
                # Prefer direct mood if it's a valid Sandy mood; otherwise try reaction map.
                if key in _VALID_MOODS:
                    self.set_mood(key)
                elif key in _REACTION_TO_MOOD:
                    self.set_mood_from_reaction(key)
                else:
                    self.set_mood("idle")
            except Exception as e:
                logger.warning("[sandy_device] sync_mood_async failed: %s", e)

        threading.Thread(target=_run, daemon=True).start()


# singleton
_client: Optional[SandyDeviceClient] = None


def get_sandy_device_client() -> SandyDeviceClient:
    global _client
    if _client is None:
        _client = SandyDeviceClient()
        if _client.available:
            logger.info("[sandy_device] initializing MQTT to %s:%s", _client._host, _client._port)
            _client._ensure_client()
        else:
            logger.warning("[sandy_device] MQTT env vars missing or paho-mqtt not installed")
    return _client


# Backward-compat alias
def get_arduino_client() -> SandyDeviceClient:
    return get_sandy_device_client()
