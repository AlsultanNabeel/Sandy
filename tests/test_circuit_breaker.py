"""Tests for CircuitBreaker — focus on the optional timeout enforcement (B9)."""
import time

import pytest

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_no_timeout_runs_inline_and_succeeds():
    cb = CircuitBreaker(name="t1", failure_threshold=2)
    assert cb.call(lambda x: x + 1, 41) == 42
    assert cb.state == "CLOSED"


def test_fast_call_under_timeout_succeeds():
    cb = CircuitBreaker(name="t2", failure_threshold=2, timeout=1.0)
    assert cb.call(lambda: "ok") == "ok"
    assert cb.state == "CLOSED"


def test_slow_call_times_out_and_counts_as_failure():
    cb = CircuitBreaker(name="t3", failure_threshold=2, timeout=0.05)

    def slow():
        time.sleep(0.5)
        return "late"

    with pytest.raises(TimeoutError):
        cb.call(slow)
    assert cb._failure_count == 1
    assert cb.state == "CLOSED"  # one failure, threshold is 2

    # Second timeout trips the breaker OPEN.
    with pytest.raises(TimeoutError):
        cb.call(slow)
    assert cb.state == "OPEN"

    # While OPEN, calls fail fast without running fn.
    with pytest.raises(CircuitOpenError):
        cb.call(slow)


def test_error_still_trips_breaker_with_timeout_set():
    cb = CircuitBreaker(name="t4", failure_threshold=1, timeout=1.0)

    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        cb.call(boom)
    assert cb.state == "OPEN"
