"""
Unit tests for AgentManager state machine and task execution.
CopilotBackend is mocked so no real network I/O occurs.
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_agent_manager():
    """Give each test a fresh AgentManager singleton."""
    import agent_manager as am
    am._agent_manager = None
    yield
    am._agent_manager = None


@pytest.fixture
def mock_backend():
    b = AsyncMock()
    b.chat_completion = AsyncMock(return_value="42")
    b.close = AsyncMock()
    b.reset_conversation = AsyncMock()

    async def _stream(*a, **kw):
        for t in ["Forty", "-", "two"]:
            yield t

    b.chat_completion_stream = _stream
    return b


@pytest.fixture
def manager(mock_backend):
    with patch("agent_manager.CopilotBackend", return_value=mock_backend):
        from agent_manager import get_agent_manager
        return get_agent_manager(), mock_backend


# ── Lifecycle state transitions ──────────────────────────────────────

async def test_initial_state_is_stopped(manager):
    mgr, _ = manager
    assert mgr.status.value == "stopped"
    assert mgr.session_id is None


async def test_start_transitions_to_running(manager):
    mgr, _ = manager
    result = await mgr.start()
    assert result["status"] == "running"
    assert result["session_id"].startswith("agent-")
    assert mgr.status.value == "running"


async def test_double_start_raises(manager):
    mgr, _ = manager
    await mgr.start()
    with pytest.raises(ValueError, match="already"):
        await mgr.start()


async def test_stop_from_stopped_raises(manager):
    mgr, _ = manager
    with pytest.raises(ValueError, match="already stopped"):
        await mgr.stop()


async def test_stop_returns_summary(manager):
    mgr, _ = manager
    await mgr.start()
    result = await mgr.stop()
    assert result["status"] == "stopped"
    assert "tasks_total" in result
    assert mgr.status.value == "stopped"


async def test_pause_and_resume(manager):
    mgr, _ = manager
    await mgr.start()
    result = await mgr.pause()
    assert result["status"] == "paused"
    assert mgr.status.value == "paused"
    result = await mgr.resume()
    assert result["status"] == "running"


async def test_pause_when_stopped_raises(manager):
    mgr, _ = manager
    with pytest.raises(ValueError):
        await mgr.pause()


async def test_resume_when_running_raises(manager):
    mgr, _ = manager
    await mgr.start()
    with pytest.raises(ValueError, match="Cannot resume"):
        await mgr.resume()


# ── Task execution ───────────────────────────────────────────────────

async def test_task_when_stopped_raises(manager):
    mgr, _ = manager
    with pytest.raises(ValueError, match="not started"):
        await mgr.run_task("anything")


async def test_task_when_paused_raises(manager):
    mgr, _ = manager
    await mgr.start()
    await mgr.pause()
    with pytest.raises(ValueError, match="paused"):
        await mgr.run_task("anything")


async def test_task_completes_and_returns_to_running(manager):
    mgr, backend = manager
    await mgr.start()
    task = await mgr.run_task("What is 6×7?")
    assert task.status.value == "completed"
    assert task.result == "42"
    assert mgr.status.value == "running"


async def test_first_task_includes_system_prompt(manager):
    mgr, backend = manager
    await mgr.start()
    await mgr.run_task("First task")
    call_args = backend.chat_completion.call_args[1]["prompt"]
    assert "User task:" in call_args


async def test_second_task_no_system_prompt(manager):
    mgr, backend = manager
    await mgr.start()
    await mgr.run_task("First")
    await mgr.run_task("Second")
    second_call = backend.chat_completion.call_args_list[1][1]["prompt"]
    # Second call should be raw task text only
    assert second_call == "Second"


async def test_failed_task_resets_backend(manager):
    mgr, backend = manager
    backend.chat_completion = AsyncMock(side_effect=RuntimeError("network error"))
    await mgr.start()
    task = await mgr.run_task("Fail me")
    assert task.status.value == "failed"
    assert "network error" in task.error
    assert mgr.status.value == "running"  # back to running after failure


async def test_streaming_task_yields_tokens(manager):
    mgr, _ = manager
    await mgr.start()
    tokens = []
    async for t in mgr.run_task_stream("What is the answer?"):
        tokens.append(t)
    assert tokens == ["Forty", "-", "two"]


# ── History ──────────────────────────────────────────────────────────

async def test_history_grows_with_tasks(manager):
    mgr, _ = manager
    await mgr.start()
    await mgr.run_task("T1")
    await mgr.run_task("T2")
    history = mgr.get_history()
    assert len(history) == 2


async def test_get_task_by_id(manager):
    mgr, _ = manager
    await mgr.start()
    task = await mgr.run_task("Find me")
    found = mgr.get_task(task.task_id)
    assert found is not None
    assert found["task_id"] == task.task_id


async def test_get_task_unknown_id_returns_none(manager):
    mgr, _ = manager
    assert mgr.get_task("nonexistent-id") is None


async def test_clear_history(manager):
    mgr, _ = manager
    await mgr.start()
    await mgr.run_task("T1")
    result = mgr.clear_history()
    assert result["cleared"] == 1
    assert mgr.get_history() == []


async def test_history_cap_enforced(manager, mock_backend):
    """History must not exceed AGENT_MAX_HISTORY entries."""
    with patch("agent_manager.config.AGENT_MAX_HISTORY", 3):
        mgr, _ = manager
        await mgr.start()
        for i in range(5):
            await mgr.run_task(f"Task {i}")
        assert len(mgr._task_history) <= 3
