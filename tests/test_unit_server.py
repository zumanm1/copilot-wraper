"""
Unit tests for server.py utilities and HTTP endpoints.
Uses FastAPI TestClient with SydneyClient mocked.
"""
from __future__ import annotations
import json
import os
import base64
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from tests.validators import (
    validate_chat_completion_response, validate_models_list,
    validate_sse_stream, validate_agent_task_response,
)


# ── extract_user_prompt ──────────────────────────────────────────────

class TestExtractUserPrompt:
    def _msg(self, role, content):
        from models import ChatMessage
        return ChatMessage(role=role, content=content)

    def test_joins_multiple_user_turns(self):
        from server import extract_user_prompt
        msgs = [self._msg("user", "first"), self._msg("user", "second")]
        assert extract_user_prompt(msgs) == "first\nsecond"

    def test_returns_empty_for_no_messages(self):
        from server import extract_user_prompt
        assert extract_user_prompt([]) == ""

    def test_system_and_user_in_transcript(self):
        from server import extract_user_prompt
        msgs = [self._msg("system", "Be helpful."), self._msg("user", "Hello")]
        result = extract_user_prompt(msgs)
        assert "[System]: Be helpful." in result
        assert "Hello" in result

    def test_assistant_turn_included(self):
        from server import extract_user_prompt
        msgs = [
            self._msg("user", "Hi"),
            self._msg("assistant", "Hello there"),
            self._msg("user", "Bye"),
        ]
        out = extract_user_prompt(msgs)
        assert "[Assistant]: Hello there" in out
        assert "Bye" in out

    def test_system_only_transcript(self):
        from server import extract_user_prompt
        msgs = [self._msg("system", "You are helpful.")]
        result = extract_user_prompt(msgs)
        assert "[System]:" in result


# ── extract_image ────────────────────────────────────────────────────

class TestExtractImage:
    def _make_image_message(self):
        from models import ChatMessage, ContentPart, ImageURL
        img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        b64 = base64.b64encode(img_bytes).decode()
        return ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="Look at this"),
                ContentPart(type="image_url", image_url=ImageURL(url=f"data:image/png;base64,{b64}")),
            ],
        )

    def test_extracts_image_to_temp_file(self):
        from server import extract_image
        msg = self._make_image_message()
        path = extract_image([msg])
        assert path is not None
        assert os.path.exists(path)
        os.unlink(path)

    def test_returns_none_when_no_image(self):
        from server import extract_image
        from models import ChatMessage
        msgs = [ChatMessage(role="user", content="no image here")]
        assert extract_image(msgs) is None

    def test_cleanup_helper_deletes_file(self):
        from server import _cleanup_attachment
        fd, path = tempfile.mkstemp()
        os.close(fd)
        assert os.path.exists(path)
        _cleanup_attachment(path)
        assert not os.path.exists(path)

    def test_cleanup_helper_handles_none(self):
        from server import _cleanup_attachment
        _cleanup_attachment(None)  # should not raise

    def test_cleanup_helper_handles_missing_file(self):
        from server import _cleanup_attachment
        _cleanup_attachment("/tmp/nonexistent_file_xyz_12345.png")  # should not raise


# ── HTTP Endpoints ───────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_200(self, test_app):
        r = test_app.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["service"] == "copilot-openai-wrapper"


class TestModelsEndpoint:
    def test_models_returns_200(self, test_app):
        r = test_app.get("/v1/models")
        assert r.status_code == 200

    def test_models_schema(self, test_app):
        r = test_app.get("/v1/models")
        validate_models_list(r.json())

    def test_models_has_six_entries(self, test_app):
        r = test_app.get("/v1/models")
        assert len(r.json()["data"]) == 6


class TestChatCompletionEndpoint:
    def test_missing_messages_returns_422(self, test_app):
        r = test_app.post("/v1/chat/completions", json={"model": "copilot"})
        assert r.status_code == 422

    def test_empty_messages_returns_400(self, test_app):
        r = test_app.post("/v1/chat/completions",
                          json={"model": "copilot", "messages": []})
        assert r.status_code == 400

    def test_get_method_returns_405(self, test_app):
        r = test_app.get("/v1/chat/completions")
        assert r.status_code == 405

    def test_valid_request_returns_200(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        assert r.status_code == 200

    def test_response_schema(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        validate_chat_completion_response(r.json(), model="copilot")

    def test_nonexistent_endpoint_returns_404(self, test_app):
        r = test_app.get("/v1/nonexistent")
        assert r.status_code == 404


class TestCacheStatsEndpoint:
    def test_cache_stats_returns_200(self, test_app):
        r = test_app.get("/v1/cache/stats")
        assert r.status_code == 200

    def test_cache_stats_schema(self, test_app):
        r = test_app.get("/v1/cache/stats")
        body = r.json()
        for field in ("hits", "misses", "size", "maxsize", "ttl_seconds"):
            assert field in body, f"Missing field: {field}"


class TestAgentEndpoints:
    def test_task_before_start_returns_409(self, test_app):
        r = test_app.post("/v1/agent/task",
                          json={"task": "What is 2+2?"})
        assert r.status_code == 409

    def test_stop_before_start_returns_409(self, test_app):
        r = test_app.post("/v1/agent/stop")
        assert r.status_code == 409

    def test_pause_before_start_returns_409(self, test_app):
        r = test_app.post("/v1/agent/pause")
        assert r.status_code == 409

    def test_status_always_200(self, test_app):
        r = test_app.get("/v1/agent/status")
        assert r.status_code == 200
        assert "status" in r.json()

    def test_history_before_start(self, test_app):
        r = test_app.get("/v1/agent/history")
        assert r.status_code == 200


# ── Thinking Mode (resolve_chat_style_with_mode) ─────────────────────

class TestThinkingMode:
    def test_auto_maps_to_smart(self):
        from server import resolve_chat_style_with_mode
        assert resolve_chat_style_with_mode("copilot", 0.7, "auto") == "smart"

    def test_quick_maps_to_balanced(self):
        from server import resolve_chat_style_with_mode
        assert resolve_chat_style_with_mode("copilot", 0.7, "quick") == "balanced"

    def test_deep_maps_to_reasoning(self):
        from server import resolve_chat_style_with_mode
        assert resolve_chat_style_with_mode("copilot", 0.7, "deep") == "reasoning"

    def test_empty_mode_falls_through_to_model_map(self):
        from server import resolve_chat_style_with_mode
        # "copilot" is in MODEL_MAP → "smart" regardless of temperature
        assert resolve_chat_style_with_mode("copilot", 0.7, "") == "smart"

    def test_empty_mode_falls_through_temperature(self):
        from server import resolve_chat_style_with_mode
        # low temp → precise when model not in MODEL_MAP
        assert resolve_chat_style_with_mode("unknown-model", 0.1, "") == "precise"

    def test_unknown_mode_falls_through(self):
        from server import resolve_chat_style_with_mode
        assert resolve_chat_style_with_mode("copilot", 0.7, "turbo") == "smart"

    def test_x_chat_mode_header_sets_style(self, test_app):
        """X-Chat-Mode: deep → backend.style=reasoning visible in gen_note header."""
        r = test_app.post(
            "/v1/chat/completions",
            headers={"X-Chat-Mode": "deep"},
            json={"model": "copilot", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert r.status_code == 200
        note = r.headers.get("x-generation-params-note", "")
        assert "copilot_style=reasoning" in note
        assert "thinking_mode=deep" in note

    def test_x_chat_mode_quick(self, test_app):
        r = test_app.post(
            "/v1/chat/completions",
            headers={"X-Chat-Mode": "quick"},
            json={"model": "copilot", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert r.status_code == 200
        note = r.headers.get("x-generation-params-note", "")
        assert "copilot_style=balanced" in note

    def test_x_work_mode_in_gen_note(self, test_app):
        """X-Work-Mode: web visible in gen_note header."""
        r = test_app.post(
            "/v1/chat/completions",
            headers={"X-Work-Mode": "web"},
            json={"model": "copilot", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert r.status_code == 200
        note = r.headers.get("x-generation-params-note", "")
        assert "work_mode=web" in note


# ── File Ref in extract_user_prompt ──────────────────────────────────

class TestExtractUserPromptFileRef:
    def test_file_ref_injects_text(self):
        from server import extract_user_prompt, _file_store
        fid = "test_file_001"
        _file_store[fid] = {"type": "text", "text": "Revenue: $1M", "filename": "report.pdf", "size": 100}
        from models import ChatMessage, ContentPart
        msg = ChatMessage(role="user", content=[
            ContentPart(type="text", text="Summarise this"),
            ContentPart(type="file_ref", file_id=fid, filename="report.pdf"),
        ])
        result = extract_user_prompt([msg])
        assert "Summarise this" in result
        assert "[Attached file: report.pdf]" in result
        assert "Revenue: $1M" in result
        del _file_store[fid]

    def test_missing_file_ref_does_not_crash(self):
        from server import extract_user_prompt
        from models import ChatMessage, ContentPart
        msg = ChatMessage(role="user", content=[
            ContentPart(type="text", text="hi"),
            ContentPart(type="file_ref", file_id="nonexistent_xyz"),
        ])
        result = extract_user_prompt([msg])
        assert "hi" in result  # text part still included


# ── File Upload Endpoint (/v1/files) ──────────────────────────────────

class TestFileUploadEndpoint:
    def test_upload_text_file(self, test_app):
        r = test_app.post(
            "/v1/files",
            files={"file": ("hello.txt", b"Hello world", "text/plain")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "text"
        assert body["filename"] == "hello.txt"
        assert "file_id" in body
        assert "preview" in body

    def test_upload_unsupported_type_returns_400(self, test_app):
        r = test_app.post(
            "/v1/files",
            files={"file": ("virus.exe", b"\x00\x01", "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_upload_oversized_returns_413(self, test_app):
        import server as srv
        original = srv.config.MAX_FILE_BYTES
        srv.config.MAX_FILE_BYTES = 5
        try:
            r = test_app.post(
                "/v1/files",
                files={"file": ("big.txt", b"0123456789", "text/plain")},
            )
            assert r.status_code == 413
        finally:
            srv.config.MAX_FILE_BYTES = original

    def test_uploaded_file_id_resolves_in_prompt(self, test_app):
        # Upload a text file, then send it in a chat
        upload = test_app.post(
            "/v1/files",
            files={"file": ("note.txt", b"My secret note", "text/plain")},
        )
        assert upload.status_code == 200
        fid = upload.json()["file_id"]

        chat = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "What does the file say?"},
                {"type": "file_ref", "file_id": fid, "filename": "note.txt"},
            ]}],
        })
        assert chat.status_code == 200
        # The mocked backend returns a canned response; we just verify no crash
        body = chat.json()
        assert body["choices"][0]["message"]["role"] == "assistant"

    def test_upload_png_returns_image_type(self, test_app):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        r = test_app.post(
            "/v1/files",
            files={"file": ("img.png", png_header, "image/png")},
        )
        assert r.status_code == 200
        assert r.json()["type"] == "image"
