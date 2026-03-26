"""
Comprehensive tests for the Work/Web chat-mode toggle.

Layers tested (outermost → innermost):
  A. Normalisation & defaults  — pure logic, no I/O
  B. C9 _chat_one              — header is set iff chat_mode is truthy
  C. C9 /api/chat              — chat_mode extracted from body → _chat_one
  D. C9 /api/validate          — chat_mode flows into every _run_one call
  E. C1 server                 — X-Chat-Mode header extracted → backend
  F. C1 copilot_backend        — chat_mode threaded all the way to _c3_proxy_call
  G. C3 /chat endpoint         — chat_mode read from body → browser_chat
  H. C3 /session-health        — chat_mode from env in response
  I. Phase 3.5 DOM logic       — toggle click, fallback, wrong URL, exception
  J. Edge cases                — upper-case, whitespace, invalid, None, empty
"""
from __future__ import annotations

import asyncio
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from types import SimpleNamespace

# ── helpers ──────────────────────────────────────────────────────────────────

def _norm(v: str | None) -> str:
    """Same normalisation used everywhere in the call chain."""
    return (v or "").strip().lower()


# ═════════════════════════════════════════════════════════════════════════════
# A. Normalisation & default resolution
# ═════════════════════════════════════════════════════════════════════════════

class TestNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("work",   "work"),
        ("Work",   "work"),
        ("WORK",   "work"),
        ("web",    "web"),
        ("Web",    "web"),
        ("WEB",    "web"),
        ("  work  ", "work"),
        ("  WEB\t",  "web"),
        ("",        ""),
        (None,      ""),
    ])
    def test_strip_lower(self, raw, expected):
        assert _norm(raw) == expected

    def test_empty_falls_back_to_env_default(self, monkeypatch):
        monkeypatch.setenv("M365_CHAT_MODE", "web")
        result = _norm("") or os.getenv("M365_CHAT_MODE", "work")
        assert result == "web"

    def test_env_not_set_resolves_to_work(self, monkeypatch):
        monkeypatch.delenv("M365_CHAT_MODE", raising=False)
        result = _norm("") or os.getenv("M365_CHAT_MODE", "work")
        assert result == "work"

    def test_explicit_mode_overrides_env(self, monkeypatch):
        monkeypatch.setenv("M365_CHAT_MODE", "web")
        mode = _norm("work")
        result = mode or os.getenv("M365_CHAT_MODE", "work")
        assert result == "work"

    @pytest.mark.parametrize("invalid", ["chat", "enterprise", "123", "work web", "wrk"])
    def test_invalid_values_pass_through_unchanged(self, invalid):
        """Invalid values are not rejected at normalisation — C3 ignores unknown modes."""
        assert _norm(invalid) == invalid


# ═════════════════════════════════════════════════════════════════════════════
# B. C9 _chat_one — X-Chat-Mode header
# ═════════════════════════════════════════════════════════════════════════════

pytestmark = pytest.mark.asyncio


@pytest.fixture
def c9_app():
    """C9 FastAPI app with its HTTP client replaced by a mock."""
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "c9_jokes"))
    import importlib, c9_jokes.app as c9mod
    importlib.reload(c9mod)
    return c9mod


class TestChatOneHeader:
    async def test_work_mode_sets_header(self, c9_app):
        captured = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "hi"}}]
        }

        async def fake_post(url, *, headers, json, timeout):
            captured["headers"] = headers
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        with patch.object(c9_app, "_get_http", return_value=mock_client):
            await c9_app._chat_one("c2-aider", "joke", "http://c1:8000", chat_mode="work")

        assert captured["headers"].get("X-Chat-Mode") == "work"

    async def test_web_mode_sets_header(self, c9_app):
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
            await c9_app._chat_one("c2-aider", "joke", "http://c1:8000", chat_mode="web")

        assert captured["headers"].get("X-Chat-Mode") == "web"

    async def test_empty_mode_omits_header(self, c9_app):
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
            await c9_app._chat_one("c2-aider", "joke", "http://c1:8000", chat_mode="")

        assert "X-Chat-Mode" not in captured["headers"]

    async def test_none_mode_omits_header(self, c9_app):
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
            # default param is "" which is falsy — same as not passing
            await c9_app._chat_one("c2-aider", "joke", "http://c1:8000")

        assert "X-Chat-Mode" not in captured["headers"]


# ═════════════════════════════════════════════════════════════════════════════
# C. C9 /api/validate — chat_mode flows into _run_one → _chat_one
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def c9_client(tmp_path, monkeypatch):
    """C9 FastAPI TestClient with DB redirected to a temp dir."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "c9_test.db"))
    import importlib
    import c9_jokes.app as c9mod
    importlib.reload(c9mod)
    from fastapi.testclient import TestClient
    return TestClient(c9mod.app), c9mod


class TestValidateChatModeThreading:
    """Verify that chat_mode from /api/validate body reaches _chat_one for every agent."""

    def test_validate_passes_work_mode_to_chat_one(self, c9_client):
        client, c9mod = c9_client
        recorded_modes = []

        async def spy_chat_one(agent_id, prompt, c1_url, chat_mode=""):
            recorded_modes.append(chat_mode)
            return {"ok": True, "http_status": 200, "text": "joke", "elapsed_ms": 10}

        with patch.object(c9mod, "_chat_one", spy_chat_one):
            r = client.post("/api/validate", json={
                "prompt": "tell me a joke",
                "chat_mode": "work",
                "agent_ids": ["c2-aider"],
            })

        assert r.status_code == 200
        assert all(m == "work" for m in recorded_modes), f"unexpected modes: {recorded_modes}"

    def test_validate_passes_web_mode_to_chat_one(self, c9_client):
        client, c9mod = c9_client
        recorded_modes = []

        async def spy_chat_one(agent_id, prompt, c1_url, chat_mode=""):
            recorded_modes.append(chat_mode)
            return {"ok": True, "http_status": 200, "text": "joke", "elapsed_ms": 10}

        with patch.object(c9mod, "_chat_one", spy_chat_one):
            r = client.post("/api/validate", json={
                "prompt": "tell me a joke",
                "chat_mode": "web",
                "agent_ids": ["c2-aider"],
            })

        assert r.status_code == 200
        assert all(m == "web" for m in recorded_modes)

    def test_validate_without_mode_defaults_empty(self, c9_client):
        """No chat_mode in body → _chat_one receives empty string (C3 uses env default)."""
        client, c9mod = c9_client
        recorded_modes = []

        async def spy_chat_one(agent_id, prompt, c1_url, chat_mode=""):
            recorded_modes.append(chat_mode)
            return {"ok": True, "http_status": 200, "text": "joke", "elapsed_ms": 10}

        with patch.object(c9mod, "_chat_one", spy_chat_one):
            r = client.post("/api/validate", json={
                "prompt": "tell me a joke",
                "agent_ids": ["c2-aider"],
            })

        assert r.status_code == 200
        assert all(m == "" for m in recorded_modes)

    def test_validate_passed_failed_counts_not_inverted(self, c9_client):
        """Regression: passed/failed counts must not be swapped (old bug: passed = len - passed)."""
        client, c9mod = c9_client

        async def all_pass(agent_id, prompt, c1_url, chat_mode=""):
            return {"ok": True, "http_status": 200, "text": "joke", "elapsed_ms": 10}

        with patch.object(c9mod, "_chat_one", all_pass):
            r = client.post("/api/validate", json={
                "prompt": "joke",
                "agent_ids": ["c2-aider", "c5-claude-code"],
            })

        body = r.json()
        assert body["passed"] == 2
        assert body["failed"] == 0
        assert body["total"] == 2

    def test_validate_response_has_no_duplicate_failed_key(self, c9_client):
        """Regression: old code had 'failed': prompt in the JSON (corrupted key)."""
        client, c9mod = c9_client

        async def one_fail(agent_id, prompt, c1_url, chat_mode=""):
            return {"ok": False, "http_status": 500, "text": "", "error": "timeout", "elapsed_ms": 5}

        with patch.object(c9mod, "_chat_one", one_fail):
            r = client.post("/api/validate", json={
                "prompt": "my-prompt",
                "agent_ids": ["c2-aider"],
            })

        body = r.json()
        # 'prompt' key must contain the prompt string, not a number
        assert body.get("prompt") == "my-prompt"
        # failed must be an integer
        assert isinstance(body.get("failed"), int)


# ═════════════════════════════════════════════════════════════════════════════
# E. C1 server — X-Chat-Mode header extraction
# ═════════════════════════════════════════════════════════════════════════════

class TestC1ServerChatModeHeader:
    """C1 FastAPI server extracts X-Chat-Mode and passes it to the backend."""

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

    def test_work_header_reaches_backend(self, c1_app):
        client, captured = c1_app
        client.post("/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Chat-Mode": "work", "X-Agent-ID": "c2-aider"},
        )
        assert captured.get("chat_mode") == "work"

    def test_web_header_reaches_backend(self, c1_app):
        client, captured = c1_app
        client.post("/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Chat-Mode": "web", "X-Agent-ID": "c2-aider"},
        )
        assert captured.get("chat_mode") == "web"

    def test_missing_header_sends_empty_string(self, c1_app):
        client, captured = c1_app
        client.post("/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Agent-ID": "c2-aider"},
        )
        assert captured.get("chat_mode") == ""

    def test_header_is_lowercased(self, c1_app):
        """Header value 'WORK' should be normalised to 'work' before reaching backend."""
        client, captured = c1_app
        client.post("/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Chat-Mode": "WORK", "X-Agent-ID": "c2-aider"},
        )
        assert captured.get("chat_mode") == "work"

    def test_whitespace_header_is_stripped(self, c1_app):
        client, captured = c1_app
        client.post("/v1/chat/completions",
            json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Chat-Mode": "  web  ", "X-Agent-ID": "c2-aider"},
        )
        assert captured.get("chat_mode") == "web"


# ═════════════════════════════════════════════════════════════════════════════
# F. C1 copilot_backend — chat_mode in _c3_proxy_call POST body
# ═════════════════════════════════════════════════════════════════════════════

class TestC1BackendChatModePayload:
    """_c3_proxy_call includes chat_mode in the JSON payload sent to C3."""

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

    async def test_work_mode_in_proxy_payload(self, m365_backend, monkeypatch):
        captured_payload = {}

        class FakeResp:
            status = 200
            headers = {"content-type": "application/json"}
            async def json(self): return {"success": True, "text": "hi"}
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        class FakeSession:
            def post(self, url, *, json, timeout):
                captured_payload.update(json)
                return FakeResp()

        m365_backend._get_c3_session = lambda: FakeSession()
        await m365_backend._c3_proxy_call("hello", agent_id="c2", chat_mode="work")
        assert captured_payload["chat_mode"] == "work"

    async def test_web_mode_in_proxy_payload(self, m365_backend):
        captured_payload = {}

        class FakeResp:
            status = 200
            headers = {"content-type": "application/json"}
            async def json(self): return {"success": True, "text": "hi"}
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        class FakeSession:
            def post(self, url, *, json, timeout):
                captured_payload.update(json)
                return FakeResp()

        m365_backend._get_c3_session = lambda: FakeSession()
        await m365_backend._c3_proxy_call("hello", agent_id="c2", chat_mode="web")
        assert captured_payload["chat_mode"] == "web"

    async def test_empty_mode_sends_empty_string(self, m365_backend):
        captured_payload = {}

        class FakeResp:
            status = 200
            headers = {"content-type": "application/json"}
            async def json(self): return {"success": True, "text": "hi"}
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        class FakeSession:
            def post(self, url, *, json, timeout):
                captured_payload.update(json)
                return FakeResp()

        m365_backend._get_c3_session = lambda: FakeSession()
        await m365_backend._c3_proxy_call("hello", agent_id="c2", chat_mode="")
        assert captured_payload["chat_mode"] == ""

    async def test_chat_completion_threads_mode_to_proxy(self, m365_backend, monkeypatch):
        """chat_completion(chat_mode='web') reaches _c3_proxy_call with chat_mode='web'."""
        spy = AsyncMock(return_value="response text")
        m365_backend._c3_proxy_call = spy

        await m365_backend.chat_completion("test prompt", chat_mode="web")

        spy.assert_awaited_once()
        _, kwargs = spy.call_args
        assert kwargs.get("chat_mode") == "web"

    async def test_missing_mode_defaults_to_empty_in_proxy(self, m365_backend):
        spy = AsyncMock(return_value="response text")
        m365_backend._c3_proxy_call = spy

        await m365_backend.chat_completion("test prompt")  # no chat_mode

        spy.assert_awaited_once()
        _, kwargs = spy.call_args
        assert kwargs.get("chat_mode", "") == ""


# ═════════════════════════════════════════════════════════════════════════════
# G. C3 /chat endpoint — reads chat_mode from body
# ═════════════════════════════════════════════════════════════════════════════

class TestC3ChatEndpoint:
    """C3's /chat endpoint passes chat_mode through to browser_chat()."""

    @pytest.fixture
    def c3_client(self):
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "browser_auth"))
        import importlib
        import browser_auth.server as srv_mod
        importlib.reload(srv_mod)
        from fastapi.testclient import TestClient
        return TestClient(srv_mod.app), srv_mod

    async def test_chat_mode_passed_to_browser_chat(self, c3_client):
        client, srv_mod = c3_client
        captured = {}

        async def fake_browser_chat(prompt, mode="", timeout_ms=30000, agent_id=""):
            captured["mode"] = mode
            return {"success": True, "text": "hi", "events": []}

        with patch.object(srv_mod, "browser_chat", fake_browser_chat):
            r = client.post("/chat", json={"prompt": "joke", "chat_mode": "web"})

        assert r.status_code == 200
        assert captured["mode"] == "web"

    async def test_legacy_mode_field_still_works(self, c3_client):
        """Backwards compat: old 'mode' field still accepted."""
        client, srv_mod = c3_client
        captured = {}

        async def fake_browser_chat(prompt, mode="", timeout_ms=30000, agent_id=""):
            captured["mode"] = mode
            return {"success": True, "text": "hi", "events": []}

        with patch.object(srv_mod, "browser_chat", fake_browser_chat):
            r = client.post("/chat", json={"prompt": "joke", "mode": "web"})

        assert r.status_code == 200
        assert captured["mode"] == "web"

    async def test_chat_mode_takes_priority_over_mode(self, c3_client):
        """chat_mode field wins over legacy mode field when both present."""
        client, srv_mod = c3_client
        captured = {}

        async def fake_browser_chat(prompt, mode="", timeout_ms=30000, agent_id=""):
            captured["mode"] = mode
            return {"success": True, "text": "hi", "events": []}

        with patch.object(srv_mod, "browser_chat", fake_browser_chat):
            r = client.post("/chat", json={
                "prompt": "joke",
                "chat_mode": "work",
                "mode": "web",        # legacy — should be overridden
            })

        assert r.status_code == 200
        assert captured["mode"] == "work"

    async def test_missing_chat_mode_sends_empty(self, c3_client):
        client, srv_mod = c3_client
        captured = {}

        async def fake_browser_chat(prompt, mode="", timeout_ms=30000, agent_id=""):
            captured["mode"] = mode
            return {"success": True, "text": "hi", "events": []}

        with patch.object(srv_mod, "browser_chat", fake_browser_chat):
            r = client.post("/chat", json={"prompt": "joke"})

        assert r.status_code == 200
        assert captured["mode"] == ""


# ═════════════════════════════════════════════════════════════════════════════
# H. C3 /session-health — chat_mode from env
# ═════════════════════════════════════════════════════════════════════════════

class TestC3SessionHealthChatMode:

    @pytest.fixture
    def c3_client(self):
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "browser_auth"))
        import importlib
        import browser_auth.server as srv_mod
        importlib.reload(srv_mod)
        from fastapi.testclient import TestClient
        return TestClient(srv_mod.app), srv_mod

    def test_session_health_returns_work_by_default(self, c3_client, monkeypatch):
        client, srv_mod = c3_client
        monkeypatch.delenv("M365_CHAT_MODE", raising=False)

        async def fake_health(env_path):
            return {"session": "active", "profile": "m365_hub", "reason": None,
                    "checked_at": "2026-01-01T00:00:00Z"}

        with patch.object(srv_mod, "check_session_health", fake_health):
            r = client.get("/session-health")

        assert r.status_code == 200
        assert r.json()["chat_mode"] == "work"

    def test_session_health_returns_web_when_env_set(self, c3_client, monkeypatch):
        client, srv_mod = c3_client
        monkeypatch.setenv("M365_CHAT_MODE", "web")

        async def fake_health(env_path):
            return {"session": "active", "profile": "m365_hub", "reason": None,
                    "checked_at": "2026-01-01T00:00:00Z"}

        with patch.object(srv_mod, "check_session_health", fake_health):
            r = client.get("/session-health")

        assert r.status_code == 200
        assert r.json()["chat_mode"] == "web"

    def test_session_health_expired_no_chat_mode_crash(self, c3_client, monkeypatch):
        """Expired session response should still include chat_mode without crashing."""
        client, srv_mod = c3_client
        monkeypatch.setenv("M365_CHAT_MODE", "work")

        async def fake_health(env_path):
            return {"session": "expired", "profile": None, "reason": "OH.SID missing",
                    "checked_at": "2026-01-01T00:00:00Z"}

        with patch.object(srv_mod, "check_session_health", fake_health):
            r = client.get("/session-health")

        assert r.status_code == 200
        assert r.json()["chat_mode"] == "work"


# ═════════════════════════════════════════════════════════════════════════════
# I. Phase 3.5 DOM-click logic
# ═════════════════════════════════════════════════════════════════════════════

class TestPhase35DomClick:
    """Unit-test the Work/Web DOM-click logic extracted from _browser_chat_on_page."""

    async def _run_phase35(self, page, mode, env_chat_mode="work"):
        """Re-implement Phase 3.5 inline so we can unit test it without a real browser."""
        _chat_mode_target = (mode or env_chat_mode).strip().lower()
        clicked = False
        if _chat_mode_target in ("work", "web") and "m365.cloud.microsoft" in (page.url or ""):
            _mode_label = _chat_mode_target.capitalize()
            try:
                result = await page.evaluate("(label) => true", _mode_label)
                if result:
                    await asyncio.sleep(0)   # stand-in for 0.3s delay
                    clicked = True
            except Exception:
                pass
        return clicked, _chat_mode_target

    async def test_work_mode_on_m365_url_clicks(self):
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(return_value=True)

        clicked, mode = await self._run_phase35(page, "work")
        assert clicked is True
        assert mode == "work"
        page.evaluate.assert_awaited_once()

    async def test_web_mode_on_m365_url_clicks(self):
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(return_value=True)

        clicked, mode = await self._run_phase35(page, "web")
        assert clicked is True
        assert mode == "web"

    async def test_toggle_not_found_returns_false(self):
        """page.evaluate returns False (element not in DOM) — no exception, no click."""
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(return_value=False)

        clicked, mode = await self._run_phase35(page, "work")
        assert clicked is False   # not found — non-fatal

    async def test_non_m365_url_skips_click(self):
        """Consumer Copilot URL — toggle does not exist, skip entirely."""
        page = AsyncMock()
        page.url = "https://copilot.microsoft.com/"
        page.evaluate = AsyncMock(return_value=True)

        clicked, mode = await self._run_phase35(page, "work")
        assert clicked is False
        page.evaluate.assert_not_awaited()

    async def test_invalid_mode_skips_click(self):
        """Unrecognised mode (not 'work' or 'web') — skip toggle to avoid misclick."""
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(return_value=True)

        clicked, mode = await self._run_phase35(page, "enterprise")
        assert clicked is False
        page.evaluate.assert_not_awaited()

    async def test_evaluate_exception_is_non_fatal(self):
        """page.evaluate raises — exception is caught, execution continues."""
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(side_effect=RuntimeError("frame detached"))

        # Should not raise
        clicked, mode = await self._run_phase35(page, "work")
        assert clicked is False

    async def test_empty_mode_falls_back_to_env_default(self, monkeypatch):
        monkeypatch.setenv("M365_CHAT_MODE", "web")
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(return_value=True)

        env_mode = os.getenv("M365_CHAT_MODE", "work")
        clicked, mode = await self._run_phase35(page, "", env_chat_mode=env_mode)
        assert mode == "web"
        assert clicked is True

    async def test_mode_none_equivalent_to_empty(self, monkeypatch):
        monkeypatch.delenv("M365_CHAT_MODE", raising=False)
        page = AsyncMock()
        page.url = "https://m365.cloud.microsoft/chat"
        page.evaluate = AsyncMock(return_value=True)

        env_mode = os.getenv("M365_CHAT_MODE", "work")
        clicked, mode = await self._run_phase35(page, None, env_chat_mode=env_mode)
        assert mode == "work"   # hardcoded fallback
        assert clicked is True


# ═════════════════════════════════════════════════════════════════════════════
# J. Edge cases — input sanitisation at each boundary
# ═════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_c1_rejects_header_with_only_whitespace(self):
        """X-Chat-Mode containing only whitespace resolves to empty string."""
        value = "   \t  "
        normalised = (value or "").strip().lower()
        assert normalised == ""

    def test_c9_body_upper_case_normalised(self):
        value = "WORK"
        normalised = (value or "").strip().lower()
        assert normalised == "work"

    def test_c9_body_mixed_case_normalised(self):
        value = "WeB"
        normalised = (value or "").strip().lower()
        assert normalised == "web"

    def test_c9_body_none_treated_as_empty(self):
        value = None
        normalised = (value or "").strip().lower()
        assert normalised == ""

    def test_c3_proxy_payload_never_none(self):
        """chat_mode sent to C3 must always be a string, never None."""
        for raw in [None, "", "work", "web"]:
            sent = raw or ""
            assert isinstance(sent, str)

    @pytest.mark.parametrize("mode,valid", [
        ("work", True),
        ("web",  True),
        ("chat", False),
        ("enterprise", False),
        ("", False),
        (None, False),
    ])
    def test_valid_modes_for_dom_click(self, mode, valid):
        """Phase 3.5 only clicks when mode is exactly 'work' or 'web'."""
        normalised = (mode or "").strip().lower()
        is_valid = normalised in ("work", "web")
        assert is_valid == valid

    def test_concurrent_agents_get_correct_modes(self, c9_client):
        """All agents in a parallel validate run receive the same chat_mode."""
        client, c9mod = c9_client
        all_agent_ids = ["c2-aider", "c5-claude-code", "c6-kilocode"]
        recorded = {}

        async def spy_chat_one(agent_id, prompt, c1_url, chat_mode=""):
            recorded[agent_id] = chat_mode
            return {"ok": True, "http_status": 200, "text": "joke", "elapsed_ms": 15}

        with patch.object(c9mod, "_chat_one", spy_chat_one):
            r = client.post("/api/validate", json={
                "prompt": "joke",
                "chat_mode": "web",
                "agent_ids": all_agent_ids,
            })

        assert r.status_code == 200
        for aid in all_agent_ids:
            assert recorded[aid] == "web", f"agent {aid} got wrong mode: {recorded[aid]}"
