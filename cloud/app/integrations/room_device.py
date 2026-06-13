"""Room Device Client — MQTT control for the room node (lights/music/fan/curtain).

A SECOND physical device on the SAME HiveMQ broker as the robot body
(`sandy_device.py`). The room node is the old classic ESP32 (`sandy/` firmware),
slated to drive the room. This client is **publish-only** for now — it fires
`room/cmd/*` commands and is a graceful no-op when MQTT isn't configured, so
scenes can be wired and tested before the hardware is online.

Topics (the cloud↔room-node contract — keep firmware in sync):
    room/cmd/light    — "on" | "off" | "0".."100"  (brightness %)
    room/cmd/color    — "warm" | "cool" | "white" | "red" | "green" | "blue" |
                        "purple" | "amber"  (or "#rrggbb")
    room/cmd/music    — "on" | "off" | "pause"
    room/cmd/fan      — "on" | "off" | "0".."100"
    room/cmd/curtain  — "open" | "close"
    room/cmd/scene    — "<scene name>"  (optional: let the node run a named scene
                        locally; the cloud also sends the individual commands)

Reuses the robot's broker creds (in .env — never commit):
    SANDY_MQTT_HOST / SANDY_MQTT_PORT / SANDY_MQTT_USER / SANDY_MQTT_PASS
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt  # type: ignore
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

_TOPIC_LIGHT   = "room/cmd/light"
_TOPIC_COLOR   = "room/cmd/color"
_TOPIC_MUSIC   = "room/cmd/music"
_TOPIC_FAN     = "room/cmd/fan"
_TOPIC_CURTAIN = "room/cmd/curtain"
_TOPIC_SCENE   = "room/cmd/scene"

# device name → topic. Used by scene_store to validate + route actions.
_DEVICE_TOPIC = {
    "light":   _TOPIC_LIGHT,
    "color":   _TOPIC_COLOR,
    "music":   _TOPIC_MUSIC,
    "fan":     _TOPIC_FAN,
    "curtain": _TOPIC_CURTAIN,
    "scene":   _TOPIC_SCENE,
}

VALID_DEVICES = frozenset(_DEVICE_TOPIC)
_VALID_COLOR = {"warm", "cool", "white", "red", "green", "blue", "purple", "amber"}


def normalize_action(device: str, value: str) -> Optional[str]:
    """Return a clean payload for (device, value), or None if invalid.

    Light/fan accept on|off or a 0..100 brightness/speed; color accepts a named
    color or #rrggbb; music accepts on|off|pause; curtain open|close.
    """
    device = (device or "").strip().lower()
    value = str(value or "").strip().lower()
    if device not in _DEVICE_TOPIC or not value:
        return None
    if device in ("light", "fan"):
        if value in ("on", "off"):
            return value
        try:
            return str(max(0, min(100, int(value))))
        except ValueError:
            return None
    if device == "color":
        if value in _VALID_COLOR:
            return value
        if value.startswith("#") and len(value) == 7:
            return value
        return None
    if device == "music":
        return value if value in ("on", "off", "pause") else None
    if device == "curtain":
        return value if value in ("open", "close") else None
    if device == "scene":
        return value
    return None


class RoomDeviceClient:
    """Publish-only MQTT client for the room node. No-op when unconfigured."""

    def __init__(self):
        self._host = os.getenv("SANDY_MQTT_HOST", "").strip()
        self._user = os.getenv("SANDY_MQTT_USER", "").strip()
        self._pass = os.getenv("SANDY_MQTT_PASS", "").strip()
        try:
            self._port = int(os.getenv("SANDY_MQTT_PORT", "8883"))
        except ValueError:
            self._port = 8883
        self._client: Optional[Any] = None
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return MQTT_AVAILABLE and bool(self._host and self._user and self._pass)

    def _ensure_client(self) -> Optional[Any]:
        if not self.available:
            return None
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                c = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"sandy-room-{os.getpid()}",
                    clean_session=False,
                )
                c.username_pw_set(self._user, self._pass)
                c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
                c.connect(self._host, self._port, keepalive=60)
                c.loop_start()
                self._client = c
                return c
            except Exception as e:  # noqa: BLE001
                logger.warning("[room_device] MQTT connect failed: %s", e)
                self._client = None
                return None

    def _publish(self, topic: str, payload: str) -> bool:
        c = self._ensure_client()
        if c is None:
            return False
        try:
            return c.publish(topic, payload, qos=1).rc == 0
        except Exception as e:  # noqa: BLE001
            logger.warning("[room_device] publish failed (%s): %s", topic, e)
            return False

    def send(self, device: str, value: str) -> bool:
        """Send one normalized command. Returns False if invalid or offline."""
        payload = normalize_action(device, value)
        if payload is None:
            return False
        topic = _DEVICE_TOPIC[(device or "").strip().lower()]
        return self._publish(topic, payload)

    def apply_actions(self, actions: List[Dict[str, str]]) -> Dict[str, Any]:
        """Send a list of [{device, value}]. Returns how many reached the broker."""
        sent, skipped = [], []
        for a in actions or []:
            dev, val = a.get("device", ""), a.get("value", "")
            (sent if self.send(dev, val) else skipped).append({"device": dev, "value": val})
        return {"available": self.available, "sent": sent, "skipped": skipped}


_room_client: Optional[RoomDeviceClient] = None


def get_room_device_client() -> RoomDeviceClient:
    global _room_client
    if _room_client is None:
        _room_client = RoomDeviceClient()
    return _room_client
