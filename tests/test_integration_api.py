"""
Integration tests for the full API lifecycle using FastAPI TestClient.
Copilot WebSocket I/O is stubbed via `conftest` (`_ws_stream` patch) — no real network calls.
"""
from __future__ import annotations
import json
import pytest
from unittest.mock import patch

from tests.validators import (
    validate_chat_completion_response, validate_models_list,
    validate_agent_task_response,
)


class TestFullChatCompletionRoundtrip:
    def test_non_streaming_response_schema(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        })
        assert r.status_code == 200
        body = r.json()
        validate_chat_completion_response(body, model="copilot")
        assert len(body["choices"][0]["message"]["content"]) > 0

    def test_system_plus_user_message(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [
                {"role": "system", "content": "You respond only in JSON"},
                {"role": "user", "content": "List two colors"},
            ],
        })
        assert r.status_code == 200
        validate_chat_completion_response(r.json(), model="copilot")

    def test_usage_tokens_are_consistent(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "Hello world"}],
        })
        usage = r.json()["usage"]
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]

    def test_model_echo_in_response(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.json()["model"] == "gpt-4"


class TestStreamingChatCompletion:
    def test_streaming_response_is_sse(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "stream": True,
        })
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")

    def test_streaming_response_ends_with_done(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "stream": True,
        })
        lines = r.text.strip().splitlines()
        assert "data: [DONE]" in lines

    def test_streaming_has_init_chunk_with_role(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        lines = [l for l in r.text.splitlines() if l.startswith("data: ") and l != "data: [DONE]"]
        first = json.loads(lines[0][len("data: "):])
        assert first["choices"][0]["delta"].get("role") == "assistant"

    def test_streaming_last_chunk_has_stop_reason(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        data_lines = [l for l in r.text.splitlines()
                      if l.startswith("data: ") and l != "data: [DONE]"]
        last = json.loads(data_lines[-1][len("data: "):])
        assert last["choices"][0]["finish_reason"] == "stop"

    def test_streaming_yields_multiple_content_chunks(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        assert r.status_code == 200
        data_lines = [l for l in r.text.splitlines()
                      if l.startswith("data: ") and l != "data: [DONE]"
                      and not l.startswith("data: {\"error\"")]
        # init + heartbeat + one chunk per stub token (Mocked,  Copilot,  response)
        content_deltas = [
            json.loads(l[len("data: "):])["choices"][0]["delta"].get("content")
            for l in data_lines
        ]
        non_empty = [c for c in content_deltas if c]
        assert len(non_empty) >= 3

    def test_streaming_max_tokens_sets_finish_reason_length(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "max_tokens": 2,
        })
        assert r.status_code == 200
        data_lines = [l for l in r.text.splitlines()
                      if l.startswith("data: ") and l != "data: [DONE]"]
        last = json.loads(data_lines[-1][len("data: "):])
        assert last["choices"][0]["finish_reason"] == "length"


class TestAnthropicMessages:
    def test_non_streaming_schema(self, test_app):
        r = test_app.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Say hello"}],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "assistant"
        assert body["type"] == "message"
        assert len(body["content"]) >= 1
        assert body["content"][0]["type"] == "text"
        assert len(body["content"][0]["text"]) > 0
        assert "usage" in body
        assert body["usage"]["input_tokens"] >= 0
        assert body["usage"]["output_tokens"] >= 0

    def test_streaming_emits_anthropic_events(self, test_app):
        r = test_app.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        types = []
        for line in r.text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                obj = json.loads(line[len("data: "):])
                types.append(obj.get("type"))
        assert "message_start" in types
        assert "content_block_delta" in types
        assert types[-1] == "message_stop"

    def test_streaming_error_has_no_success_tail(self, test_app):
        async def failing_stream(self, *args, **kwargs):
            raise RuntimeError("simulated stream failure")
            yield ""  # pragma: no cover

        with patch("copilot_backend.CopilotBackend.chat_completion_stream", failing_stream):
            r = test_app.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
        assert r.status_code == 200
        types = []
        for line in r.text.splitlines():
            if line.startswith("data: "):
                try:
                    obj = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    continue
                types.append(obj.get("type"))
        assert "error" in types
        assert "message_stop" not in types
        assert "content_block_stop" not in types


class TestAgentLifecycle:
    def test_full_lifecycle(self, test_app):
        # Start
        r = test_app.post("/v1/agent/start", json={})
        assert r.status_code == 200
        session_id = r.json()["session_id"]
        assert session_id.startswith("agent-")

        # Status → running
        r = test_app.get("/v1/agent/status")
        assert r.json()["status"] == "running"

        # Task
        r = test_app.post("/v1/agent/task", json={"task": "What is 7×6?"})
        assert r.status_code == 200
        validate_agent_task_response(r.json())
        task_id = r.json()["task_id"]

        # History has 1 entry
        r = test_app.get("/v1/agent/history")
        assert r.json()["total"] == 1

        # Get specific task
        r = test_app.get(f"/v1/agent/history/{task_id}")
        assert r.status_code == 200
        assert r.json()["task_id"] == task_id

        # Pause
        r = test_app.post("/v1/agent/pause")
        assert r.json()["status"] == "paused"

        # Task while paused → 409
        r = test_app.post("/v1/agent/task", json={"task": "should fail"})
        assert r.status_code == 409

        # Resume
        r = test_app.post("/v1/agent/resume")
        assert r.json()["status"] == "running"

        # Stop
        r = test_app.post("/v1/agent/stop")
        assert r.json()["tasks_total"] == 1

        # Double stop → 409
        r = test_app.post("/v1/agent/stop")
        assert r.status_code == 409

    def test_double_start_returns_409(self, test_app):
        test_app.post("/v1/agent/start", json={})
        r = test_app.post("/v1/agent/start", json={})
        assert r.status_code == 409

    def test_clear_history(self, test_app):
        test_app.post("/v1/agent/start", json={})
        test_app.post("/v1/agent/task", json={"task": "T1"})
        r = test_app.delete("/v1/agent/history")
        assert r.json()["cleared"] == 1
        r = test_app.get("/v1/agent/history")
        assert r.json()["total"] == 0

    def test_unknown_task_id_returns_404(self, test_app):
        r = test_app.get("/v1/agent/history/task-doesnotexist")
        assert r.status_code == 404


class TestErrorHandling:
    def test_missing_messages_field(self, test_app):
        r = test_app.post("/v1/chat/completions", json={"model": "copilot"})
        assert r.status_code == 422

    def test_no_user_message(self, test_app):
        r = test_app.post("/v1/chat/completions", json={
            "model": "copilot",
            "messages": [{"role": "system", "content": "Be helpful"}],
        })
        # system-only produces a prompt (the [System]: prefix), so 200 or 400
        # depending on implementation. Just verify it doesn't 500.
        assert r.status_code in (200, 400)
