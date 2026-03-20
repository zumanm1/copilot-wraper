"""
Shared fixtures for the unit + integration test suite.
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── AsyncMock SydneyClient ────────────────────────────────────────────────────

@pytest.fixture
def mock_sydney_client():
    """A fully mocked SydneyClient that returns a canned response."""
    client = AsyncMock()
    client.ask = AsyncMock(return_value="Mocked Copilot response")
    client.start_conversation = AsyncMock()
    client.close_conversation = AsyncMock()
    client.reset_conversation = AsyncMock()

    async def _fake_ask_stream(*args, **kwargs):
        for token in ["Hello", " ", "world", "!"]:
            yield token

    client.ask_stream = _fake_ask_stream
    return client


@pytest.fixture
def mock_copilot_backend(mock_sydney_client):
    """A CopilotBackend whose SydneyClient is replaced with mock_sydney_client."""
    from copilot_backend import CopilotBackend
    backend = CopilotBackend()
    backend._client = mock_sydney_client
    return backend


# ── FastAPI TestClient ────────────────────────────────────────────────────────

@pytest.fixture
def test_app(mock_sydney_client):
    """FastAPI app with all Copilot I/O mocked at the SydneyClient level."""
    import os
    os.environ.setdefault("BING_COOKIES", "test-cookie")

    # Patch SydneyClient globally so every CopilotBackend uses the mock
    with patch("copilot_backend.SydneyClient", return_value=mock_sydney_client):
        # Also patch pool warm-up so startup event doesn't fail
        with patch("server.config.POOL_WARM_COUNT", 0):
            from fastapi.testclient import TestClient
            import server as srv
            # Reset the singleton pool between tests
            import copilot_backend as cb
            cb._connection_pool = None
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client
            cb._connection_pool = None


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
