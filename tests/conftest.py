"""
Shared fixtures for the unit + integration test suite.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _reset_agent_registry_between_tests():
    """Default session is global on the FastAPI app; clear between tests for isolation."""
    from agent_manager import reset_agent_registry_for_tests

    reset_agent_registry_for_tests()
    yield
    reset_agent_registry_for_tests()


def pytest_collection_modifyitems(config, items):
    """Run Playwright/network container tests last; they leave a running asyncio loop that breaks pytest-asyncio."""
    def sort_key(item):
        node = item.nodeid
        late = (
            node.startswith("tests/test_new_containers")
            or node.startswith("tests/test_playwright")
            or node.startswith("tests/test_playwright_c3_setup")
            or node.startswith("tests/test_playwright_novnc")
            or node.startswith("tests/test_puppeteer_novnc")
            or node.startswith("tests/test_puppeteer_c3_setup")
        )
        return (1 if late else 0, node)

    items.sort(key=sort_key)


async def _fake_ws_stream(self, prompt, context, attachment_path=None):
    """Avoid real HTTP/WebSocket; yields canned tokens."""
    yield "Mocked"
    yield " Copilot"
    yield " response"


# ── FastAPI TestClient ────────────────────────────────────────────────────────

@pytest.fixture
def test_app():
    """FastAPI app with Copilot WebSocket I/O stubbed via _ws_stream."""
    import os
    os.environ.setdefault("BING_COOKIES", "test-cookie")

    with patch("copilot_backend.CopilotBackend._ws_stream", _fake_ws_stream):
        with patch("server.config.POOL_WARM_COUNT", 0):
            from fastapi.testclient import TestClient
            import server as srv
            import copilot_backend as cb
            cb._connection_pool = None
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client
            cb._connection_pool = None
