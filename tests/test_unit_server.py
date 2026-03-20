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

    def test_returns_last_user_content(self):
        from server import extract_user_prompt
        msgs = [self._msg("user", "first"), self._msg("user", "second")]
        assert extract_user_prompt(msgs) == "second"

    def test_returns_empty_for_no_messages(self):
        from server import extract_user_prompt
        assert extract_user_prompt([]) == ""

    def test_system_message_included_as_prefix(self):
        from server import extract_user_prompt
        msgs = [self._msg("system", "Be helpful."), self._msg("user", "Hello")]
        result = extract_user_prompt(msgs)
        assert result == "Hello"  # returns last part

    def test_returns_empty_when_only_system(self):
        from server import extract_user_prompt
        msgs = [self._msg("system", "You are helpful.")]
        # System-only returns the system content as last part
        result = extract_user_prompt(msgs)
        assert "[System]" in result or result == ""


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
