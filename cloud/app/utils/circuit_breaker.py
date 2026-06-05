"""Circuit Breaker pattern for resilient service calls.

States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing) → CLOSED
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Dedicated pool for timeout-enforced calls. Kept separate from the shared
# `sandy_executor` so a hung provider can't starve fire-and-forget work.
_timeout_pool: Optional[ThreadPoolExecutor] = None
_timeout_pool_lock = threading.Lock()


def _get_timeout_pool() -> ThreadPoolExecutor:
    global _timeout_pool
    if _timeout_pool is None:
        with _timeout_pool_lock:
            if _timeout_pool is None:
                _timeout_pool = ThreadPoolExecutor(
                    max_workers=8, thread_name_prefix="cb-timeout"
                )
    return _timeout_pool


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is in OPEN state."""


class CircuitBreaker:
    """Thread-safe circuit breaker with automatic recovery.

    Usage:
        cb = CircuitBreaker(name="openai", failure_threshold=5, recovery_timeout=60.0)
        try:
            result = cb.call(my_fn, arg1, arg2)
        except CircuitOpenError:
            result = fallback_value
    """

    _CLOSED = "CLOSED"
    _OPEN = "OPEN"
    _HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        timeout: Optional[float] = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        # When set, a call slower than `timeout` seconds counts as a failure so
        # the breaker can OPEN on slowness, not just on errors. None = no limit.
        self.timeout = timeout

        self._state = self._CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def _should_attempt(self) -> bool:
        with self._lock:
            if self._state == self._CLOSED:
                return True
            if self._state == self._OPEN:
                elapsed = time.monotonic() - (self._last_failure_time or 0)
                if elapsed >= self.recovery_timeout:
                    self._state = self._HALF_OPEN
                    logger.info(
                        f"[CB:{self.name}] → HALF_OPEN (probing after {elapsed:.0f}s)"
                    )
                    return True
                return False
            # HALF_OPEN: allow one probe
            return True

    def _on_success(self) -> None:
        with self._lock:
            if self._state != self._CLOSED:
                logger.info(f"[CB:{self.name}] → CLOSED (recovered)")
            self._state = self._CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if (
                self._state == self._HALF_OPEN
                or self._failure_count >= self.failure_threshold
            ):
                self._state = self._OPEN
                exc_summary = str(exc).splitlines()[0][:80] if str(exc) else type(exc).__name__
                logger.warning(
                    f"[CB:{self.name}] → OPEN after {self._failure_count} failures: {exc_summary}"
                )

    def _invoke(self, fn: Callable[..., Any], args: Any, kwargs: Any) -> Any:
        """Run fn, enforcing self.timeout when set (slow call → TimeoutError)."""
        if self.timeout is None:
            return fn(*args, **kwargs)
        future = _get_timeout_pool().submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=self.timeout)
        except FuturesTimeoutError as exc:
            # The thread can't be cancelled, but the caller gets control back and
            # the breaker records the slowness as a failure (counted once below).
            future.cancel()
            raise TimeoutError(
                f"Circuit '{self.name}' call exceeded {self.timeout:.1f}s"
            ) from exc

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute fn through the breaker; raises CircuitOpenError when OPEN."""
        if not self._should_attempt():
            raise CircuitOpenError(
                f"Circuit '{self.name}' is OPEN — service unavailable"
            )
        try:
            result = self._invoke(fn, args, kwargs)
            self._on_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            self._on_failure(exc)
            raise
