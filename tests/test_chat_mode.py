"""
Regression coverage for the current C9 → C1 → C3 mode contract.

Current contract:
  - X-Chat-Mode: thinking depth (auto | quick | deep)
  - X-Work-Mode: M365 grounding scope forwarded to C3 (work | web)
  - C3 /chat body field chat_mode: work/web selector for the browser flow
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def c9_app():
    sys.path.insert(0, str(Path(__file__).parent.parent / "c9_jokes"))
    import importlib
    import c9_jokes.app as c9mod

    importlib.reload(c9mod)
    return c9mod


@pytest.fixture
def c9_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "c9_test.db"))
    import importlib
    import c9_jokes.app as c9mod

    importlib.reload(c9mod)
    from fastapi.testclient import TestClient

    return TestClient(c9mod.app), c9mod


class TestC9Headers:
    async def test_chat_one_sets_thinking_and_work_headers(self, c9_app):
        captured = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}

        async def fake_post(url, *, headers, json, timeout):
            captured["headers"] = headers
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        with patch.object(c9_app, "_get_http", return_value=mock_client):
            await c9_app._chat_one(
                "c2-aider",
                "joke",
                "http://c1:8000",
                chat_mode="deep",
                work_mode="web",
            )

        assert captured["headers"]["X-Chat-Mode"] == "deep"
        assert captured["headers"]["X-Work-Mode"] == "web"

    async def test_chat_one_omits_empty_headers(self, c9_app):
        captured = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hi"}}]}

        async def fake_post(url, *, headers, json, timeout):
            captured["headers"] = headers
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        with patch.object(c9_app, "_get_http", return_value=mock_client):
            await c9_app._chat_one("c2-aider", "joke", "http://c1:8000")

        assert "X-Chat-Mode" not in captured["headers"]
        assert "X-Work-Mode" not in captured["headers"]


class TestValidateThreading:
    def test_validate_passes_modes_to_all_agents(self, c9_client):
        client, c9mod = c9_client
        all_agent_ids = ["c2-aider", "c5-claude-code", "c6-kilocode"]
        recorded = {}

        async def spy_chat_one(
            agent_id,
            prompt,
            c1_url,
            chat_mode="",
            attachments=None,
            work_mode="",
            messages=None,
            **kwargs,
        ):
            recorded[agent_id] = {"chat_mode": chat_mode, "work_mode": work_mode}
            return {"ok": True, "http_status": 200, "text": "joke", "elapsed_ms": 15}

        with patch.object(c9mod, "_chat_one", spy_chat_one):
            r = client.post("/api/validate", json={
                "prompt": "joke",
                "chat_mode": "deep",
                "work_mode": "web",
                "agent_ids": all_agent_ids,
            })

        assert r.status_code == 200
        for aid in all_agent_ids:
            assert recorded[aid] == {"chat_mode": "deep", "work_mode": "web"}


class TestC1ServerHeaders:
    @pytest.fixture
    def c1_app(self, monkeypatch):
        monkeypatch.setenv("BING_COOKIES", "test-cookie")
        monkeypatch.setenv("COPILOT_PROVIDER", "m365")
        monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "m365_hub")

        captured = {}

        async def spy_c3_proxy(self, prompt, agent_id="", chat_mode=""):
            captured["chat_mode"] = chat_mode
            return "mocked response"

        import importlib
        import config as cfg
        import copilot_backend as cb

        importlib.reload(cfg)
        importlib.reload(cb)

        with patch("copilot_backend.CopilotBackend._c3_proxy_call", spy_c3_proxy):
            with patch("server.config.POOL_WARM_COUNT", 0):
                import server as srv

                importlib.reload(srv)
                cb._connection_pool = None
                from fastapi.testclient import TestClient

                client = TestClient(srv.app, raise_server_exceptions=False)
                yield client, captured
                cb._connection_pool = None

    def test_work_mode_reaches_backend(self, c1_app):
        client, captured = c1_app

        client.post(
            "/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Work-Mode": "work", "X-Agent-ID": "c2-aider"},
        )

        assert captured.get("chat_mode") == "work"

    def test_thinking_mode_affects_style_but_not_backend_mode(self, c1_app):
        client, captured = c1_app

        r = client.post(
            "/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "X-Chat-Mode": "deep",
                "X-Work-Mode": "web",
                "X-Agent-ID": "c2-aider",
            },
        )

        assert captured.get("chat_mode") == "web"
        assert "copilot_style=reasoning" in r.headers.get("x-generation-params-note", "")

    def test_missing_work_mode_sends_empty_string(self, c1_app):
        client, captured = c1_app

        client.post(
            "/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Chat-Mode": "quick", "X-Agent-ID": "c2-aider"},
        )

        assert captured.get("chat_mode") == ""


class TestC1BackendProxyPayload:
    @pytest.fixture
    def m365_backend(self, monkeypatch):
        monkeypatch.setenv("COPILOT_PROVIDER", "m365")
        monkeypatch.setenv("COPILOT_PORTAL_PROFILE", "m365_hub")
        import config as cfg
        import copilot_backend as cb
        import importlib

        importlib.reload(cfg)
        importlib.reload(cb)
        return cb.CopilotBackend()

    async def test_work_mode_in_proxy_payload(self, m365_backend):
        captured_payload = {}

        class FakeResp:
            status = 200
            headers = {"content-type": "application/json"}

            async def json(self):
                return {"success": True, "text": "hi"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            def post(self, url, *, json, timeout):
                captured_payload.update(json)
                return FakeResp()

        async def fake_get_session():
            return FakeSession()

        m365_backend._get_c3_session = fake_get_session
        await m365_backend._c3_proxy_call("hello", agent_id="c2", chat_mode="work")
        assert captured_payload["chat_mode"] == "work"

    async def test_empty_mode_sends_empty_string(self, m365_backend):
        captured_payload = {}

        class FakeResp:
            status = 200
            headers = {"content-type": "application/json"}

            async def json(self):
                return {"success": True, "text": "hi"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            def post(self, url, *, json, timeout):
                captured_payload.update(json)
                return FakeResp()

        async def fake_get_session():
            return FakeSession()

        m365_backend._get_c3_session = fake_get_session
        await m365_backend._c3_proxy_call("hello", agent_id="c2", chat_mode="")
        assert captured_payload["chat_mode"] == ""

    async def test_chat_completion_threads_work_mode_to_proxy(self, m365_backend):
        spy = AsyncMock(return_value="response text")
        m365_backend._c3_proxy_call = spy

        await m365_backend.chat_completion("test prompt", chat_mode="web")

        spy.assert_awaited_once()
        _, kwargs = spy.call_args
        assert kwargs.get("chat_mode") == "web"


class TestC3Endpoints:
    @pytest.fixture
    def c3_client(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "browser_auth"))
        import importlib
        import browser_auth.server as srv_mod

        importlib.reload(srv_mod)
        from fastapi.testclient import TestClient

        return TestClient(srv_mod.app), srv_mod

    async def test_chat_endpoint_passes_work_mode_to_browser(self, c3_client):
        client, srv_mod = c3_client
        captured = {}

        async def fake_browser_chat(prompt, mode="", timeout_ms=30000, agent_id=""):
            captured["mode"] = mode
            return {"success": True, "text": "hi", "events": []}

        with patch.object(srv_mod, "browser_chat", fake_browser_chat):
            r = client.post("/chat", json={"prompt": "joke", "chat_mode": "web"})

        assert r.status_code == 200
        assert captured["mode"] == "web"

    def test_session_health_returns_env_chat_mode(self, c3_client, monkeypatch):
        client, srv_mod = c3_client
        monkeypatch.setenv("M365_CHAT_MODE", "web")

        async def fake_health(env_path):
            return {
                "session": "active",
                "profile": "m365_hub",
                "reason": None,
                "checked_at": "2026-01-01T00:00:00Z",
            }

        with patch.object(srv_mod, "check_session_health", fake_health):
            r = client.get("/session-health")

        assert r.status_code == 200
        assert r.json()["chat_mode"] == "web"
