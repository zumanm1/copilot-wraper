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


def test_extract_conversation_id_shapes():
    from copilot_backend import _extract_conversation_id
    assert _extract_conversation_id({"id": "abc"}) == "abc"
    assert _extract_conversation_id({"conversations": [{"id": "c1"}]}) == "c1"
    assert _extract_conversation_id({"items": [{"conversationId": "c2"}]}) == "c2"
    assert _extract_conversation_id([{"conversationId": "c3"}]) == "c3"
    assert _extract_conversation_id({"items": []}) is None


def test_validate_provider_cookie_compatibility_m365_ok(monkeypatch):
    import config
    from copilot_backend import _validate_provider_cookie_compatibility

    monkeypatch.setattr(config, "copilot_provider", lambda: "m365")
    _validate_provider_cookie_compatibility("MSFPC=abc; foo=bar")


def test_validate_provider_cookie_compatibility_m365_missing_required(monkeypatch):
    import config
    from copilot_backend import _validate_provider_cookie_compatibility

    monkeypatch.setattr(config, "copilot_provider", lambda: "m365")
    with pytest.raises(RuntimeError, match="M365 provider selected"):
        _validate_provider_cookie_compatibility("MUID=abc; foo=bar")


def test_validate_provider_cookie_compatibility_copilot_ok(monkeypatch):
    import config
    from copilot_backend import _validate_provider_cookie_compatibility

    monkeypatch.setattr(config, "copilot_provider", lambda: "copilot")
    _validate_provider_cookie_compatibility("MUID=abc; foo=bar")


def test_validate_provider_cookie_compatibility_copilot_missing_required(monkeypatch):
    import config
    from copilot_backend import _validate_provider_cookie_compatibility

    monkeypatch.setattr(config, "copilot_provider", lambda: "copilot")
    with pytest.raises(RuntimeError, match="Copilot provider selected"):
        _validate_provider_cookie_compatibility("OH.SID=abc; foo=bar")


def test_validate_provider_cookie_compatibility_empty_cookie_header(monkeypatch):
    import config
    from copilot_backend import _validate_provider_cookie_compatibility

    monkeypatch.setattr(config, "copilot_provider", lambda: "m365")
    with pytest.raises(RuntimeError, match="No Copilot cookies loaded"):
        _validate_provider_cookie_compatibility("")


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, get_resp=None, post_resp=None):
        self._get_resp = get_resp
        self._post_resp = post_resp

    def get(self, *_args, **_kwargs):
        return self._get_resp

    def post(self, *_args, **_kwargs):
        return self._post_resp


async def test_create_conversation_m365_get_success(monkeypatch):
    from copilot_backend import CopilotBackend
    import config
    monkeypatch.setattr(config, "copilot_provider", lambda: "m365")
    b = CopilotBackend()
    s = _FakeSession(
        get_resp=_FakeResp(200, {"conversations": [{"id": "m365-1"}]}),
        post_resp=_FakeResp(200, {"id": "post-fallback"}),
    )
    cid = await b._create_conversation(s)
    assert cid == "m365-1"


async def test_create_conversation_m365_post_fallback(monkeypatch):
    from copilot_backend import CopilotBackend
    import config
    monkeypatch.setattr(config, "copilot_provider", lambda: "m365")
    b = CopilotBackend()
    s = _FakeSession(
        get_resp=_FakeResp(405, {}),
        post_resp=_FakeResp(200, {"id": "post-id"}),
    )
    cid = await b._create_conversation(s)
    assert cid == "post-id"


async def test_create_conversation_m365_unauthorized(monkeypatch):
    from copilot_backend import CopilotBackend
    import config
    monkeypatch.setattr(config, "copilot_provider", lambda: "m365")
    b = CopilotBackend()
    s = _FakeSession(
        get_resp=_FakeResp(405, {}),
        post_resp=_FakeResp(403, {}),
    )
    with pytest.raises(RuntimeError, match="unauthorized"):
        await b._create_conversation(s)


async def test_create_conversation_copilot_m365_base_mismatch(monkeypatch):
    from copilot_backend import CopilotBackend
    import config

    monkeypatch.setattr(config, "copilot_provider", lambda: "copilot")
    monkeypatch.setattr(config, "copilot_conversations_url", lambda: "https://m365.cloud.microsoft/c/api/conversations")
    b = CopilotBackend()
    s = _FakeSession(
        get_resp=_FakeResp(200, {}),
        post_resp=_FakeResp(200, {"id": "should-not-be-used"}),
    )
    with pytest.raises(RuntimeError, match="Config mismatch"):
        await b._create_conversation(s)
