"""
circuit_breaker.py
==================
Lightweight async circuit breaker for the Copilot backend.

States
------
CLOSED   → Normal operation. Failures are counted. If consecutive failures
           exceed `threshold`, trip to OPEN.
OPEN     → All requests are rejected immediately with CircuitOpenError.
           After `timeout_seconds`, one probe is allowed (→ HALF_OPEN).
HALF_OPEN → One test request is in flight. Success → CLOSED, failure → OPEN.

Usage
-----
    cb = CircuitBreaker(threshold=5, timeout_seconds=60, name="copilot")

    try:
        result = await cb.call(my_async_function, arg1, arg2)
    except CircuitOpenError:
        # Fast-fail: backend is known-bad, don't waste a WebSocket slot
        raise HTTPException(503, "Copilot backend temporarily unavailable")

Thread / coroutine safety
--------------------------
State transitions are guarded by an asyncio.Lock.  The lock is held only
for state reads/writes — never across the actual network call — so it
never becomes a bottleneck.
"""
from __future__ import annotations

import asyncio
import time
import logging
from enum import Enum
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit is OPEN and a request is rejected."""


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 5,
        timeout_seconds: float = 60.0,
        name: str = "default",
    ) -> None:
        self.threshold       = threshold
        self.timeout_seconds = timeout_seconds
        self.name            = name

        self._state          = CircuitState.CLOSED
        self._failure_count  = 0
        self._opened_at: float | None = None
        self._lock           = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def call(
        self,
        coro_func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute `coro_func(*args, **kwargs)` under circuit-breaker protection.

        Raises CircuitOpenError immediately if the circuit is OPEN.
        """
        await self._before_call()
        try:
            result = await coro_func(*args, **kwargs)
            await self._on_success()
            return result
        except CircuitOpenError:
            raise  # already handled in _before_call
        except Exception as exc:
            await self._on_failure(exc)
            raise

    def get_status(self) -> dict:
        """Return a status dict suitable for a /health or /debug endpoint."""
        return {
            "name":            self.name,
            "state":           self._state.value,
            "failure_count":   self._failure_count,
            "threshold":       self.threshold,
            "timeout_seconds": self.timeout_seconds,
            "opened_at":       self._opened_at,
            "seconds_until_probe": (
                max(0.0, self.timeout_seconds - (time.monotonic() - self._opened_at))
                if self._opened_at and self._state == CircuitState.OPEN
                else None
            ),
        }

    # ── Internal state machine ────────────────────────────────────────────────

    async def _before_call(self) -> None:
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return  # normal path — no action needed

            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0)
                if elapsed < self.timeout_seconds:
                    raise CircuitOpenError(
                        f"Circuit '{self.name}' is OPEN "
                        f"({self.timeout_seconds - elapsed:.0f}s until probe)"
                    )
                # Timeout expired: allow one probe
                self._state = CircuitState.HALF_OPEN
                log.warning("[circuit_breaker] '%s' → HALF_OPEN (probing)", self.name)

            # HALF_OPEN: allow this one call through; no state change here

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
                if self._failure_count > 0 or self._state == CircuitState.HALF_OPEN:
                    log.info(
                        "[circuit_breaker] '%s' → CLOSED (recovered after %d failures)",
                        self.name, self._failure_count,
                    )
                self._state         = CircuitState.CLOSED
                self._failure_count = 0
                self._opened_at     = None

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — go back to OPEN immediately
                self._state     = CircuitState.OPEN
                self._opened_at = time.monotonic()
                log.error(
                    "[circuit_breaker] '%s' probe FAILED → OPEN. Error: %s",
                    self.name, exc,
                )
                return

            if self._failure_count >= self.threshold:
                self._state     = CircuitState.OPEN
                self._opened_at = time.monotonic()
                log.error(
                    "[circuit_breaker] '%s' TRIPPED → OPEN after %d failures. "
                    "Will probe in %ds. Last error: %s",
                    self.name, self._failure_count, self.timeout_seconds, exc,
                )
            else:
                log.warning(
                    "[circuit_breaker] '%s' failure %d/%d: %s",
                    self.name, self._failure_count, self.threshold, exc,
                )

    async def reset(self) -> None:
        """Manually reset the circuit to CLOSED (e.g. after config reload)."""
        async with self._lock:
            self._state         = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at     = None
            log.info("[circuit_breaker] '%s' manually reset → CLOSED", self.name)


# ── Module-level singleton ────────────────────────────────────────────────────
# Shared across all CopilotBackend instances via copilot_backend.py.
# Protected by a threading lock (not asyncio.Lock) so it's safe during module
# import, which happens in the main thread before the event loop starts.

import threading
_cb_lock: threading.Lock = threading.Lock()
_copilot_breaker: CircuitBreaker | None = None


def get_circuit_breaker(threshold: int = 5, timeout_seconds: float = 60.0) -> CircuitBreaker:
    global _copilot_breaker
    if _copilot_breaker is None:
        with _cb_lock:
            # Double-checked locking — safe against concurrent first-call
            if _copilot_breaker is None:
                _copilot_breaker = CircuitBreaker(
                    threshold=threshold,
                    timeout_seconds=timeout_seconds,
                    name="copilot",
                )
    return _copilot_breaker
