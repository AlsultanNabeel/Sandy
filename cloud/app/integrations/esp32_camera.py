"""ESP32-CAM client — MQTT-based via SandyDeviceClient.

API compatible with the old HTTP client so dispatch handlers don't change.
"""

from __future__ import annotations

from typing import Optional

from app.integrations.sandy_device import get_sandy_device_client
import logging

logger = logging.getLogger(__name__)


class ESP32CameraClient:
    """Thin wrapper over the shared MQTT client. Snapshots travel as chunked
    MQTT messages on `sandy/cam/snapshot` and are reassembled cloud-side."""

    @property
    def available(self) -> bool:
        return get_sandy_device_client().available

    def capture_snapshot(self, timeout_sec: float = 15.0) -> Optional[bytes]:
        """Publish a snapshot request and wait for the assembled JPEG bytes."""
        return get_sandy_device_client().request_snapshot(timeout_sec=timeout_sec)

    def set_power(self, on: bool) -> bool:
        # Camera firmware auto-wakes on request — power command is a no-op now.
        return True

    def get_status(self) -> Optional[dict]:
        client = get_sandy_device_client()
        return dict(client._cam_latest_status) if client._cam_latest_status else None


_client: Optional[ESP32CameraClient] = None


def get_camera_client() -> ESP32CameraClient:
    global _client
    if _client is None:
        _client = ESP32CameraClient()
        logger.info("[Camera] client initialized (MQTT-based)")
    return _client
