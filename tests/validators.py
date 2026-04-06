"""
Reusable OpenAI schema validators for the test suite.
All functions raise AssertionError on failure so they work with pytest.
"""
from __future__ import annotations
import json


def validate_chat_completion_response(body: dict, model: str = "copilot") -> None:
    """Validate a non-streaming /v1/chat/completions response."""
    required = {"id", "object", "created", "model", "choices", "usage"}
    missing = required - body.keys()
    assert not missing, f"Response missing fields: {missing}"

    assert body["object"] == "chat.completion", f"Wrong object: {body['object']}"
    assert body["id"].startswith("chatcmpl-"), f"Bad id format: {body['id']}"
    assert isinstance(body["created"], int) and body["created"] > 0
    assert body["model"] == model, f"Model mismatch: {body['model']!r} != {model!r}"

    choices = body["choices"]
    assert isinstance(choices, list) and len(choices) > 0, "choices must be non-empty list"
    choice = choices[0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert isinstance(choice["message"]["content"], str)
    assert choice["finish_reason"] in ("stop", "length", "tool_calls")

    usage = body["usage"]
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert isinstance(usage[field], int) and usage[field] >= 0, \
            f"usage.{field} must be non-negative int"
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def validate_sse_chunk(line: str, chat_id: str | None = None) -> dict | None:
    """Parse and validate a single SSE data line. Returns None for [DONE]."""
    assert line.startswith("data: "), f"SSE line must start with 'data: ': {line!r}"
    payload = line[len("data: "):]
    if payload == "[DONE]":
        return None
    body = json.loads(payload)
    assert body["object"] == "chat.completion.chunk"
    if chat_id:
        assert body["id"] == chat_id, f"chunk id mismatch: {body['id']!r}"
    assert "choices" in body and len(body["choices"]) > 0
    return body


def validate_sse_stream(raw_sse: str) -> list[str]:
    """
    Validate a full SSE stream body and return all content tokens.
    Expects: init chunk (role:assistant), content chunks, stop chunk, [DONE].
    """
    lines = [l for l in raw_sse.strip().splitlines() if l.startswith("data: ")]
    assert len(lines) >= 2, "Stream must have at least init + [DONE]"

    tokens: list[str] = []
    chat_id = None
    saw_done = False

    for i, line in enumerate(lines):
        if line == "data: [DONE]":
            saw_done = True
            assert i == len(lines) - 1, "[DONE] must be the last line"
            continue

        chunk = validate_sse_chunk(line)
        if chunk is None:
            continue

        if chat_id is None:
            chat_id = chunk["id"]

        delta = chunk["choices"][0]["delta"]
        finish = chunk["choices"][0].get("finish_reason")

        if i == 0:
            assert delta.get("role") == "assistant", "First chunk must set role=assistant"
        elif finish == "stop":
            assert delta == {}, "Stop chunk must have empty delta"
        elif "content" in delta and delta["content"]:
            tokens.append(delta["content"])

    assert saw_done, "Stream must end with data: [DONE]"
    return tokens


def validate_models_list(body: dict) -> None:
    """Validate a /v1/models response."""
    assert body["object"] == "list"
    data = body["data"]
    assert isinstance(data, list) and len(data) > 0
    for model in data:
        assert "id" in model
        assert model["object"] == "model"
        assert isinstance(model["created"], int)
        assert "owned_by" in model


def validate_agent_task_response(body: dict) -> None:
    """Validate a /v1/agent/task non-streaming response."""
    for field in ("task_id", "session_id", "status", "prompt", "created_at"):
        assert field in body, f"Missing field: {field}"
    assert body["task_id"].startswith("task-")
    assert body["session_id"].startswith("agent-")
    assert body["status"] in ("completed", "failed", "running", "pending", "cancelled")
    assert isinstance(body["prompt"], str)
    assert isinstance(body["created_at"], str)
