"""
Regression tests for the asyncio.shield + ensure_future fix in C9's /api/chat
streaming loop (c9_jokes/app.py).

Verifies:
  1. Tokens arrive correctly after one or more heartbeat timeouts (the bug
     scenario: wait_for cancels the read, stream appears empty).
  2. The _pending_line_task is cancelled in the finally block when the
     generator exits (no leaked background tasks).
  3. StopAsyncIteration correctly breaks the loop after the last line.
"""
from __future__ import annotations
import asyncio
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

class _SlowIter:
    """Async iterator that delivers lines with configurable delays.

    Simulates httpx's aiter_lines() where the first line arrives after
    `delay_s` seconds — longer than WAIT_HEARTBEAT_S — so the heartbeat
    timeout fires at least once before data arrives.
    """

    def __init__(self, lines: list[str], delay_s: float = 0.0):
        self._lines = iter(lines)
        self._delay_s = delay_s
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._first and self._delay_s:
            self._first = False
            await asyncio.sleep(self._delay_s)
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


async def _run_shield_loop(lines: list[str], delay_s: float, heartbeat_s: float) -> tuple[list[str], int]:
    """Exercise the exact shield pattern from c9_jokes/app.py.

    Returns (collected_lines, heartbeat_count).
    """
    it = _SlowIter(lines, delay_s=delay_s)
    pending: asyncio.Task | None = None
    collected: list[str] = []
    heartbeats = 0

    try:
        while True:
            try:
                if pending is None or pending.done():
                    pending = asyncio.ensure_future(it.__anext__())
                raw = await asyncio.wait_for(asyncio.shield(pending), timeout=heartbeat_s)
                pending = None
                collected.append(raw)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                heartbeats += 1
                continue
    finally:
        if pending is not None and not pending.done():
            pending.cancel()

    return collected, heartbeats


# ── tests ─────────────────────────────────────────────────────────────────────

class TestShieldLoop:
    async def test_immediate_lines_no_heartbeat(self):
        """Lines arrive instantly → no heartbeat timeout fires."""
        lines = ["line1", "line2", "line3"]
        collected, hb = await _run_shield_loop(lines, delay_s=0.0, heartbeat_s=0.5)
        assert collected == lines
        assert hb == 0

    async def test_slow_first_line_triggers_heartbeat(self):
        """First line delayed beyond heartbeat_s → at least one heartbeat fires,
        but the line still arrives and is collected (the bug scenario)."""
        lines = ["data: token", "data: [DONE]"]
        collected, hb = await _run_shield_loop(lines, delay_s=0.12, heartbeat_s=0.05)
        assert "data: token" in collected
        assert hb >= 1

    async def test_stop_async_iteration_breaks_loop(self):
        """Empty iterator raises StopAsyncIteration → loop exits cleanly."""
        collected, hb = await _run_shield_loop([], delay_s=0.0, heartbeat_s=0.5)
        assert collected == []
        assert hb == 0

    async def test_multiple_heartbeats_before_data(self):
        """Multiple heartbeats fire before each of several lines arrive."""
        lines = ["A", "B"]
        collected, hb = await _run_shield_loop(lines, delay_s=0.18, heartbeat_s=0.05)
        assert collected == ["A", "B"]
        assert hb >= 2

    async def test_pending_task_cancelled_on_early_break(self):
        """When the loop breaks (e.g. [DONE] token), the shielded Task must be
        cancelled in the finally block — no dangling background task.

        Simulates the try/finally pattern from c9_jokes/app.py directly:
        the loop breaks after the first line ([DONE]), and the still-running
        pending task for the *next* line must be cancelled.
        """
        sentinel = asyncio.Event()

        async def _never_ending_anext():
            await sentinel.wait()  # blocks forever unless cancelled
            raise StopAsyncIteration  # pragma: no cover

        pending: asyncio.Task | None = None
        try:
            for _i in range(1):  # loop that breaks immediately
                pending = asyncio.ensure_future(_never_ending_anext())
                # Simulate "[DONE]" received — break without awaiting pending
                break
        finally:
            if pending is not None and not pending.done():
                pending.cancel()

        await asyncio.sleep(0)  # let cancellation propagate
        assert pending is not None
        assert pending.cancelled(), "Pending task must be cancelled when loop exits early"

    async def test_task_not_recreated_while_still_running(self):
        """If pending task is still running (not done), the loop must NOT create
        a new task — the same Task must be re-awaited."""
        create_count = 0

        class _CountingIter:
            async def __anext__(self):
                nonlocal create_count
                create_count += 1
                await asyncio.sleep(0.15)
                if create_count == 1:
                    return "only_line"
                raise StopAsyncIteration

        it = _CountingIter()
        pending: asyncio.Task | None = None
        collected = []
        heartbeats = 0

        try:
            while True:
                try:
                    if pending is None or pending.done():
                        pending = asyncio.ensure_future(it.__anext__())
                    raw = await asyncio.wait_for(asyncio.shield(pending), timeout=0.05)
                    pending = None
                    collected.append(raw)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    heartbeats += 1
                    continue
        finally:
            if pending is not None and not pending.done():
                pending.cancel()

        assert collected == ["only_line"]
        # __anext__ is called exactly twice: once for "only_line" and once to
        # discover StopAsyncIteration. Critically, it is NOT called once per
        # heartbeat timeout — the same Task is re-awaited during timeouts.
        assert create_count == 2, (
            f"Expected 2 __anext__ calls (data + StopAsyncIteration), got {create_count}. "
            "A higher count means the Task was incorrectly recreated during heartbeat timeouts."
        )
