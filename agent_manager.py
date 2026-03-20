"""
============================================================
Agent Manager — manages the AI agent lifecycle and tasks.

State machine:
  STOPPED ──start()──► RUNNING
  RUNNING ──pause()──► PAUSED
  PAUSED  ──resume()─► RUNNING
  RUNNING ──task()───► BUSY ──► RUNNING  (task completes/fails)
  RUNNING / PAUSED / BUSY ──stop()──► STOPPED

The manager wraps a persistent CopilotBackend so that
conversation history is maintained across tasks in the same session.
============================================================
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncGenerator, Optional

import httpx

from copilot_backend import CopilotBackend
import config

# ─────────────────────────── Enumerations ─────────────────────────────

class AgentStatus(str, Enum):
    STOPPED  = "stopped"
    RUNNING  = "running"
    PAUSED   = "paused"
    BUSY     = "busy"        # actively executing a task


class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


# ─────────────────────────── Tool definitions ─────────────────────────

AGENT_SYSTEM_PROMPT = """\
You are an intelligent AI agent powered by Microsoft Copilot.
You can answer questions, reason through problems, write and explain code,
analyse data, and assist with complex tasks.
You maintain full conversation context across multiple turns.
When solving math or logic questions, show your reasoning step by step.
Be concise, accurate, and helpful."""


def _tool_get_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _tool_get_weather(city: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://wttr.in/{city}?format=j1")
        if resp.status_code == 200:
            d = resp.json()["current_condition"][0]
            return (
                f"Weather in {city}: {d['weatherDesc'][0]['value']}, "
                f"{d['temp_C']}°C ({d['temp_F']}°F), "
                f"Humidity: {d['humidity']}%"
            )
        return f"Could not get weather for {city}"
    except Exception as exc:
        return f"Error: {exc}"


def _tool_list_directory(path: str) -> str:
    try:
        if not os.path.exists(path):
            return f"Path does not exist: {path}"
        files = os.listdir(path)
        return "\n".join(f"  {f}" for f in files[:20]) or "Empty directory"
    except Exception as exc:
        return f"Error: {exc}"


def _tool_run_python(code: str) -> str:
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or result.stderr or "No output"
    except subprocess.TimeoutExpired:
        return "Error: Execution timed out (30s)"
    except Exception as exc:
        return f"Error: {exc}"


def _tool_read_file(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()[:2000]
    except Exception as exc:
        return f"Error: {exc}"


TOOL_REGISTRY: dict[str, callable] = {
    "get_current_time": _tool_get_time,
    "get_weather":      _tool_get_weather,
    "list_directory":   _tool_list_directory,
    "run_python_code":  _tool_run_python,
    "read_file":        _tool_read_file,
}


# ─────────────────────────── Task record ──────────────────────────────

class AgentTask:
    """Represents a single task submitted to the agent."""

    def __init__(self, task_id: str, prompt: str):
        self.task_id   = task_id
        self.prompt    = prompt
        self.status    = TaskStatus.PENDING
        self.result:   Optional[str]      = None
        self.error:    Optional[str]      = None
        self.tool_calls: list[dict]       = []
        self.created_at   = datetime.now(timezone.utc)
        self.completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "task_id":      self.task_id,
            "prompt":       self.prompt,
            "status":       self.status,
            "result":       self.result,
            "error":        self.error,
            "tool_calls":   self.tool_calls,
            "created_at":   self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# ─────────────────────────── Agent Manager ────────────────────────────

class AgentManager:
    """
    Singleton that manages the agent's lifecycle.

    Thread-safety: all mutations are guarded by an asyncio.Lock.
    The underlying CopilotBackend + SydneyClient maintains WebSocket
    conversation history for the duration of a session.
    """

    def __init__(self):
        self._status:     AgentStatus       = AgentStatus.STOPPED
        self._session_id: Optional[str]     = None
        self._backend:    Optional[CopilotBackend] = None
        self._lock        = asyncio.Lock()
        self._task_history: list[AgentTask] = []
        self._started_at:  Optional[datetime] = None
        self._paused_at:   Optional[datetime] = None
        self._system_prompt: str = AGENT_SYSTEM_PROMPT

    # ── Public state properties ───────────────────────────────────────

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    # ── Lifecycle operations ──────────────────────────────────────────

    async def start(self, system_prompt: Optional[str] = None) -> dict:
        """Start a new agent session. Raises if already running."""
        async with self._lock:
            if self._status in (AgentStatus.RUNNING, AgentStatus.BUSY):
                raise ValueError(
                    f"Agent is already {self._status.value}. "
                    "Stop it first before starting a new session."
                )
            # Clean up any previous backend
            if self._backend:
                try:
                    await self._backend.close()
                except Exception:
                    pass

            self._session_id    = f"agent-{uuid.uuid4().hex[:12]}"
            self._system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
            self._backend       = CopilotBackend()
            self._task_history  = []
            self._status        = AgentStatus.RUNNING
            self._started_at    = datetime.now(timezone.utc)
            self._paused_at     = None

            return {
                "session_id": self._session_id,
                "status":     self._status.value,
                "started_at": self._started_at.isoformat(),
                "message":    "Agent started successfully.",
            }

    async def stop(self) -> dict:
        """Stop the agent and close the Copilot connection."""
        async with self._lock:
            if self._status == AgentStatus.STOPPED:
                raise ValueError("Agent is already stopped.")

            if self._backend:
                try:
                    await self._backend.close()
                except Exception:
                    pass
                self._backend = None

            summary = {
                "session_id":       self._session_id,
                "status":           AgentStatus.STOPPED.value,
                "tasks_total":      len(self._task_history),
                "tasks_completed":  sum(1 for t in self._task_history
                                        if t.status == TaskStatus.COMPLETED),
                "tasks_failed":     sum(1 for t in self._task_history
                                        if t.status == TaskStatus.FAILED),
                "message":          "Agent stopped successfully.",
            }
            self._status     = AgentStatus.STOPPED
            self._session_id = None
            self._started_at = None
            self._paused_at  = None
            return summary

    async def pause(self) -> dict:
        """Pause the agent. New task submissions will be rejected until resumed."""
        async with self._lock:
            if self._status == AgentStatus.BUSY:
                raise ValueError(
                    "Cannot pause while a task is running. "
                    "Wait for the task to finish."
                )
            if self._status != AgentStatus.RUNNING:
                raise ValueError(
                    f"Cannot pause agent with status: {self._status.value}"
                )
            self._status    = AgentStatus.PAUSED
            self._paused_at = datetime.now(timezone.utc)
            return {
                "session_id": self._session_id,
                "status":     self._status.value,
                "paused_at":  self._paused_at.isoformat(),
                "message":    "Agent paused. Submit /v1/agent/resume to continue.",
            }

    async def resume(self) -> dict:
        """Resume a paused agent."""
        async with self._lock:
            if self._status != AgentStatus.PAUSED:
                raise ValueError(
                    f"Cannot resume agent with status: {self._status.value}"
                )
            self._status    = AgentStatus.RUNNING
            self._paused_at = None
            return {
                "session_id": self._session_id,
                "status":     self._status.value,
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "message":    "Agent resumed successfully.",
            }

    # ── Task execution ────────────────────────────────────────────────

    async def run_task(self, task_prompt: str) -> AgentTask:
        """
        Submit a task to the running agent and await the result.
        Maintains full conversation history through the SydneyClient session.
        """
        # Pre-flight checks
        async with self._lock:
            self._check_can_run_task()
            self._status = AgentStatus.BUSY

        task = AgentTask(
            task_id=f"task-{uuid.uuid4().hex[:12]}",
            prompt=task_prompt,
        )
        task.status = TaskStatus.RUNNING
        self._task_history.append(task)
        if len(self._task_history) > config.AGENT_MAX_HISTORY:
            self._task_history = self._task_history[-config.AGENT_MAX_HISTORY:]

        try:
            # Prepend system prompt on first task so Copilot has context
            effective_prompt = (
                f"{self._system_prompt}\n\nUser task: {task_prompt}"
                if not self._task_history or len(self._task_history) == 1
                else task_prompt
            )

            response = await self._backend.chat_completion(
                prompt=effective_prompt,
            )

            task.status       = TaskStatus.COMPLETED
            task.result       = response
            task.completed_at = datetime.now(timezone.utc)

        except Exception as exc:
            task.status       = TaskStatus.FAILED
            task.error        = str(exc)
            task.completed_at = datetime.now(timezone.utc)
            # Reset backend on failure so next task can reconnect
            try:
                await self._backend.close()
            except Exception:
                pass
            self._backend = CopilotBackend()

        finally:
            async with self._lock:
                if self._status == AgentStatus.BUSY:
                    self._status = AgentStatus.RUNNING

        return task

    async def run_task_stream(self, task_prompt: str) -> AsyncGenerator[str, None]:
        """
        Submit a task and stream tokens back in real-time.
        Yields raw text tokens as they arrive from Copilot.
        """
        # Pre-flight checks
        async with self._lock:
            self._check_can_run_task()
            self._status = AgentStatus.BUSY

        task = AgentTask(
            task_id=f"task-{uuid.uuid4().hex[:12]}",
            prompt=task_prompt,
        )
        task.status = TaskStatus.RUNNING
        self._task_history.append(task)
        if len(self._task_history) > config.AGENT_MAX_HISTORY:
            self._task_history = self._task_history[-config.AGENT_MAX_HISTORY:]
        accumulated = []

        try:
            effective_prompt = (
                f"{self._system_prompt}\n\nUser task: {task_prompt}"
                if len(self._task_history) == 1
                else task_prompt
            )

            async for token in self._backend.chat_completion_stream(
                prompt=effective_prompt
            ):
                accumulated.append(token)
                yield token

            task.status       = TaskStatus.COMPLETED
            task.result       = "".join(accumulated)
            task.completed_at = datetime.now(timezone.utc)

        except Exception as exc:
            task.status       = TaskStatus.FAILED
            task.error        = str(exc)
            task.completed_at = datetime.now(timezone.utc)
            yield f"\n[Error: {exc}]"
            try:
                await self._backend.close()
            except Exception:
                pass
            self._backend = CopilotBackend()

        finally:
            async with self._lock:
                if self._status == AgentStatus.BUSY:
                    self._status = AgentStatus.RUNNING

    # ── Status & history ──────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "status":              self._status.value,
            "session_id":          self._session_id,
            "started_at":          self._started_at.isoformat() if self._started_at else None,
            "paused_at":           self._paused_at.isoformat() if self._paused_at else None,
            "tasks_total":         len(self._task_history),
            "tasks_completed":     sum(1 for t in self._task_history
                                       if t.status == TaskStatus.COMPLETED),
            "tasks_failed":        sum(1 for t in self._task_history
                                       if t.status == TaskStatus.FAILED),
            "tasks_pending_busy":  sum(1 for t in self._task_history
                                       if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)),
        }

    def get_history(self) -> list[dict]:
        return [t.to_dict() for t in self._task_history]

    def get_task(self, task_id: str) -> Optional[dict]:
        for t in self._task_history:
            if t.task_id == task_id:
                return t.to_dict()
        return None

    def clear_history(self) -> dict:
        count = len(self._task_history)
        self._task_history = []
        return {"cleared": count, "message": f"Cleared {count} task(s) from history."}

    # ── Private helpers ───────────────────────────────────────────────

    def _check_can_run_task(self):
        """Raise if the agent cannot accept a new task right now."""
        if self._status == AgentStatus.STOPPED:
            raise ValueError(
                "Agent is not started. POST /v1/agent/start first."
            )
        if self._status == AgentStatus.PAUSED:
            raise ValueError(
                "Agent is paused. POST /v1/agent/resume before submitting tasks."
            )
        if self._status == AgentStatus.BUSY:
            raise ValueError(
                "Agent is already executing a task. Wait for it to finish."
            )


# ─────────────────────────── Global singleton ─────────────────────────

_agent_manager: Optional[AgentManager] = None


def get_agent_manager() -> AgentManager:
    """Return (and lazily create) the global AgentManager singleton."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager()
    return _agent_manager
