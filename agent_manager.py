"""
============================================================
Agent Manager — manages the AI agent lifecycle and tasks.

State machine:
  STOPPED ──start()──► RUNNING
  RUNNING ──pause()──► PAUSED
  PAUSED  ──resume()─► RUNNING
  RUNNING ──task()───► BUSY ──► RUNNING  (task completes/fails)
  RUNNING / PAUSED / BUSY ──stop()──► STOPPED

Named sessions: multiple AgentManager instances keyed by session_name
(default "default"). Each wraps a persistent CopilotBackend.
============================================================
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
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
    BUSY     = "busy"


class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


# ─────────────────────────── Tool definitions ─────────────────────────

MAX_TOOL_ITERATIONS = 5
TOOL_LINE_RE = re.compile(
    r"(?m)^\s*TOOL:\s*([a-zA-Z_]\w*)\s*\(([^)]*)\)\s*$",
)

_FORBIDDEN_PY = (
    "import os", "import subprocess", "open(", "eval(", "exec(",
    "__import__", "import pathlib", "shutil", "socket",
)

AGENT_SYSTEM_PROMPT = """\
You are an intelligent AI agent powered by Microsoft Copilot.
You can answer questions, reason through problems, write and explain code,
analyse data, and assist with complex tasks.
You maintain full conversation context across multiple turns.
When solving math or logic questions, show your reasoning step by step.
Be concise, accurate, and helpful.

To call a built-in tool, output EXACTLY one line at the start of your reply:
  TOOL: tool_name("arg")
Allowed tools:
  TOOL: get_current_time()
  TOOL: get_weather("CityName")
  TOOL: list_directory("/path")
  TOOL: read_file("/path")
  TOOL: run_python_code("print(1+1)")
After a tool runs, you will receive its output and should continue with a normal answer."""


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


TOOL_REGISTRY: dict[str, object] = {
    "get_current_time": _tool_get_time,
    "get_weather":      _tool_get_weather,
    "list_directory":   _tool_list_directory,
    "run_python_code":  _tool_run_python,
    "read_file":        _tool_read_file,
}


def _parse_quoted_arg(inner: str) -> str:
    inner = inner.strip()
    if not inner:
        return ""
    if (inner.startswith('"') and inner.endswith('"')) or (
        inner.startswith("'") and inner.endswith("'")
    ):
        return inner[1:-1]
    return inner


def _sanitize_python_snippet(code: str) -> tuple[str | None, str | None]:
    c = code.strip()
    if len(c) > 2000:
        return None, "code too long"
    low = c.lower()
    for bad in _FORBIDDEN_PY:
        if bad.lower() in low:
            return None, f"forbidden pattern: {bad}"
    return c, None


async def _dispatch_tool(name: str, inner: str) -> str:
    if name not in TOOL_REGISTRY:
        return f"Unknown tool: {name}"
    if name == "get_current_time":
        fn = TOOL_REGISTRY[name]
        assert callable(fn)
        return str(fn())  # type: ignore[operator]
    if name == "get_weather":
        city = _parse_quoted_arg(inner) or inner.strip() or "London"
        fn = TOOL_REGISTRY[name]
        return await fn(city)  # type: ignore[misc]
    if name in ("list_directory", "read_file"):
        path = _parse_quoted_arg(inner) or inner.strip()
        if not path:
            return "Error: missing path"
        fn = TOOL_REGISTRY[name]
        assert callable(fn)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, path)  # type: ignore[arg-type]
    if name == "run_python_code":
        raw = _parse_quoted_arg(inner) if inner.strip() else inner
        code, err = _sanitize_python_snippet(raw)
        if err:
            return f"Error: {err}"
        if not code:
            return "Error: empty code"
        loop = asyncio.get_event_loop()
        fn = _tool_run_python
        return await loop.run_in_executor(None, fn, code)
    return "Unsupported tool"


def _find_tool_invocation(text: str) -> tuple[str, str] | None:
    m = TOOL_LINE_RE.search(text.strip())
    if not m:
        return None
    name, inner = m.group(1), m.group(2)
    if name not in TOOL_REGISTRY:
        return None
    return name, inner


# ─────────────────────────── Task record ──────────────────────────────

class AgentTask:
    """Represents a single task submitted to the agent."""

    def __init__(self, task_id: str, prompt: str):
        self.task_id:      str = task_id
        self.prompt:       str = prompt
        self.status:       TaskStatus = TaskStatus.PENDING
        self.result:       Optional[str] = None
        self.error:        Optional[str] = None
        self.tool_calls:   list[dict] = []
        self.created_at:   datetime = datetime.now(timezone.utc)
        self.completed_at: Optional[datetime] = None
        self.suggested_responses: list[str] = []

    def to_dict(self) -> dict:
        return {
            "task_id":      self.task_id,
            "prompt":       self.prompt,
            "status":       self.status.value,
            "result":       self.result,
            "error":        self.error,
            "tool_calls":   self.tool_calls,
            "created_at":   self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# ─────────────────────────── Agent Manager ────────────────────────────

class AgentManager:
    """Per-session agent lifecycle and task queue (one task at a time per manager)."""

    def __init__(self, session_key: str = "default"):
        self._session_key = session_key
        self._status:     AgentStatus       = AgentStatus.STOPPED
        self._session_id: Optional[str]     = None
        self._backend:    Optional[CopilotBackend] = None
        self._lock        = asyncio.Lock()
        self._task_history: list[AgentTask] = []
        self._started_at:  Optional[datetime] = None
        self._paused_at:   Optional[datetime] = None
        self._system_prompt: str = AGENT_SYSTEM_PROMPT

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def start(self, system_prompt: Optional[str] = None) -> dict:
        async with self._lock:
            if self._status in (AgentStatus.RUNNING, AgentStatus.BUSY):
                raise ValueError(
                    f"Agent is already {self._status.value}. "
                    "Stop it first before starting a new session."
                )
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

    async def run_task(self, task_prompt: str) -> AgentTask:
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
            effective_prompt = (
                f"{self._system_prompt}\n\nUser task: {task_prompt}"
                if len(self._task_history) == 1
                else task_prompt
            )

            assert self._backend is not None
            response = await self._backend.chat_completion(prompt=effective_prompt)
            for _ in range(MAX_TOOL_ITERATIONS):
                inv = _find_tool_invocation(response)
                if not inv:
                    break
                name, inner = inv
                out = await _dispatch_tool(name, inner)
                task.tool_calls.append({"tool": name, "args_preview": inner[:200]})
                follow = (
                    f"[Tool {name} result]\n{out}\n\n"
                    "Continue your answer for the user. Do not repeat the same TOOL line."
                )
                response = await self._backend.chat_completion(prompt=follow)
            task.status       = TaskStatus.COMPLETED
            task.result       = response
            task.completed_at = datetime.now(timezone.utc)
            task.suggested_responses = self._backend._last_suggestions

        except Exception as exc:
            task.status       = TaskStatus.FAILED
            task.error        = str(exc)
            task.completed_at = datetime.now(timezone.utc)
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
        accumulated: list[str] = []

        try:
            effective_prompt = (
                f"{self._system_prompt}\n\nUser task: {task_prompt}"
                if len(self._task_history) == 1
                else task_prompt
            )

            full_text: list[str] = []
            async for token in self._backend.chat_completion_stream(
                prompt=effective_prompt
            ):
                full_text.append(token)
                accumulated.append(token)
                yield token

            response = "".join(full_text)
            for _ in range(MAX_TOOL_ITERATIONS):
                inv = _find_tool_invocation(response)
                if not inv:
                    break
                name, inner = inv
                out = await _dispatch_tool(name, inner)
                task.tool_calls.append({"tool": name, "args_preview": inner[:200]})
                yield f"\n[tool:{name}]\n"
                follow = (
                    f"[Tool {name} result]\n{out}\n\n"
                    "Continue your answer for the user."
                )
                more: list[str] = []
                async for token in self._backend.chat_completion_stream(prompt=follow):
                    more.append(token)
                    accumulated.append(token)
                    yield token
                response = "".join(more)

            task.status       = TaskStatus.COMPLETED
            task.result       = "".join(accumulated)
            task.completed_at = datetime.now(timezone.utc)
            task.suggested_responses = self._backend._last_suggestions

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

    def _check_can_run_task(self):
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


# ───────────────── Named session registry ─────────────────────────────

_registry: dict[str, AgentManager] = {}
_registry_lock = asyncio.Lock()
_registry_last_used: dict[str, float] = {}


def _sanitize_session_name(name: str) -> str:
    n = (name or "default").strip()
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", n):
        raise ValueError(
            "Invalid session_name: use 1-64 characters [a-zA-Z0-9_-] only"
        )
    return n


async def get_agent_manager(session_name: str = "default") -> AgentManager:
    key = _sanitize_session_name(session_name)
    async with _registry_lock:
        if key not in _registry:
            _registry[key] = AgentManager(session_key=key)
        _registry_last_used[key] = time.time()
        return _registry[key]


async def list_agent_api_sessions() -> dict:
    async with _registry_lock:
        return {k: v.get_status() for k, v in _registry.items()}


async def agent_registry_reaper_loop() -> None:
    """Remove STOPPED named sessions idle longer than AGENT_API_SESSION_TTL."""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        ttl = config.AGENT_API_SESSION_TTL
        async with _registry_lock:
            to_drop: list[str] = []
            for name, mgr in list(_registry.items()):
                if mgr.status != AgentStatus.STOPPED:
                    continue
                last = _registry_last_used.get(name, 0)
                if now - last > ttl:
                    to_drop.append(name)
            for name in to_drop:
                m = _registry.pop(name, None)
                _registry_last_used.pop(name, None)
                if m and m._backend:
                    try:
                        await m._backend.close()
                    except Exception:
                        pass


def reset_agent_registry_for_tests() -> None:
    _registry.clear()
    _registry_last_used.clear()
