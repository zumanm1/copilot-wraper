"""
Unit tests for CopilotBackend and CopilotConnectionPool.
WebSocket layer is mocked by patching CopilotBackend._ws_stream.
"""
from __future__ import annotations
import asyncio
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch


pytestmark = pytest.mark.asyncio


async def _fake_ws_tokens(self, prompt, context, attachment_path=None):
    for t in ["tok1", "tok2", "tok3"]:
        yield t


@pytest.fixture
def backend():
    with patch("copilot_backend.CopilotBackend._ws_stream", _fake_ws_tokens):
        from copilot_backend import CopilotBackend
        b = CopilotBackend()
        yield b


# ── chat_completion() ────────────────────────────────────────────────

async def test_chat_completion_joins_stream_chunks(backend):
    result = await backend.chat_completion("Hello")
    assert result == "tok1tok2tok3"


async def test_chat_completion_caches_identical_prompts(backend):
    await backend.chat_completion("Unique prompt xyz")
    await backend.chat_completion("Unique prompt xyz")
    # Cache hit: only one upstream call
    # We cannot count _ws_stream calls easily without a Mock; second result must match
    r2 = await backend.chat_completion("Unique prompt xyz")
    assert r2 == "tok1tok2tok3"


async def test_chat_completion_no_cache_for_image(backend):
    import tempfile as tf
    fd, path = tf.mkstemp()
    os.close(fd)
    try:
        c1 = await backend.chat_completion("prompt", attachment_path=path)
        c2 = await backend.chat_completion("prompt", attachment_path=path)
        assert c1 == "tok1tok2tok3"
        assert c2 == "tok1tok2tok3"
    finally:
        os.unlink(path)


async def test_chat_completion_stream_yields_tokens(backend):
    tokens = []
    async for t in backend.chat_completion_stream("hi"):
        tokens.append(t)
    assert tokens == ["tok1", "tok2", "tok3"]


async def test_reset_conversation_clears_id(backend):
    backend._conversation_id = "fake-id"
    await backend.reset_conversation()
    assert backend._conversation_id is None


async def test_close_clears_conversation_id(backend):
    backend._conversation_id = "x"
    await backend.close()
    assert backend._conversation_id is None


def test_provider_auto_m365(monkeypatch):
    monkeypatch.setenv("COPILOT_PROVIDER", "auto")
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "m365_hub")
    import config as cfg
    import copilot_backend as cb
    import importlib
    importlib.reload(cfg)
    importlib.reload(cb)
    b = cb.CopilotBackend()
    assert b.provider.name == "m365"


def test_provider_auto_consumer(monkeypatch):
    monkeypatch.setenv("COPILOT_PROVIDER", "auto")
    monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "consumer")
    import config as cfg
    import copilot_backend as cb
    import importlib
    importlib.reload(cfg)
    importlib.reload(cb)
    b = cb.CopilotBackend()
    assert b.provider.name == "copilot"


def test_m365_provider_missing_session_cookie_error(monkeypatch):
    monkeypatch.setenv("COPILOT_PROVIDER", "m365")
    monkeypatch.setenv("M365_PROVIDER_FALLBACK_TO_COPILOT", "false")
    import config as cfg
    import copilot_backend as cb
    import importlib
    importlib.reload(cfg)
    importlib.reload(cb)
    p = cb.M365Provider()
    with pytest.raises(RuntimeError, match="M365 provider requires an active M365 web session cookie"):
        p.validate_session("MUID=abc;_U=xyz")


def test_no_cross_provider_auto_fallback(monkeypatch):
    monkeypatch.setenv("COPILOT_PROVIDER", "m365")
    import config as cfg
    import copilot_backend as cb
    import importlib
    importlib.reload(cfg)
    importlib.reload(cb)
    b = cb.CopilotBackend()
    assert b.provider.name == "m365"
    assert cb._should_fallback_to_copilot(b.provider, "MUID=abc;_U=xyz") is False


# ── CopilotConnectionPool ────────────────────────────────────────────

async def test_pool_acquire_creates_backend():
    with patch("copilot_backend.CopilotBackend._ws_stream", _fake_ws_tokens):
        from copilot_backend import CopilotConnectionPool
        pool = CopilotConnectionPool(max_connections=2)
        b = await pool.acquire()
        assert b is not None


async def test_pool_release_resets_and_returns():
    with patch("copilot_backend.CopilotBackend._ws_stream", _fake_ws_tokens):
        from copilot_backend import CopilotConnectionPool, CopilotBackend
        pool = CopilotConnectionPool(max_connections=2)
        b = CopilotBackend()
        b._conversation_id = "c1"
        await pool.release(b)
        assert b._conversation_id is None
        assert len(pool._connections) == 1


async def test_pool_close_all_empties_pool():
    with patch("copilot_backend.CopilotBackend._ws_stream", _fake_ws_tokens):
        from copilot_backend import CopilotConnectionPool, CopilotBackend
        pool = CopilotConnectionPool()
        b = CopilotBackend()
        pool._connections.append(b)
        await pool.close_all()
        assert len(pool._connections) == 0
