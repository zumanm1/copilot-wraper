"""
Regression tests for the aiohttp ThreadedResolver + force_close fix in C1's
CopilotBackend._get_c3_session (copilot_backend.py).

Verifies:
  1. The connector uses ThreadedResolver (not aiodns).
  2. force_close=True is set on the connector.
  3. Concurrent calls to _get_c3_session return the same session (no race leak).
  4. A closed session is replaced on the next call (not reused).
  5. The session lock is acquired before creation, preventing double-creation.
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import patch, MagicMock


# ── helpers ──────────────────────────────────────────────────────────────────

def _reset_c3_session():
    """Reset class-level state between tests."""
    from copilot_backend import CopilotBackend
    CopilotBackend._c3_session = None
    CopilotBackend._c3_session_lock = None


# ── tests ─────────────────────────────────────────────────────────────────────

class TestGetC3Session:
    def setup_method(self):
        _reset_c3_session()

    def teardown_method(self):
        _reset_c3_session()

    async def test_uses_threaded_resolver(self):
        """Connector must use ThreadedResolver, not the default aiodns."""
        import aiohttp
        from copilot_backend import CopilotBackend

        created_connectors: list[aiohttp.TCPConnector] = []
        original_connector = aiohttp.TCPConnector

        def capturing_connector(**kwargs):
            c = original_connector(**kwargs)
            created_connectors.append(c)
            return c

        with patch("aiohttp.TCPConnector", side_effect=capturing_connector):
            session = await CopilotBackend._get_c3_session()

        assert len(created_connectors) == 1
        connector = created_connectors[0]
        # ThreadedResolver is set via _resolver attribute in aiohttp
        assert isinstance(connector._resolver, aiohttp.ThreadedResolver), (
            "Connector must use ThreadedResolver to avoid aiodns Docker DNS failure"
        )
        await session.close()

    async def test_force_close_is_set(self):
        """Connector must have force_close=True to prevent stale TCP reuse."""
        import aiohttp
        from copilot_backend import CopilotBackend

        session = await CopilotBackend._get_c3_session()
        connector = session.connector
        assert connector._force_close is True, (
            "force_close must be True to prevent stale TCP connections after C3 restarts"
        )
        await session.close()

    async def test_same_session_returned_on_repeated_calls(self):
        """Multiple awaits must return the identical session object (singleton)."""
        from copilot_backend import CopilotBackend

        s1 = await CopilotBackend._get_c3_session()
        s2 = await CopilotBackend._get_c3_session()
        assert s1 is s2, "Must return singleton session, not create a new one each call"
        await s1.close()

    async def test_closed_session_is_replaced(self):
        """If the singleton session is closed, next call must create a fresh one."""
        from copilot_backend import CopilotBackend

        s1 = await CopilotBackend._get_c3_session()
        await s1.close()
        assert s1.closed

        s2 = await CopilotBackend._get_c3_session()
        assert not s2.closed
        assert s1 is not s2, "Closed session must be replaced, not reused"
        await s2.close()

    async def test_concurrent_calls_produce_single_session(self):
        """Racing concurrent coroutines must not create two separate sessions
        (the E6 race condition guarded by _c3_session_lock)."""
        from copilot_backend import CopilotBackend

        results = await asyncio.gather(
            CopilotBackend._get_c3_session(),
            CopilotBackend._get_c3_session(),
            CopilotBackend._get_c3_session(),
        )
        # All three must be the exact same object
        assert results[0] is results[1] is results[2], (
            "Concurrent _get_c3_session calls must return the same singleton (lock broken)"
        )
        await results[0].close()

    async def test_lock_is_created_lazily_and_reused(self):
        """_get_c3_session_lock must return the same Lock on repeated calls."""
        from copilot_backend import CopilotBackend

        lock1 = CopilotBackend._get_c3_session_lock()
        lock2 = CopilotBackend._get_c3_session_lock()
        assert lock1 is lock2, "Lock must be a singleton (lazy-created, not recreated)"
