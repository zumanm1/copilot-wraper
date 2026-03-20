"""
Pydantic models matching OpenAI API schema for chat completions.
"""
from __future__ import annotations

import time
from typing import Literal
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A chat message in OpenAI format."""
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart] | None = None
    name: str | None = None


class ContentPart(BaseModel):
    """Content part for multimodal messages."""
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: ImageURL | None = None


class ImageURL(BaseModel):
    """Image URL for multimodal messages."""
    url: str
    detail: Literal["auto", "low", "high"] = "auto"


class ChatCompletionRequest(BaseModel):
    """Request model matching OpenAI's chat completion request."""
    model: str = "copilot"
    messages: list[ChatMessage]
    temperature: float = 0.7
    top_p: float = 1.0
    n: int = 1
    stream: bool = False
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    logit_bias: dict[str, float] | None = None
    user: str | None = None


class UsageInfo(BaseModel):
    """Token usage information."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    """A single choice in the response."""
    index: int = 0
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls"] | None = "stop"


class ChatCompletionResponse(BaseModel):
    """Response model matching OpenAI's chat completion response."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{int(time.time())}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "copilot"
    choices: list[ChatCompletionChoice]
    usage: UsageInfo = Field(default_factory=UsageInfo)
    system_fingerprint: str | None = None


class ChatCompletionChunkDelta(BaseModel):
    """Delta for streaming chunks."""
    role: str | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    """A single chunk choice in streaming."""
    index: int = 0
    delta: ChatCompletionChunkDelta = Field(default_factory=ChatCompletionChunkDelta)
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    """Streaming chunk matching OpenAI's SSE format."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{int(time.time())}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "copilot"
    choices: list[ChatCompletionChunkChoice]
    system_fingerprint: str | None = None


class ModelInfo(BaseModel):
    """Model information."""
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "microsoft"


class ModelList(BaseModel):
    """List of available models."""
    object: str = "list"
    data: list[ModelInfo]


class ErrorResponse(BaseModel):
    """Error response matching OpenAI format."""
    error: ErrorDetail


class ErrorDetail(BaseModel):
    """Error detail."""
    message: str
    type: str
    code: str | None = None


# ─────────────────────────────────────────────────────────────────────
# Agent Management Models
# ─────────────────────────────────────────────────────────────────────

class AgentStartRequest(BaseModel):
    """Request body for POST /v1/agent/start."""
    system_prompt: str | None = Field(
        default=None,
        description="Optional custom system prompt for the agent. "
                    "Defaults to the built-in Copilot agent prompt.",
    )


class AgentStartResponse(BaseModel):
    """Response from POST /v1/agent/start."""
    session_id: str
    status:     str
    started_at: str
    message:    str


class AgentStopResponse(BaseModel):
    """Response from POST /v1/agent/stop."""
    session_id:      str | None
    status:          str
    tasks_total:     int
    tasks_completed: int
    tasks_failed:    int
    message:         str


class AgentPauseResponse(BaseModel):
    """Response from POST /v1/agent/pause."""
    session_id: str
    status:     str
    paused_at:  str
    message:    str


class AgentResumeResponse(BaseModel):
    """Response from POST /v1/agent/resume."""
    session_id: str
    status:     str
    resumed_at: str
    message:    str


class AgentTaskRequest(BaseModel):
    """Request body for POST /v1/agent/task."""
    task: str = Field(
        ...,
        description="The task or question to give the agent. e.g. 'What is 1 + 1?'",
        min_length=1,
    )
    stream: bool = Field(
        default=False,
        description="If true, stream the response as Server-Sent Events.",
    )


class AgentTaskResponse(BaseModel):
    """Response from POST /v1/agent/task (non-streaming)."""
    task_id:      str
    session_id:   str | None
    status:       str
    prompt:       str
    result:       str | None
    error:        str | None
    created_at:   str
    completed_at: str | None


class AgentStatusResponse(BaseModel):
    """Response from GET /v1/agent/status."""
    status:             str
    session_id:         str | None
    started_at:         str | None
    paused_at:          str | None
    tasks_total:        int
    tasks_completed:    int
    tasks_failed:       int
    tasks_pending_busy: int


class AgentHistoryResponse(BaseModel):
    """Response from GET /v1/agent/history."""
    session_id: str | None
    tasks:      list[dict]
    total:      int


class AgentClearHistoryResponse(BaseModel):
    """Response from DELETE /v1/agent/history."""
    cleared: int
    message: str


# ─────────────────────────────────────────────────────────────────────
# Anthropic-compatible Models  (POST /v1/messages)
# ─────────────────────────────────────────────────────────────────────

class AnthropicContentBlock(BaseModel):
    """A content block in Anthropic message format."""
    type: Literal["text"] = "text"
    text: str = ""


class AnthropicMessage(BaseModel):
    """A message in Anthropic format (role + content list)."""
    role: Literal["user", "assistant"]
    content: str | list[AnthropicContentBlock]


class AnthropicRequest(BaseModel):
    """Request body for POST /v1/messages (Anthropic SDK format)."""
    model: str = "claude-3-5-sonnet-20241022"
    messages: list[AnthropicMessage]
    system: str | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = False


class AnthropicUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicResponse(BaseModel):
    """Response body matching Anthropic Messages API format."""
    id: str = Field(default_factory=lambda: f"msg_{int(time.time())}")
    type: str = "message"
    role: str = "assistant"
    content: list[AnthropicContentBlock]
    model: str
    stop_reason: str = "end_turn"
    stop_sequence: str | None = None
    usage: AnthropicUsage = Field(default_factory=AnthropicUsage)
