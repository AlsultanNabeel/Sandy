"""Grafana Cloud Prometheus remote_write pusher.

Heroku doesn't let Grafana Cloud scrape `/metrics` directly — Grafana Cloud
Prometheus is pull-only and our dyno is behind Heroku's routing layer. This
module solves that by *pushing* metrics from Sandy to Grafana Cloud every
N seconds using Prometheus's standard remote_write protocol (snappy-
compressed protobuf, basic-auth).

The protobuf encoding is hand-rolled to avoid a build step and a protoc dep.
The remote_write schema is small (4 messages, 2 levels deep) so it fits in
about 50 lines.

Configuration (env vars, all optional — if any is missing, pushing is off):
    GRAFANA_CLOUD_REMOTE_WRITE_URL  e.g. https://prometheus-prod-XX.grafana.net/api/prom/push
    GRAFANA_CLOUD_USERNAME          numeric instance id from Grafana Cloud
    GRAFANA_CLOUD_API_KEY           a publisher API key
    GRAFANA_CLOUD_PUSH_INTERVAL_S   override push cadence (default 30)
    GRAFANA_CLOUD_EXTRA_LABEL       optional, format "key=value" — added to
                                    every series (useful for env/instance)
"""

from __future__ import annotations

import logging
import os
import struct
import threading
import time
from typing import Iterable, List, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 30
_PUSH_TIMEOUT_S = 10
_pusher_thread: threading.Thread | None = None


# Protobuf wire-format helpers.
# Wire types: 0 = varint, 1 = 64-bit fixed, 2 = length-delimited

def _varint(value: int) -> bytes:
    out = bytearray()
    v = value
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _string_field(field_number: int, value: str) -> bytes:
    data = value.encode("utf-8")
    return _tag(field_number, 2) + _varint(len(data)) + data


def _bytes_field(field_number: int, data: bytes) -> bytes:
    return _tag(field_number, 2) + _varint(len(data)) + data


def _double_field(field_number: int, value: float) -> bytes:
    # Fixed64, little-endian IEEE-754 double
    return _tag(field_number, 1) + struct.pack("<d", float(value))


def _int64_field(field_number: int, value: int) -> bytes:
    return _tag(field_number, 0) + _varint(value)


# Prometheus remote_write message encoders
def _encode_label(name: str, value: str) -> bytes:
    return _string_field(1, name) + _string_field(2, value)


def _encode_sample(value: float, timestamp_ms: int) -> bytes:
    return _double_field(1, value) + _int64_field(2, timestamp_ms)


def _encode_timeseries(
    labels: List[Tuple[str, str]], value: float, timestamp_ms: int
) -> bytes:
    parts: List[bytes] = []
    for name, val in labels:
        parts.append(_bytes_field(1, _encode_label(name, val)))
    parts.append(_bytes_field(2, _encode_sample(value, timestamp_ms)))
    return b"".join(parts)


def _encode_write_request(series: Iterable[bytes]) -> bytes:
    return b"".join(_bytes_field(1, s) for s in series)


# Collecting metrics from prometheus_client
def _collect_samples(extra_labels: List[Tuple[str, str]]) -> List[bytes]:
    """Walk the global registry and return encoded TimeSeries blobs."""
    try:
        from prometheus_client import REGISTRY
    except ImportError:
        return []

    now_ms = int(time.time() * 1000)
    series: List[bytes] = []
    for family in REGISTRY.collect():
        for sample in family.samples:
            # sample = (name, labels_dict, value, timestamp_or_None, exemplar)
            value = sample.value
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            labels: List[Tuple[str, str]] = [("__name__", sample.name)]
            for k, v in (sample.labels or {}).items():
                labels.append((k, str(v)))
            labels.extend(extra_labels)
            series.append(_encode_timeseries(labels, value, now_ms))
    return series


# Public API
def _parse_extra_label(raw: str) -> List[Tuple[str, str]]:
    if not raw or "=" not in raw:
        return []
    k, v = raw.split("=", 1)
    k = k.strip()
    v = v.strip()
    return [(k, v)] if k and v else []


def _push_once(
    url: str,
    username: str,
    api_key: str,
    extra_labels: List[Tuple[str, str]],
) -> bool:
    series = _collect_samples(extra_labels)
    if not series:
        return False
    try:
        import snappy  # python-snappy
    except ImportError:
        logger.warning("[metrics_push] python-snappy missing — push disabled")
        return False
    try:
        import requests
    except ImportError:
        logger.warning("[metrics_push] requests missing — push disabled")
        return False

    body = _encode_write_request(series)
    compressed = snappy.compress(body)
    try:
        resp = requests.post(
            url,
            data=compressed,
            headers={
                "Content-Encoding": "snappy",
                "Content-Type": "application/x-protobuf",
                "X-Prometheus-Remote-Write-Version": "0.1.0",
                "User-Agent": "sandy-metrics-push/0.1",
            },
            auth=(username, api_key),
            timeout=_PUSH_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("[metrics_push] HTTP error: %s", exc)
        return False
    if resp.status_code >= 300:
        logger.warning(
            "[metrics_push] non-2xx from Grafana Cloud: %d %s",
            resp.status_code,
            resp.text[:200],
        )
        return False
    return True


def _pusher_loop(
    url: str,
    username: str,
    api_key: str,
    interval_s: int,
    extra_labels: List[Tuple[str, str]],
) -> None:
    logger.info("[metrics_push] started — interval=%ss url=%s", interval_s, url[:60])
    while True:
        ok = _push_once(url, username, api_key, extra_labels)
        if not ok:
            # Back off a bit on failure but stay in the loop
            time.sleep(min(interval_s * 2, 120))
        else:
            time.sleep(interval_s)


def start_metrics_pusher() -> bool:
    """Start the background pusher if Grafana Cloud creds are configured.

    Idempotent: a second call is a no-op while the first thread is alive.
    Returns True if a pusher is running (started by this call or already),
    False if the config is missing or required deps aren't available.
    """
    global _pusher_thread
    if _pusher_thread is not None and _pusher_thread.is_alive():
        return True

    url = os.getenv("GRAFANA_CLOUD_REMOTE_WRITE_URL", "").strip()
    username = os.getenv("GRAFANA_CLOUD_USERNAME", "").strip()
    api_key = os.getenv("GRAFANA_CLOUD_API_KEY", "").strip()
    if not (url and username and api_key):
        logger.info("[metrics_push] config missing — skipping pusher")
        return False

    try:
        interval_s = int(os.getenv("GRAFANA_CLOUD_PUSH_INTERVAL_S", _DEFAULT_INTERVAL_S))
    except ValueError:
        interval_s = _DEFAULT_INTERVAL_S
    interval_s = max(10, min(interval_s, 600))

    extra_labels = _parse_extra_label(os.getenv("GRAFANA_CLOUD_EXTRA_LABEL", ""))

    _pusher_thread = threading.Thread(
        target=_pusher_loop,
        args=(url, username, api_key, interval_s, extra_labels),
        name="sandy-metrics-pusher",
        daemon=True,
    )
    _pusher_thread.start()
    return True
