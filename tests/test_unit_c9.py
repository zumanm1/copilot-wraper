"""
Unit tests for c9_jokes/app.py — no live containers required.

Strategy:
- FastAPI TestClient for C9's app
- httpx calls to C1/C3 are intercepted by monkeypatching _get_http() to return
  a mock AsyncClient that returns canned JSON responses.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# ── Make c9_jokes importable ─────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "c9_jokes"))


# Override the autouse conftest fixture (imports agent_manager, irrelevant here)
@pytest.fixture(autouse=True)
def _reset_agent_registry_between_tests():
    yield


# ── Shared fake C1 response ───────────────────────────────────────────────────

def _make_c1_ok(text: str = "Why don't scientists trust atoms? Because they make up everything.") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "copilot",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20},
    }


def _make_mock_http(response_json: dict, status: int = 200):
    """Return a mock httpx.AsyncClient that intercepts POST /v1/chat/completions."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json = MagicMock(return_value=response_json)
    mock_resp.text = json.dumps(response_json)

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.get  = AsyncMock(return_value=mock_resp)
    mock_client.is_closed = False
    return mock_client


# ── TestClient fixture ────────────────────────────────────────────────────────

@pytest.fixture
def c9_app(tmp_path):
    """C9 FastAPI app with mocked C1 HTTP client and temp SQLite DB."""
    db_path = str(tmp_path / "test_c9.db")
    with patch.dict(os.environ, {
        "C1_URL": "http://fake-c1:8000",
        "DATABASE_PATH": db_path,
    }):
        import importlib
        import c9_jokes.app as c9_mod
        importlib.reload(c9_mod)          # fresh module with patched env
        from fastapi.testclient import TestClient

        mock_http = _make_mock_http(_make_c1_ok())
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            with patch.object(c9_mod, "_http", mock_http):
                yield TestClient(c9_mod.app, raise_server_exceptions=False)


# ── Page route tests ──────────────────────────────────────────────────────────

class TestC9PageRoutes:
    def test_chat_page_returns_200(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200

    def test_chat_page_has_thinking_dropdown(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        html = r.text
        assert "thinking-pill" in html
        assert 'data-mode="auto"' in html
        assert 'data-mode="quick"' in html
        assert 'data-mode="deep"' in html
        assert "thinkingMode" in html

    def test_chat_page_has_work_web_toggle(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        html = r.text
        assert "work-web-toggle" in html
        assert 'data-mode="work"' in html
        assert 'data-mode="web"' in html
        assert "workMode" in html

    def test_chat_page_has_file_upload(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        html = r.text
        assert "attach-plus" in html
        assert "file-input" in html
        assert "Upload files" in html
        assert "/api/upload" in html

    def test_pairs_page_returns_200(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        for agent_id in ("c2-aider", "c5-claude-code", "c6-kilocode", "c8-hermes", "c9-jokes"):
            assert agent_id in r.text

    def test_pairs_page_has_thinking_dropdown(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        html = r.text
        assert "thinking-pill" in html
        assert 'data-mode="auto"' in html
        assert 'data-mode="quick"' in html
        assert 'data-mode="deep"' in html
        assert "pairsThinkingMode" in html

    def test_pairs_page_has_work_web_toggle(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        html = r.text
        assert "mode-work" in html
        assert "mode-web" in html
        assert "activeWorkMode" in html

    def test_pairs_page_has_file_upload(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        html = r.text
        assert "attach-plus" in html
        assert "file-input" in html
        assert "Upload files" in html
        assert "/api/upload" in html

    def test_api_reference_page_returns_200(self, c9_app):
        r = c9_app.get("/api")
        assert r.status_code == 200

    def test_logs_page_returns_200(self, c9_app):
        r = c9_app.get("/logs")
        assert r.status_code == 200

    def test_health_page_returns_200(self, c9_app):
        r = c9_app.get("/health")
        assert r.status_code == 200


# ── /api/chat tests ───────────────────────────────────────────────────────────

class TestC9ApiChat:
    def _post_chat(self, c9_app, payload: dict):
        return c9_app.post("/api/chat", json=payload)

    def test_basic_joke_returns_ok(self, c9_app):
        r = self._post_chat(c9_app, {"agent_id": "c9-jokes", "prompt": "Tell me a joke"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "everything" in body["text"]  # canned response contains "make up everything"

    def test_empty_prompt_returns_400(self, c9_app):
        r = self._post_chat(c9_app, {"agent_id": "c9-jokes", "prompt": ""})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_missing_prompt_returns_400(self, c9_app):
        r = self._post_chat(c9_app, {"agent_id": "c9-jokes"})
        assert r.status_code == 400

    @pytest.mark.parametrize("think", ["auto", "quick", "deep"])
    def test_thinking_mode_accepted(self, c9_app, think):
        """chat_mode (thinking) must be forwarded without error for all three modes."""
        r = self._post_chat(c9_app, {
            "agent_id": "c9-jokes",
            "prompt": "Tell me a short joke",
            "chat_mode": think,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    @pytest.mark.parametrize("work", ["work", "web"])
    def test_work_web_mode_accepted(self, c9_app, work):
        """work_mode (Work/Web) must be accepted without error."""
        r = self._post_chat(c9_app, {
            "agent_id": "c9-jokes",
            "prompt": "Tell me a joke",
            "work_mode": work,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_x_chat_mode_header_sent_to_c1(self, c9_app):
        """Verify X-Chat-Mode: deep is forwarded to C1."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Joke",
                "chat_mode": "deep",
            })
        assert r.status_code == 200
        assert captured_headers.get("X-Chat-Mode") == "deep"

    def test_x_work_mode_header_sent_to_c1(self, c9_app):
        """Verify X-Work-Mode: web is forwarded to C1."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Joke",
                "work_mode": "web",
            })
        assert r.status_code == 200
        assert captured_headers.get("X-Work-Mode") == "web"

    def test_both_headers_sent_together(self, c9_app):
        """X-Chat-Mode and X-Work-Mode can both be sent in the same request."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Joke",
                "chat_mode": "quick",
                "work_mode": "work",
            })
        assert r.status_code == 200
        assert captured_headers.get("X-Chat-Mode") == "quick"
        assert captured_headers.get("X-Work-Mode") == "work"

    def test_c9_jokes_agent_uses_correct_agent_id(self, c9_app):
        """Default agent_id fallback is 'c9-jokes'."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            # Don't pass agent_id — should default to c9-jokes
            r = c9_app.post("/api/chat", json={"prompt": "Joke"})
        assert r.status_code == 200
        assert captured_headers.get("X-Agent-ID") == "c9-jokes"


# ── /api/validate tests ───────────────────────────────────────────────────────

class TestC9ApiValidate:
    def test_validate_all_agents_ok(self, c9_app):
        r = c9_app.post("/api/validate", json={"prompt": "Tell me a joke"})
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] == body["total"]
        assert body["failed"] == 0
        assert len(body["results"]) > 0

    def test_validate_with_thinking_mode(self, c9_app):
        r = c9_app.post("/api/validate", json={
            "prompt": "Tell me a joke",
            "chat_mode": "deep",
            "work_mode": "work",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] == body["total"]

    def test_validate_with_web_mode(self, c9_app):
        r = c9_app.post("/api/validate", json={
            "prompt": "Tell me a joke",
            "work_mode": "web",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] == body["total"]

    def test_validate_no_matching_agents_returns_400(self, c9_app):
        r = c9_app.post("/api/validate", json={
            "prompt": "Joke",
            "agent_ids": ["nonexistent-agent"],
        })
        assert r.status_code == 400

    def test_validate_mode_parallel(self, c9_app):
        r = c9_app.post("/api/validate", json={"prompt": "Joke", "parallel": True})
        assert r.json()["mode"] == "parallel"

    def test_validate_mode_sequential(self, c9_app):
        r = c9_app.post("/api/validate", json={"prompt": "Joke", "parallel": False})
        assert r.json()["mode"] == "sequential"

    def test_validate_calls_appear_in_logs(self, c9_app):
        """Validation runs must be visible in /logs (source='validate')."""
        # Run a single-agent validate
        c9_app.post("/api/validate", json={
            "prompt": "Validate log test",
            "agent_ids": ["c9-jokes"],
        })
        r = c9_app.get("/logs")
        assert r.status_code == 200
        assert "validate" in r.text  # source badge visible

    def test_chat_logs_include_elapsed_ms(self, c9_app):
        """chat_logs must store elapsed_ms so /logs can display response time."""
        c9_app.post("/api/chat", json={"agent_id": "c9-jokes", "prompt": "Timing test"})
        r = c9_app.get("/api/logs")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert len(rows) > 0
        # elapsed_ms should be present (may be 0 in test but not absent)
        assert "elapsed_ms" in rows[0]

    def test_failed_chat_logs_error_text(self, c9_app):
        """When C1 returns an error, response_excerpt must contain the error, not be blank."""
        import c9_jokes.app as c9_mod
        error_resp = {"detail": "Upstream timeout from Copilot"}
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json = MagicMock(return_value=error_resp)
        mock_resp.text = json.dumps(error_resp)
        mock_http = _make_mock_http(error_resp, status=503)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            c9_app.post("/api/chat", json={"agent_id": "c9-jokes", "prompt": "Will fail"})
        r = c9_app.get("/api/logs")
        rows = r.json()["rows"]
        assert len(rows) > 0
        latest = rows[0]
        assert latest["http_status"] == 503
        assert "Upstream timeout" in (latest["response_excerpt"] or "")


# ── /pairs page: header correctness tests ────────────────────────────────────

class TestC9PairsValidate:
    """Verify pairs page sends chat_mode (thinking) and work_mode correctly."""

    def _capture_validate(self, c9_app, payload: dict):
        """Send /api/validate and capture the headers forwarded to C1."""
        import c9_jokes.app as c9_mod
        captured = {}

        async def capture_post(url, **kwargs):
            captured["headers"] = dict(kwargs.get("headers", {}))
            captured["json"] = kwargs.get("json", {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            mock_resp.text = ""
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post("/api/validate", json=payload)
        return r, captured

    def test_validate_thinking_deep_sends_x_chat_mode(self, c9_app):
        """Selecting Think Deeper must send X-Chat-Mode: deep, not X-Work-Mode."""
        r, captured = self._capture_validate(c9_app, {
            "prompt": "Joke",
            "agent_ids": ["c9-jokes"],
            "chat_mode": "deep",
            "work_mode": "work",
        })
        assert r.status_code == 200
        assert captured["headers"].get("X-Chat-Mode") == "deep"
        assert captured["headers"].get("X-Work-Mode") == "work"

    def test_validate_work_mode_web_sends_x_work_mode(self, c9_app):
        """Work/Web toggle (web) must send X-Work-Mode: web on its own header."""
        r, captured = self._capture_validate(c9_app, {
            "prompt": "Joke",
            "agent_ids": ["c9-jokes"],
            "chat_mode": "auto",
            "work_mode": "web",
        })
        assert r.status_code == 200
        assert captured["headers"].get("X-Work-Mode") == "web"
        # Thinking mode should NOT bleed into X-Work-Mode
        assert captured["headers"].get("X-Chat-Mode") == "auto"

    def test_validate_work_mode_is_not_sent_as_chat_mode(self, c9_app):
        """Bug regression: 'work' must never appear as X-Chat-Mode value."""
        r, captured = self._capture_validate(c9_app, {
            "prompt": "Joke",
            "agent_ids": ["c9-jokes"],
            "chat_mode": "quick",
            "work_mode": "work",
        })
        assert r.status_code == 200
        # X-Chat-Mode must be the thinking key, not 'work'
        assert captured["headers"].get("X-Chat-Mode") == "quick"
        assert captured["headers"].get("X-Chat-Mode") != "work"

    def test_validate_forwards_attachments(self, c9_app):
        """Attachments array must be forwarded as file_ref content parts to C1."""
        import c9_jokes.app as c9_mod
        captured_json = {}

        async def capture_post(url, **kwargs):
            captured_json.update(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            mock_resp.text = ""
            return mock_resp

        # Pre-load a fake file_id into the C1 _file_store via upload mock
        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post("/api/validate", json={
                "prompt": "Summarise this",
                "agent_ids": ["c9-jokes"],
                "attachments": [{"file_id": "fid_xyz", "filename": "doc.txt"}],
            })
        assert r.status_code == 200
        # The message content should be a list with text + file_ref parts
        messages = captured_json.get("messages", [])
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        types = [p["type"] for p in content]
        assert "text" in types
        assert "file_ref" in types
        fref = next(p for p in content if p["type"] == "file_ref")
        assert fref["file_id"] == "fid_xyz"


# ── /api/status tests ─────────────────────────────────────────────────────────

class TestC9ApiStatus:
    def test_api_status_returns_200(self, c9_app):
        r = c9_app.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        # /api/status returns a dict keyed by agent ID plus a "ts" timestamp key
        assert isinstance(body, dict)
        assert "ts" in body
        agent_probes = {k: v for k, v in body.items() if k != "ts"}
        assert len(agent_probes) > 0
        for _agent_id, probe in agent_probes.items():
            assert "http_status" in probe
            assert "name" in probe


# ── /api/upload tests ─────────────────────────────────────────────────────────

class TestC9ApiUpload:
    """Test that C9's /api/upload correctly proxies to C1 /v1/files."""

    def _make_upload_response(self, file_type="text", file_id="abc123", preview="Hello world"):
        return {
            "ok": True,
            "file_id": file_id,
            "type": file_type,
            "filename": "test.txt",
            "size": 11,
            "preview": preview,
        }

    def test_upload_txt_returns_ok(self, c9_app):
        import c9_jokes.app as c9_mod
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=self._make_upload_response())
        mock_http = _make_mock_http(self._make_upload_response())
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("note.txt", b"Hello world", "text/plain")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["file_id"] == "abc123"
        assert body["type"] == "text"

    def test_upload_png_returns_image_type(self, c9_app):
        import c9_jokes.app as c9_mod
        img_response = self._make_upload_response(file_type="image", preview=None)
        img_response["filename"] = "photo.png"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=img_response)
        mock_http = _make_mock_http(img_response)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["type"] == "image"

    def test_upload_c1_error_propagated(self, c9_app):
        """When C1 returns 400, C9 should propagate the error status."""
        import c9_jokes.app as c9_mod
        error_body = {"detail": "Unsupported file type"}
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json = MagicMock(return_value=error_body)
        mock_resp.text = '{"detail": "Unsupported file type"}'
        mock_http = _make_mock_http(error_body)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("virus.exe", b"\x00\x01", "application/octet-stream")},
            )
        assert r.status_code == 400
        body = r.json()
        assert body["ok"] is False
        assert "Unsupported" in (body.get("error") or "")

    def test_upload_then_chat_with_attachment(self, c9_app):
        """Full flow: upload a file, then reference it in a chat message."""
        import c9_jokes.app as c9_mod

        upload_response = self._make_upload_response(file_id="file999", preview="My secret note")
        chat_response = _make_c1_ok("The file says: My secret note")

        call_count = {"n": 0}

        async def fake_post(url, *, headers=None, json=None, files=None, timeout=None, **kw):
            call_count["n"] += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if files is not None:
                # This is the upload call
                mock_resp.json = MagicMock(return_value=upload_response)
            else:
                # This is the chat call
                mock_resp.json = MagicMock(return_value=chat_response)
            return mock_resp

        mock_http = MagicMock()
        mock_http.post = fake_post
        mock_http.get  = AsyncMock(return_value=MagicMock(status_code=200, json=MagicMock(return_value={})))
        mock_http.is_closed = False

        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            # Step 1: Upload
            r_upload = c9_app.post(
                "/api/upload",
                files={"file": ("note.txt", b"My secret note", "text/plain")},
            )
            assert r_upload.status_code == 200
            assert r_upload.json()["file_id"] == "file999"

            # Step 2: Chat with the attachment
            r_chat = c9_app.post("/api/chat", json={
                "agent_id": "c9-jokes",
                "prompt": "What does the file say?",
                "attachments": [{"file_id": "file999", "filename": "note.txt"}],
            })
            assert r_chat.status_code == 200
            assert r_chat.json()["ok"] is True

        assert call_count["n"] == 2, f"Expected 2 HTTP calls (upload + chat), got {call_count['n']}"

    def test_upload_pdf_shows_preview(self, c9_app):
        """PDF upload should return a text preview from C1."""
        import c9_jokes.app as c9_mod
        pdf_response = self._make_upload_response(
            file_type="text", file_id="pdf001",
            preview="Revenue Q1: $1M  Revenue Q2: $2M"
        )
        pdf_response["filename"] = "report.pdf"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=pdf_response)
        mock_http = _make_mock_http(pdf_response)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "preview" in body
        assert body["preview"] is not None
