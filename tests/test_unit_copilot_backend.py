"""
Unit tests for CopilotBackend and CopilotConnectionPool.
SydneyClient is mocked at the module boundary.
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_sydney():
    client = AsyncMock()
    client.start_conversation = AsyncMock()
    client.close_conversation = AsyncMock()
    client.reset_conversation = AsyncMock()
    client.ask = AsyncMock(return_value="test response")

    async def _stream(*a, **kw):
        for t in ["tok1", "tok2", "tok3"]:
            yield t

    client.ask_stream = _stream
    return client


@pytest.fixture
def backend(mock_sydney):
    with patch("copilot_backend.SydneyClient", return_value=mock_sydney):
        from copilot_backend import CopilotBackend
        b = CopilotBackend(bing_cookies="test")
        return b, mock_sydney


# ── _get_client() ────────────────────────────────────────────────────

async def test_get_client_calls_start_conversation(backend):
    b, sydney = backend
    client = await b._get_client()
    sydney.start_conversation.assert_called_once()
    assert client is sydney


async def test_get_client_is_idempotent(backend):
    b, sydney = backend
    c1 = await b._get_client()
    c2 = await b._get_client()
    assert c1 is c2
    sydney.start_conversation.assert_called_once()


# ── chat_completion() ────────────────────────────────────────────────

async def test_chat_completion_returns_response(backend):
    b, sydney = backend
    result = await b.chat_completion("Hello")
    assert result == "test response"
    sydney.ask.assert_called_once()


async def test_chat_completion_caches_response(backend):
    b, sydney = backend
    await b.chat_completion("Unique prompt xyz")
    await b.chat_completion("Unique prompt xyz")
    # Second call should hit cache — only one call to sydney.ask
    sydney.ask.assert_called_once()


async def test_chat_completion_no_cache_for_image(backend):
    b, sydney = backend
    import tempfile, os
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        await b.chat_completion("prompt", attachment_path=path)
        await b.chat_completion("prompt", attachment_path=path)
        # Image requests never cached — should call ask twice
        assert sydney.ask.call_count == 2
    finally:
        os.unlink(path)


async def test_chat_completion_timeout_raises(backend):
    b, sydney = backend
    sydney.ask = AsyncMock(side_effect=asyncio.TimeoutError())
    with patch("copilot_backend.config.REQUEST_TIMEOUT", 1):
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await b.chat_completion("slow prompt")


async def test_chat_completion_exception_resets_client(backend):
    b, sydney = backend
    sydney.ask = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await b.chat_completion("boom prompt")
    assert b._client is None


# ── chat_completion_stream() ─────────────────────────────────────────

async def test_stream_yields_tokens(backend):
    b, _ = backend
    tokens = []
    async for t in b.chat_completion_stream("hi"):
        tokens.append(t)
    assert tokens == ["tok1", "tok2", "tok3"]


# ── reset_conversation() ─────────────────────────────────────────────

async def test_reset_conversation_called(backend):
    b, sydney = backend
    await b._get_client()
    await b.reset_conversation()
    sydney.reset_conversation.assert_called_once()


async def test_reset_conversation_noop_when_no_client(backend):
    b, sydney = backend
    await b.reset_conversation()  # no _get_client() called first
    sydney.reset_conversation.assert_not_called()


# ── close() ─────────────────────────────────────────────────────────

async def test_close_nulls_client(backend):
    b, sydney = backend
    await b._get_client()
    await b.close()
    sydney.close_conversation.assert_called_once()
    assert b._client is None


async def test_close_is_safe_when_no_client(backend):
    b, sydney = backend
    await b.close()  # no _get_client() first
    sydney.close_conversation.assert_not_called()


# ── CopilotConnectionPool ────────────────────────────────────────────

async def test_pool_acquire_creates_backend(mock_sydney):
    with patch("copilot_backend.SydneyClient", return_value=mock_sydney):
        from copilot_backend import CopilotConnectionPool
        pool = CopilotConnectionPool(max_connections=2)
        b = await pool.acquire()
        assert b is not None
        mock_sydney.start_conversation.assert_called_once()


async def test_pool_release_resets_and_returns(mock_sydney):
    with patch("copilot_backend.SydneyClient", return_value=mock_sydney):
        from copilot_backend import CopilotConnectionPool, CopilotBackend
        pool = CopilotConnectionPool(max_connections=2)
        b = CopilotBackend()
        b._client = mock_sydney
        await pool.release(b)
        # reset_conversation should have been called
        mock_sydney.reset_conversation.assert_called_once()
        # backend should be in pool
        assert len(pool._connections) == 1


async def test_pool_release_discards_when_full(mock_sydney):
    with patch("copilot_backend.SydneyClient", return_value=mock_sydney):
        from copilot_backend import CopilotConnectionPool, CopilotBackend
        pool = CopilotConnectionPool(max_connections=1)
        # Fill pool to capacity
        b1 = CopilotBackend(); b1._client = mock_sydney
        pool._connections.append(b1)
        # Now release another — should be discarded (pool full)
        b2 = CopilotBackend(); b2._client = AsyncMock()
        b2._client.reset_conversation = AsyncMock()
        b2._client.close_conversation = AsyncMock()
        await pool.release(b2)
        assert len(pool._connections) == 1  # still 1, b2 was closed


async def test_pool_close_all_empties_pool(mock_sydney):
    with patch("copilot_backend.SydneyClient", return_value=mock_sydney):
        from copilot_backend import CopilotConnectionPool, CopilotBackend
        pool = CopilotConnectionPool()
        b = CopilotBackend(); b._client = mock_sydney
        pool._connections.append(b)
        await pool.close_all()
        assert len(pool._connections) == 0
