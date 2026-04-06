"""
Unit tests for Pydantic models in models.py.
No mocks needed — pure validation logic.
"""
import pytest
from pydantic import ValidationError
from models import (
    ChatMessage, ChatCompletionRequest, ChatCompletionResponse,
    ChatCompletionChoice, UsageInfo, ContentPart, ImageURL,
    AgentStartRequest, AgentTaskRequest,
)


class TestChatMessage:
    def test_valid_user_message(self):
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_valid_assistant_message(self):
        msg = ChatMessage(role="assistant", content="Hi there")
        assert msg.role == "assistant"

    def test_valid_system_message(self):
        msg = ChatMessage(role="system", content="You are helpful.")
        assert msg.role == "system"

    def test_none_content_allowed(self):
        msg = ChatMessage(role="assistant", content=None)
        assert msg.content is None

    def test_list_content_allowed(self):
        msg = ChatMessage(role="user", content=[ContentPart(type="text", text="hi")])
        assert isinstance(msg.content, list)


class TestChatCompletionRequest:
    def test_requires_messages(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="copilot")

    def test_default_model_when_omitted(self):
        req = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        assert req.model == "copilot"

    def test_valid_minimal_request(self):
        req = ChatCompletionRequest(
            model="copilot",
            messages=[ChatMessage(role="user", content="Hi")]
        )
        assert req.model == "copilot"
        assert req.stream is False  # default

    def test_stream_flag(self):
        req = ChatCompletionRequest(
            model="copilot",
            messages=[ChatMessage(role="user", content="Hi")],
            stream=True,
        )
        assert req.stream is True

    def test_empty_messages_allowed_at_model_level(self):
        # Validation of empty messages is done at the endpoint, not the model
        req = ChatCompletionRequest(model="copilot", messages=[])
        assert req.messages == []


class TestContentPart:
    def test_text_part(self):
        part = ContentPart(type="text", text="hello")
        assert part.type == "text"
        assert part.text == "hello"

    def test_image_url_part(self):
        part = ContentPart(
            type="image_url",
            image_url=ImageURL(url="data:image/png;base64,abc", detail="auto"),
        )
        assert part.type == "image_url"
        assert part.image_url.url.startswith("data:")

    def test_file_ref_part(self):
        part = ContentPart(type="file_ref", file_id="abc123", filename="report.pdf")
        assert part.type == "file_ref"
        assert part.file_id == "abc123"
        assert part.filename == "report.pdf"

    def test_file_ref_without_filename(self):
        part = ContentPart(type="file_ref", file_id="xyz")
        assert part.file_id == "xyz"
        assert part.filename is None

    def test_invalid_type_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ContentPart(type="video_url", text="bad")


class TestUsageInfo:
    def test_zero_tokens_valid(self):
        u = UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        assert u.total_tokens == 0

    def test_positive_tokens(self):
        u = UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        assert u.prompt_tokens == 10


class TestAgentModels:
    def test_agent_start_default_system_prompt(self):
        req = AgentStartRequest()
        assert req.system_prompt is None

    def test_agent_start_custom_prompt(self):
        req = AgentStartRequest(system_prompt="Be concise.")
        assert req.system_prompt == "Be concise."

    def test_agent_task_requires_task_field(self):
        with pytest.raises(ValidationError):
            AgentTaskRequest()

    def test_agent_task_valid(self):
        req = AgentTaskRequest(task="What is 2+2?")
        assert req.task == "What is 2+2?"
        assert req.stream is False
