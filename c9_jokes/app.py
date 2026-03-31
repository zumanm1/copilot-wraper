"""
C9_JOKES — read-only validation console (FastAPI + httpx + SQLite).
Does not rewrite C1–C8 env or C3 cookies; HTTP to peers + controlled C10/C11 workspace APIs for agent pages.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import uuid

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("DATABASE_PATH", "/app/data/c9.db"))

# ── C10 Sandbox URL (single-agent /agent workspace) ──────────────────────────
C10_URL = os.environ.get("C10_URL", "http://c10-sandbox:8100").rstrip("/")

# ── C11 Sandbox URL (multi-agent /multi-Agento, session-scoped workspace) ────
C11_URL = os.environ.get("C11_URL", "http://c11-sandbox:8200").rstrip("/")

# ── Container targets ─────────────────────────────────────────────────────────
TARGETS = {
    "c1":  {"env": "C1_URL",  "default": "http://localhost:8000",  "label": "C1 copilot-api",       "health": "/health"},
    "c2":  {"env": "C2_URL",  "default": "http://localhost:8080",  "label": "C2 agent-terminal",    "health": "/health"},
    "c3":  {"env": "C3_URL",  "default": "http://localhost:8001",  "label": "C3 browser-auth",      "health": "/health"},
    "c5":  {"env": "C5_URL",  "default": "http://localhost:8080",  "label": "C5 claude-code",       "health": "/health"},
    "c6":  {"env": "C6_URL",  "default": "http://localhost:8080",  "label": "C6 kilocode",          "health": "/health"},
    "c7a": {"env": "C7A_URL", "default": "http://localhost:18789", "label": "C7a openclaw-gateway", "health": "/healthz"},
    "c7b": {"env": "C7B_URL", "default": "http://localhost:8080",  "label": "C7b openclaw-cli",     "health": "/health"},
    "c8":  {"env": "C8_URL",  "default": "http://localhost:8080",  "label": "C8 hermes-agent",      "health": "/health"},
    "c10": {"env": "C10_URL", "default": "http://c10-sandbox:8100", "label": "C10 agent sandbox",  "health": "/health"},
    "c11": {"env": "C11_URL", "default": "http://c11-sandbox:8200", "label": "C11 multi-agent sandbox", "health": "/health"},
}

# ── AI agents that can chat ───────────────────────────────────────────────────
AGENTS = [
    {"id": "c2-aider",       "label": "C2 Aider (OpenAI)"},
    {"id": "c5-claude-code", "label": "C5 Claude Code (Anthropic)"},
    {"id": "c6-kilocode",    "label": "C6 KiloCode (OpenAI)"},
    {"id": "c7-openclaw",    "label": "C7b OpenClaw"},
    {"id": "c8-hermes",      "label": "C8 Hermes Agent"},
    {"id": "c9-jokes",       "label": "C9 (generic session)"},
]


# ── Shared async HTTP client ─────────────────────────────────────────────────
_http: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(connect=5.0, read=360.0, write=10.0, pool=10.0),
        )
    return _http


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(DEFAULT_DB) as conn:
        conn.executescript(schema)


def _ensure_db() -> None:
    if not DEFAULT_DB.exists():
        _init_db()
    # Migrate: add columns introduced after initial schema
    try:
        with _db() as conn:
            conn.execute("ALTER TABLE chat_logs ADD COLUMN elapsed_ms INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        with _db() as conn:
            conn.execute("ALTER TABLE chat_logs ADD COLUMN source TEXT DEFAULT 'chat'")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: create agent_sessions and agent_messages tables if missing
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    task TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    chat_mode TEXT DEFAULT 'auto',
                    work_mode TEXT DEFAULT 'work',
                    status TEXT DEFAULT 'running',
                    steps_taken INTEGER DEFAULT 0,
                    files_created TEXT DEFAULT '[]',
                    summary TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
                )
            """)
    except sqlite3.Error:
        pass
    # Migrate: create multi_agent_sessions and multi_agent_pane_messages if missing
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS multi_agent_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT DEFAULT 'running',
                    roles TEXT DEFAULT '[]',
                    summary TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS multi_agent_pane_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    pane_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    role_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES multi_agent_sessions(id)
                )
            """)
    except sqlite3.Error:
        pass
    # Migrate: add session_id column to chat_logs
    try:
        with _db() as conn:
            conn.execute("ALTER TABLE chat_logs ADD COLUMN session_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: create chat_sessions table for persistent /chat history
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    title TEXT,
                    message_count INTEGER DEFAULT 0,
                    token_estimate INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                )
            """)
    except sqlite3.Error:
        pass
    # Migrate: create workspace_projects table if missing
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workspace_projects (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'active'
                )
            """)
    except sqlite3.Error:
        pass
    # Migrate: create ma_sessions and ma_projects tables for /multi-Agento (C11)
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ma_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    task TEXT NOT NULL,
                    roles TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'running',
                    steps_taken INTEGER DEFAULT 0,
                    files_created TEXT DEFAULT '[]',
                    summary TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ma_projects (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    display_name TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'active',
                    UNIQUE(session_id, name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ma_pane_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    pane_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    role_type TEXT NOT NULL,
                    content TEXT NOT NULL
                )
            """)
    except sqlite3.Error:
        pass
    # Migrate: create token_usage table for per-agent token tracking
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    page TEXT NOT NULL,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    model TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'ok'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tu_agent ON token_usage(agent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tu_ts    ON token_usage(ts)")
    except sqlite3.Error:
        pass


# ── URL helpers ───────────────────────────────────────────────────────────────

def _urls() -> dict[str, str]:
    return {
        key: os.environ.get(t["env"], t["default"]).rstrip("/")
        for key, t in TARGETS.items()
    }


# ── C10 Sandbox helpers ───────────────────────────────────────────────────────

async def _c10_exec(command: str, timeout: int = 30, cwd: str = ".") -> dict:
    """Execute a shell command in the C10 sandbox."""
    client = _get_http()
    try:
        r = await client.post(
            f"{C10_URL}/exec",
            json={"command": command, "timeout": timeout, "cwd": cwd},
            timeout=timeout + 10,
        )
        return r.json()
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False}


async def _c10_write_file(path: str, content: str) -> dict:
    """Write a file to the C10 workspace."""
    client = _get_http()
    try:
        r = await client.post(
            f"{C10_URL}/file/write",
            json={"path": path, "content": content},
            timeout=15,
        )
        return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c10_read_file(path: str) -> dict:
    """Read a file from the C10 workspace."""
    client = _get_http()
    try:
        r = await client.get(f"{C10_URL}/file/read", params={"path": path}, timeout=10)
        return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c10_list_files(path: str = ".") -> dict:
    """List files in the C10 workspace."""
    client = _get_http()
    try:
        r = await client.post(
            f"{C10_URL}/file/ls",
            json={"path": path, "recursive": True},
            timeout=10,
        )
        return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "entries": []}


async def _c10_reset() -> dict:
    """Reset (wipe) the C10 workspace."""
    client = _get_http()
    try:
        r = await client.post(f"{C10_URL}/workspace/reset", timeout=15)
        return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c10_delete(path: str) -> dict:
    """Delete a single file or directory from C10 workspace."""
    client = _get_http()
    try:
        r = await client.request(
            "DELETE", f"{C10_URL}/file/delete",
            json={"path": path}, timeout=15,
        )
        return r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c10_mkdir(path: str) -> dict:
    """Create a directory (mkdir -p) in the C10 workspace."""
    safe = re.sub(r'[;&|`$\\]', '', path).strip().strip("/")
    if not safe:
        return {"ok": False, "error": "invalid path"}
    result = await _c10_exec(f'mkdir -p "{safe}"', timeout=10)
    return {"ok": result.get("exit_code", 1) == 0, "path": safe,
            "error": result.get("stderr", "") if result.get("exit_code", 1) != 0 else None}


# ── C11 Sandbox helpers (multi-Agento, session-scoped) ───────────────────────

async def _c11_exec(command: str, timeout: int = 30, cwd: str = ".", session_id: str = "") -> dict:
    try:
        client = _get_http()
        r = await client.post(
            f"{C11_URL}/exec",
            json={"command": command, "timeout": timeout, "cwd": cwd, "session_id": session_id},
            timeout=timeout + 10,
        )
        return r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200], "exit_code": -1, "stdout": "", "stderr": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "exit_code": -1, "stdout": "", "stderr": str(exc)}


async def _c11_write_file(path: str, content: str, session_id: str = "") -> dict:
    try:
        client = _get_http()
        r = await client.post(f"{C11_URL}/file/write",
                              json={"path": path, "content": content, "session_id": session_id}, timeout=15)
        return r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c11_read_file(path: str, session_id: str = "") -> dict:
    try:
        client = _get_http()
        r = await client.get(f"{C11_URL}/file/read", params={"path": path, "session_id": session_id}, timeout=10)
        return r.json() if r.status_code in (200, 404) else {"ok": False, "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c11_list_files(path: str = ".", session_id: str = "") -> dict:
    try:
        client = _get_http()
        r = await client.post(f"{C11_URL}/file/ls",
                              json={"path": path, "recursive": True, "session_id": session_id}, timeout=10)
        return r.json() if r.status_code == 200 else {"ok": False, "entries": [], "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "entries": [], "error": str(exc)}


async def _c11_reset(session_id: str) -> dict:
    """Reset (wipe) a specific C11 session workspace."""
    if not session_id:
        return {"ok": False, "error": "session_id required"}
    try:
        client = _get_http()
        r = await client.post(f"{C11_URL}/session/{session_id}/reset", timeout=15)
        return r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c11_delete(path: str, session_id: str = "") -> dict:
    try:
        client = _get_http()
        r = await client.request(
            "DELETE", f"{C11_URL}/file/delete",
            json={"path": path, "session_id": session_id}, timeout=10,
        )
        return r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _c11_mkdir(path: str, session_id: str = "") -> dict:
    safe = re.sub(r'[;&|`$\\]', '', path).strip().strip("/")
    if not safe:
        return {"ok": False, "error": "invalid path"}
    result = await _c11_exec(f'mkdir -p "{safe}"', timeout=10, session_id=session_id)
    return {"ok": result.get("exit_code", 1) == 0, "path": safe,
            "error": result.get("stderr", "") if result.get("exit_code", 1) != 0 else None}


async def _c11_sessions() -> dict:
    try:
        client = _get_http()
        r = await client.get(f"{C11_URL}/sessions", timeout=10)
        return r.json() if r.status_code == 200 else {"ok": False, "sessions": [], "error": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "sessions": [], "error": str(exc)}


# ── Agentic loop — system prompt ──────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are an AI coding assistant with access to a live Linux sandbox (Python 3.11, Node.js 20, pip, npm, bash).

For each step, respond with EXACTLY ONE action using one of these formats:

To write a file:
FILE: path/to/filename.py
```
complete file content here
```

To run a shell command:
RUN: python3 filename.py

To install a package:
INSTALL: pip install flask

To read a file:
READ: filename.py

When the task is fully done and you have confirmed the output is correct:
DONE: Brief description of what was built and validated.

Important:
- Write ONE action per response (FILE or RUN or INSTALL or READ or DONE).
- Always write complete files, never partial snippets.
- After writing a file, run it to confirm it works.
- Fix any errors shown to you before declaring DONE.
- The workspace is /workspace. Use relative paths."""


# ── Agentic loop — Markdown-format parser ─────────────────────────────────────
# Copilot M365 rejects XML tool-calling syntax (safety filters strip it).
# We use a plain markdown format instead: FILE:/RUN:/INSTALL:/READ:/DONE:
# that Copilot follows naturally without triggering content filters.

def _parse_all_actions(text: str) -> list[dict]:
    """
    Parse ALL actions from a single LLM response in document order.
    Returns a list (possibly empty) of action dicts.
    The LLM often writes FILE: + RUN: in one message — capture both.
    """
    actions: list[dict] = []
    remaining = text

    while True:
        action = _parse_tool_call(remaining)
        if not action:
            break
        actions.append(action)
        # Remove the matched portion so we don't re-match it
        # Find the match position and advance past it
        tool = action["tool"]
        if tool == "write_file":
            # Remove the FILE: block
            remaining = re.sub(
                r"(?:\*\*)?FILE:(?:\*\*)?\s*`?[^`\n]+?`?\s*\n```[^\n]*\n.*?```",
                "", remaining, count=1, flags=re.DOTALL | re.IGNORECASE,
            )
            if remaining == text:  # no sub happened, remove simpler match
                remaining = re.sub(r"FILE:\s*\S+", "", remaining, count=1, flags=re.IGNORECASE)
        elif tool == "exec":
            remaining = re.sub(r"RUN:\s*`?[^\n`]+`?", "", remaining, count=1, flags=re.IGNORECASE)
        elif tool == "install":
            remaining = re.sub(r"INSTALL:\s*[^\n]+", "", remaining, count=1, flags=re.IGNORECASE)
        elif tool == "read_file":
            remaining = re.sub(r"READ:\s*\S+", "", remaining, count=1, flags=re.IGNORECASE)
        else:
            break
        if remaining == text:
            break  # safety: avoid infinite loop
    return actions


def _parse_tool_call(text: str) -> dict | None:
    """
    Parse one action from LLM response using the markdown protocol:
      FILE: path\\n```\\ncontent\\n```
      RUN: bash command
      INSTALL: pip install X  |  npm install X
      READ: path
    Returns a dict with 'tool' key, or None if no action found.
    """
    lines = text.strip().splitlines()

    def _clean_path(p: str) -> str:
        """Strip surrounding backticks, quotes, markdown bold markers, and whitespace."""
        cleaned = p.strip().strip("`").strip("'\"").strip("*").strip()
        # Remove markdown citation refs like \ue200cite\ue202...\ue201
        cleaned = re.sub(r'[\ue200-\ue2ff][^\s]*', '', cleaned).strip()
        # If result is not a valid filename (no extension or invalid chars), return empty
        if not cleaned or cleaned in ("**", "*", "file", "filename"):
            return ""
        return cleaned

    def _clean_cmd(c: str) -> str:
        """Strip surrounding backticks and whitespace from a shell command."""
        cleaned = c.strip().strip("`").strip()
        # Reject template placeholders from prompt examples
        _placeholder_cmds = (
            "command", "<command>", "shell command", "<shell command>",
            "cmd", "<cmd>", "your command here",
        )
        if cleaned.lower() in _placeholder_cmds:
            return ""
        return cleaned

    # ── FILE: path ───────────────────────────────────────────────────────────
    # Matches:  FILE: calc.py\n```[lang]\n...content...\n```
    # Also matches FILE: `calc.py` (LLM sometimes wraps in backticks)
    file_m = re.search(
        r"FILE:\s*`?([^`\n]+?)`?\s*\n```[^\n]*\n(.*?)```",
        text, re.DOTALL | re.IGNORECASE,
    )
    if file_m:
        _fp = _clean_path(file_m.group(1))
        if _fp:  # only write if path is valid
            return {"tool": "write_file", "path": _fp, "content": file_m.group(2)}

    # Also handle FILE without fenced block (bare content, may have blank lines)
    # Use .* (not .+) so empty lines inside file content are captured too
    file_m2 = re.search(r"FILE:\s*`?([^`\n]+?)`?\s*\n((?:(?!FILE:|RUN:|INSTALL:|READ:|DONE:).*\n)+)", text, re.IGNORECASE)
    if file_m2 and len(file_m2.group(2).strip()) > 10:
        _fp2 = _clean_path(file_m2.group(1))
        if _fp2:
            return {"tool": "write_file", "path": _fp2, "content": file_m2.group(2)}

    # ── RUN: command ─────────────────────────────────────────────────────────
    run_m = re.search(r"^RUN:\s*`?(.+?)`?\s*$", text, re.MULTILINE | re.IGNORECASE)
    if run_m:
        _cmd = _clean_cmd(run_m.group(1))
        if _cmd:
            return {"tool": "exec", "command": _cmd}

    # ── INSTALL: pip install X  or  npm install X ────────────────────────────
    inst_m = re.search(r"^INSTALL:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if inst_m:
        raw = inst_m.group(1).strip()
        # Filter out placeholder/invalid INSTALL values from prompt examples
        # e.g. INSTALL: <package>, INSTALL: (none), INSTALL: package
        _invalid_install = (
            raw.startswith("<") or raw.startswith("(") or
            raw in ("package", "none", "X", "pip install X", "npm install X") or
            not raw
        )
        if _invalid_install:
            pass  # skip — it's a template placeholder, not a real package
        else:
            # Normalise: "pip install flask" → package=flask, manager=pip
            #            "npm install express" → package=express, manager=npm
            pip_m  = re.match(r"pip\s+install\s+(.+)", raw, re.IGNORECASE)
            npm_m  = re.match(r"npm\s+install\s+(.+)", raw, re.IGNORECASE)
            if pip_m:
                return {"tool": "install", "package": pip_m.group(1).strip(), "manager": "pip"}
            if npm_m:
                return {"tool": "install", "package": npm_m.group(1).strip(), "manager": "npm"}
            # bare package name — default pip
            return {"tool": "install", "package": raw, "manager": "pip"}

    # ── READ: path ───────────────────────────────────────────────────────────
    read_m = re.search(r"^READ:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    if read_m:
        return {"tool": "read_file", "path": read_m.group(1).strip()}

    return None


def _parse_final_answer(text: str) -> str | None:
    """Extract DONE: summary if present."""
    m = re.search(r"^DONE:\s*(.+)", text, re.MULTILINE | re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: also accept the old XML tag in case Copilot uses it
    m2 = re.search(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL)
    return m2.group(1).strip() if m2 else None


def _strip_tool_xml(text: str) -> str:
    """Remove action markers so the visible thinking text stays clean."""
    cleaned = re.sub(r"^FILE:\s*\S+.*?```[^\n]*\n.*?```", "", text, flags=re.DOTALL | re.MULTILINE)
    cleaned = re.sub(r"^(RUN|INSTALL|READ|DONE):\s*.+$", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    cleaned = re.sub(r"<final_answer>.*?</final_answer>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


async def _execute_tool(tool: dict) -> tuple[str, dict]:
    """
    Dispatch a parsed tool dict to C10 and return (observation_text, metadata).
    observation_text is what gets fed back to the LLM as <observation>.
    """
    name = tool.get("tool", "")
    meta: dict = {"tool": name}

    if name == "exec":
        cmd = tool.get("command", "")
        meta["command"] = cmd
        result = await _c10_exec(cmd, timeout=60)
        meta["exit_code"] = result.get("exit_code", -1)
        meta["timed_out"] = result.get("timed_out", False)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        obs = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\nEXIT_CODE: {result.get('exit_code', -1)}"
        if result.get("timed_out"):
            obs += "\n[TIMED OUT]"
        return obs, meta

    elif name == "write_file":
        path = tool.get("path", "file.txt")
        content = tool.get("content", "")
        meta["path"] = path
        result = await _c10_write_file(path, content)
        meta["ok"] = result.get("ok", False)
        meta["size"] = result.get("size")
        if result.get("ok"):
            obs = f"File written: {path} ({result.get('size', 0)} bytes)"
        else:
            obs = f"Error writing file: {result.get('error', 'unknown error')}"
        return obs, meta

    elif name == "read_file":
        path = tool.get("path", "")
        meta["path"] = path
        result = await _c10_read_file(path)
        if result.get("ok"):
            obs = f"File content of {path}:\n{result.get('content', '')}"
        else:
            obs = f"Error reading file: {result.get('error', result.get('detail', 'not found'))}"
        return obs, meta

    elif name == "list_files":
        result = await _c10_list_files()
        entries = result.get("entries", [])
        if entries:
            lines = [f"  {'[DIR] ' if e['type']=='dir' else '      '}{e['path']}" for e in entries]
            obs = "Workspace files:\n" + "\n".join(lines)
        else:
            obs = "Workspace is empty."
        return obs, meta

    elif name == "install":
        pkg = tool.get("package", "")
        mgr = tool.get("manager", "pip")
        meta["package"] = pkg
        meta["manager"] = mgr
        if mgr == "npm":
            cmd = f"npm install {pkg}"
        else:
            cmd = f"pip install --quiet {pkg}"
        result = await _c10_exec(cmd, timeout=120)
        meta["exit_code"] = result.get("exit_code", -1)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        obs = f"Install {pkg} ({mgr}):\nSTDOUT: {stdout}\nSTDERR: {stderr}\nEXIT_CODE: {result.get('exit_code', -1)}"
        return obs, meta

    else:
        return f"Unknown tool: {name!r}", meta


async def _execute_tool_c11(tool: dict, session_id: str) -> tuple[str, dict]:
    """
    Dispatch a parsed tool dict to C11 (session-scoped sandbox) and return (observation_text, metadata).
    Mirrors _execute_tool() but uses C11 helpers with session_id for workspace isolation.
    """
    name = tool.get("tool", "")
    meta: dict = {"tool": name}

    if name == "exec":
        cmd = tool.get("command", "")
        meta["command"] = cmd
        result = await _c11_exec(cmd, timeout=60, session_id=session_id)
        meta["exit_code"] = result.get("exit_code", -1)
        meta["timed_out"] = result.get("timed_out", False)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        obs = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\nEXIT_CODE: {result.get('exit_code', -1)}"
        if result.get("timed_out"):
            obs += "\n[TIMED OUT]"
        return obs, meta

    elif name == "write_file":
        path = tool.get("path", "file.txt")
        content = tool.get("content", "")
        meta["path"] = path
        result = await _c11_write_file(path, content, session_id=session_id)
        meta["ok"] = result.get("ok", False)
        meta["size"] = result.get("size")
        if result.get("ok"):
            obs = f"File written: {path} ({result.get('size', 0)} bytes)"
        else:
            obs = f"Error writing file: {result.get('error', 'unknown error')}"
        return obs, meta

    elif name == "read_file":
        path = tool.get("path", "")
        meta["path"] = path
        result = await _c11_read_file(path, session_id=session_id)
        if result.get("ok"):
            obs = f"File content of {path}:\n{result.get('content', '')}"
        else:
            obs = f"Error reading file: {result.get('error', result.get('detail', 'not found'))}"
        return obs, meta

    elif name == "list_files":
        result = await _c11_list_files(session_id=session_id)
        entries = result.get("entries", [])
        if entries:
            lines = [f"  {'[DIR] ' if e['type']=='dir' else '      '}{e['path']}" for e in entries]
            obs = "Workspace files:\n" + "\n".join(lines)
        else:
            obs = "Workspace is empty."
        return obs, meta

    elif name == "install":
        pkg = tool.get("package", "")
        mgr = tool.get("manager", "pip")
        meta["package"] = pkg
        meta["manager"] = mgr
        cmd = f"npm install {pkg}" if mgr == "npm" else f"pip install --quiet {pkg}"
        result = await _c11_exec(cmd, timeout=120, session_id=session_id)
        meta["exit_code"] = result.get("exit_code", -1)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        obs = f"Install {pkg} ({mgr}):\nSTDOUT: {stdout}\nSTDERR: {stderr}\nEXIT_CODE: {result.get('exit_code', -1)}"
        return obs, meta

    else:
        return f"Unknown tool: {name!r}", meta


# ── Core probe helper (async) ────────────────────────────────────────────────

async def _probe_health(client: httpx.AsyncClient, name: str, url: str, path: str) -> dict:
    full = f"{url}{path}"
    t0 = time.monotonic()
    try:
        r = await client.get(full, timeout=5)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        try:
            parsed = r.json()
        except Exception:
            parsed = {"raw": r.text[:500]}
        return {
            "name": name,
            "url": full,
            "ok": r.status_code == 200,
            "http_status": r.status_code,
            "body": parsed,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "name": name,
            "url": full,
            "ok": False,
            "http_status": None,
            "error": str(e),
            "elapsed_ms": elapsed_ms,
        }


async def _probe_all() -> list[dict]:
    urls = _urls()
    client = _get_http()
    tasks = [
        _probe_health(client, TARGETS[key]["label"], urls[key], TARGETS[key]["health"])
        for key in TARGETS
    ]
    results = await asyncio.gather(*tasks)
    out = []
    for key, p in zip(TARGETS, results):
        p["target_key"] = key
        out.append(p)
    return out


# ── Chat proxy helper (async) ────────────────────────────────────────────────

# ── Token estimation helper ──────────────────────────────────────────────────

TOKEN_BUDGET = 30_000   # warn threshold
TOKEN_HARD_CAP = 38_000  # auto-compress threshold


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: total chars / 4."""
    return sum(len(str(m.get("content", ""))) for m in messages) // 4


async def _summarize_history(messages: list[dict], c1_url: str, agent_id: str) -> str:
    """Call C1 to summarize a list of messages. Returns summary string."""
    history_text = "\n".join(
        f"[{m['role'].upper()}]: {str(m.get('content', ''))[:600]}"
        for m in messages
    )
    summary_prompt = (
        f"Summarize this conversation history in ≤400 words. "
        f"Preserve: key decisions, file names created, commands run, current task state, "
        f"and any errors encountered. Be concise and factual.\n\n"
        f"{history_text[:6000]}"
    )
    client = _get_http()
    try:
        r = await client.post(
            f"{c1_url}/v1/chat/completions",
            headers={"Content-Type": "application/json", "X-Agent-ID": f"{agent_id}-summarize"},
            json={"model": "copilot", "messages": [{"role": "user", "content": summary_prompt}], "stream": False},
            timeout=60,
        )
        if r.status_code == 200:
            return r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        pass
    # Fallback: naive truncation summary
    lines = [f"[{m['role'].upper()}]: {str(m.get('content',''))[:200]}" for m in messages[-6:]]
    return "[Auto-summary of earlier context]:\n" + "\n".join(lines)


async def _chat_one(agent_id: str, prompt: str, c1_url: str, chat_mode: str = "", attachments: list | None = None, work_mode: str = "", messages: list | None = None) -> dict:
    """Call C1 for a single agent. Returns {ok, http_status, text, elapsed_ms}.
    If `messages` is provided it is used as the full conversation history (multi-turn).
    Otherwise falls back to single-turn prompt.
    """
    if messages:
        # Multi-turn: use provided history directly
        # Inject attachments into the last user message if present
        if attachments and messages:
            last = messages[-1]
            if last.get("role") == "user":
                content: list = [{"type": "text", "text": str(last.get("content", ""))}]
                for att in attachments:
                    if att.get("file_id"):
                        content.append({"type": "file_ref", "file_id": att["file_id"], "filename": att.get("filename", "")})
                messages = messages[:-1] + [{"role": "user", "content": content}]
        body = {"model": "copilot", "messages": messages, "stream": False}
    elif attachments:
        # Build multi-part content: text + file_ref parts
        content_list: list = [{"type": "text", "text": prompt}]
        for att in attachments:
            if att.get("file_id"):
                content_list.append({"type": "file_ref", "file_id": att["file_id"], "filename": att.get("filename", "")})
        user_msg = {"role": "user", "content": content_list}
        body = {"model": "copilot", "messages": [user_msg], "stream": False}
    else:
        user_msg = {"role": "user", "content": prompt}
        body = {"model": "copilot", "messages": [user_msg], "stream": False,}
    body_final = {
        "model": "copilot",
        "messages": body["messages"],
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "X-Agent-ID": agent_id}
    if chat_mode:
        headers["X-Chat-Mode"] = chat_mode
    if work_mode in ("work", "web"):
        headers["X-Work-Mode"] = work_mode
    client = _get_http()
    t0 = time.monotonic()
    try:
        r = await client.post(
            f"{c1_url}/v1/chat/completions",
            headers=headers,
            json=body_final,
            timeout=360,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        text = ""
        error = None
        if 200 <= r.status_code < 300:
            try:
                d = r.json()
                text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception:
                text = r.text[:2000]
        else:
            # Extract the actual error message from C1's JSON error body
            # so the C9 dashboard shows it instead of a generic "failed".
            raw = r.text[:2000]
            try:
                d = r.json()
                error = (
                    d.get("detail")
                    or d.get("error")
                    or d.get("message")
                    or raw
                )
            except Exception:
                error = raw
        return {
            "ok": 200 <= r.status_code < 300,
            "http_status": r.status_code,
            "text": text,
            "error": error,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "http_status": None, "text": "", "error": str(e), "elapsed_ms": elapsed_ms}


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_db()
    yield
    client = _http
    if client and not client.is_closed:
        await client.aclose()


app = FastAPI(title="C9 Jokes — Validation Console", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Middleware: ensure DB on every non-static request ─────────────────────────

@app.middleware("http")
async def ensure_db_middleware(request: Request, call_next):
    if not request.url.path.startswith("/static"):
        _ensure_db()
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, name="dashboard")
async def dashboard(request: Request):
    probes = await _probe_all()
    up = sum(1 for p in probes if p["ok"])
    return templates.TemplateResponse(request, "dashboard.html", {
        "probes": probes, "targets": TARGETS, "up": up, "total": len(probes),
    })


@app.get("/health", response_class=HTMLResponse, name="page_health")
async def page_health(request: Request):
    probes = await _probe_all()
    urls = _urls()
    client = _get_http()
    extra = await _probe_health(client, "C3 /status", urls["c3"], "/status")
    extra["target_key"] = "c3-status"
    probes.append(extra)
    return templates.TemplateResponse(request, "health.html", {"probes": probes})


@app.get("/pairs", response_class=HTMLResponse, name="page_pairs")
async def page_pairs(request: Request):
    return templates.TemplateResponse(request, "pairs.html", {"agents": AGENTS})


@app.get("/chat", response_class=HTMLResponse, name="page_chat")
async def page_chat(request: Request):
    urls = _urls()
    return templates.TemplateResponse(request, "chat.html", {"c1_url": urls["c1"], "agents": AGENTS})


@app.get("/logs", response_class=HTMLResponse, name="page_logs")
async def page_logs(request: Request):
    agent_filter = (request.query_params.get("agent") or "").strip()
    try:
        offset = max(0, int(request.query_params.get("offset", 0)))
    except ValueError:
        offset = 0
    limit = 20
    rows = []
    total = 0
    try:
        with _db() as conn:
            if agent_filter:
                total = conn.execute(
                    "SELECT COUNT(*) FROM chat_logs WHERE agent_id=?", (agent_filter,)
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source "
                    "FROM chat_logs WHERE agent_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (agent_filter, limit, offset),
                ).fetchall()
            else:
                total = conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0]
                rows = conn.execute(
                    "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source "
                    "FROM chat_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
    except sqlite3.Error:
        rows = []
    return templates.TemplateResponse(request, "logs.html", {
        "rows": rows,
        "agents": AGENTS,
        "agent_filter": agent_filter,
        "offset": offset,
        "limit": limit,
        "total": total,
        "prev_offset": max(0, offset - limit),
        "next_offset": offset + limit,
        "has_prev": offset > 0,
        "has_next": (offset + limit) < total,
    })


@app.get("/sessions", response_class=HTMLResponse, name="page_sessions")
async def page_sessions(request: Request):
    urls = _urls()
    c1 = urls["c1"]
    data = None
    err = None
    client = _get_http()
    try:
        r = await client.get(f"{c1}/v1/sessions", timeout=5)
        ct = r.headers.get("content-type", "")
        data = r.json() if ct.startswith("application/json") else r.text
    except Exception as e:
        err = str(e)
    return templates.TemplateResponse(request, "sessions.html", {
        "data": data, "error": err, "c1_url": c1,
    })


@app.get("/api", response_class=HTMLResponse, name="page_api_reference")
async def page_api_reference(request: Request):
    return templates.TemplateResponse(request, "api_reference.html", {
        "urls": _urls(), "targets": TARGETS, "agents": AGENTS,
    })


@app.get("/api/docs", include_in_schema=False)
async def api_docs_alias():
    """Docs and older bookmarks use `/api/docs`; the canonical page is `/api`."""
    return RedirectResponse(url="/api", status_code=307)


@app.get("/agent", response_class=HTMLResponse, name="page_agent")
async def page_agent(request: Request):
    """AI Agent Workspace — IDE-like agentic task execution via C10 sandbox."""
    return templates.TemplateResponse(request, "agent.html", {
        "agents": AGENTS,
        "c10_url": C10_URL,
    })


# ─────────────────────────────────────────────────────────────────────────────
# JSON API ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/session-health", name="api_session_health")
async def api_session_health():
    """Proxy C3's /session-health endpoint; used by the LED indicator on all pages."""
    c3_url = _urls().get("c3", "http://browser-auth:8001")
    client = _get_http()
    now = datetime.now(timezone.utc).isoformat()
    try:
        r = await client.get(f"{c3_url}/session-health", timeout=5)
        try:
            body = r.json()
        except Exception:
            body = {"session": "unknown", "profile": "unknown",
                    "reason": "C3 returned non-JSON body", "checked_at": now}
        return JSONResponse(body, status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(
            {"session": "unknown", "profile": "unknown", "reason": str(exc), "checked_at": now},
            status_code=503,
        )


@app.get("/api/status", name="api_status")
async def api_status():
    """Probe all containers in parallel and persist each result to health_snapshots."""
    urls = _urls()
    client = _get_http()
    ts = datetime.now(timezone.utc).isoformat()

    tasks = [
        _probe_health(client, TARGETS[key]["label"], urls[key], TARGETS[key]["health"])
        for key in TARGETS
    ]
    probes = await asyncio.gather(*tasks)
    result: dict = {}
    rows_to_insert = []
    for key, p in zip(TARGETS, probes):
        result[key] = p
        rows_to_insert.append((
            ts, key,
            p.get("http_status"),
            json.dumps(p.get("body") or {"error": p.get("error", "")}),
        ))

    p3s = await _probe_health(client, "C3 /status", urls["c3"], "/status")
    result["c3-status"] = p3s
    rows_to_insert.append((
        ts, "c3-status",
        p3s.get("http_status"),
        json.dumps(p3s.get("body") or {"error": p3s.get("error", "")}),
    ))
    try:
        with _db() as conn:
            conn.executemany(
                "INSERT INTO health_snapshots (captured_at, target, http_status, body_json) VALUES (?,?,?,?)",
                rows_to_insert,
            )
    except sqlite3.Error:
        pass
    result["ts"] = ts
    return JSONResponse(result)


@app.get("/api/health-history", name="api_health_history")
async def api_health_history(target: str = "", limit: int = 10):
    """Return last N health snapshots per target."""
    limit = max(1, min(50, limit))
    rows = []
    try:
        with _db() as conn:
            if target:
                rows = conn.execute(
                    "SELECT captured_at, target, http_status, body_json FROM health_snapshots "
                    "WHERE target=? ORDER BY id DESC LIMIT ?",
                    (target, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT captured_at, target, http_status, body_json FROM health_snapshots "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
    except sqlite3.Error:
        pass
    return JSONResponse([dict(r) for r in rows])


@app.post("/api/upload", name="api_upload")
async def api_upload(file: UploadFile = File(...)):
    """Proxy a file upload to C1 POST /v1/files. Returns {ok, file_id, filename, type, preview}."""
    c1 = _urls()["c1"]
    raw = await file.read()
    client = _get_http()
    try:
        r = await client.post(
            f"{c1}/v1/files",
            files={"file": (file.filename, raw, file.content_type or "application/octet-stream")},
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            return JSONResponse({"ok": True, **data})
        else:
            detail = r.text[:500]
            try:
                detail = r.json().get("detail", detail)
            except Exception:
                pass
            return JSONResponse({"ok": False, "error": str(detail)}, status_code=r.status_code)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/chat", name="api_chat")
async def api_chat(request: Request):
    """Proxy a chat turn to C1.
    Body: {agent_id, prompt, chat_mode?, work_mode?, attachments?,
           messages?: [{role,content},...],  # full history for multi-turn
           session_id?: str}                 # persistent session ID
    """
    c1 = _urls()["c1"]
    try:
        payload_in = await request.json()
    except Exception:
        payload_in = {}
    agent_id   = (payload_in.get("agent_id") or "c9-jokes").strip()
    prompt     = (payload_in.get("prompt") or "").strip()
    chat_mode  = (payload_in.get("chat_mode") or "").strip().lower()
    work_mode  = (payload_in.get("work_mode") or "").strip().lower()
    attachments = payload_in.get("attachments") or []
    messages_in = payload_in.get("messages")  # full history array (optional)
    session_id  = (payload_in.get("session_id") or "").strip()

    if not prompt and not messages_in:
        return JSONResponse({"ok": False, "error": "prompt or messages required"}, status_code=400)

    # ── Session persistence ────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    if not session_id:
        session_id = "cs_" + uuid.uuid4().hex[:8]

    # Ensure session row exists
    try:
        with _db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO chat_sessions (id, created_at, updated_at, agent_id, title) VALUES (?,?,?,?,?)",
                (session_id, now, now, agent_id, (prompt or "Chat")[:80]),
            )
    except sqlite3.Error:
        pass

    # If caller sends full messages[], use it; otherwise build from prompt alone
    if messages_in and isinstance(messages_in, list) and len(messages_in) > 0:
        messages = messages_in
    else:
        messages = None  # will use prompt-only path in _chat_one

    result = await _chat_one(
        agent_id, prompt, c1,
        chat_mode=chat_mode,
        attachments=attachments,
        work_mode=work_mode,
        messages=messages,
    )

    # ── Persist turn to chat_messages + update session metadata ───────────
    resp_text = result.get("text") or result.get("error") or ""
    try:
        with _db() as conn:
            # Count existing turns to get next turn number
            turn_row = conn.execute(
                "SELECT MAX(turn) FROM chat_messages WHERE session_id=?", (session_id,)
            ).fetchone()
            next_turn = (turn_row[0] or 0) + 1

            # Persist user message
            conn.execute(
                "INSERT INTO chat_messages (session_id, turn, role, content, created_at) VALUES (?,?,?,?,?)",
                (session_id, next_turn, "user", prompt[:4000], now),
            )
            # Persist assistant reply
            conn.execute(
                "INSERT INTO chat_messages (session_id, turn, role, content, created_at) VALUES (?,?,?,?,?)",
                (session_id, next_turn, "assistant", resp_text[:4000], now),
            )

            # Compute token estimate from full messages if available
            token_est = _estimate_tokens(messages) if messages else (len(prompt) + len(resp_text)) // 4

            conn.execute(
                "UPDATE chat_sessions SET updated_at=?, message_count=message_count+2, token_estimate=? WHERE id=?",
                (now, token_est, session_id),
            )

            # Legacy chat_logs
            conn.execute(
                "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source, session_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (now, agent_id, prompt[:200], resp_text[:500],
                 result.get("http_status"), result.get("elapsed_ms"), "chat", session_id),
            )
    except sqlite3.Error:
        pass

    result["session_id"] = session_id
    result["token_estimate"] = _estimate_tokens(messages) if messages else (len(prompt) + len(resp_text)) // 4
    return JSONResponse(result)


@app.get("/api/chat/sessions", name="api_chat_sessions")
async def api_chat_sessions(limit: int = 30):
    """Return recent chat sessions for the session picker in /chat."""
    limit = max(1, min(100, limit))
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at, agent_id, title, message_count, token_estimate "
                "FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return JSONResponse([dict(r) for r in rows])
    except sqlite3.Error as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/chat/session/{session_id}", name="api_chat_session_get")
async def api_chat_session_get(session_id: str):
    """Return all messages for a chat session (for history replay)."""
    try:
        with _db() as conn:
            sess = conn.execute(
                "SELECT id, created_at, updated_at, agent_id, title, message_count, token_estimate "
                "FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not sess:
                return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
            msgs = conn.execute(
                "SELECT turn, role, content, created_at FROM chat_messages "
                "WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return JSONResponse({
            "ok": True,
            "session": dict(sess),
            "messages": [dict(m) for m in msgs],
        })
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.delete("/api/chat/session/{session_id}", name="api_chat_session_delete")
async def api_chat_session_delete(session_id: str):
    """Delete a chat session and all its messages."""
    try:
        with _db() as conn:
            conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        return JSONResponse({"ok": True, "deleted": session_id})
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/chat/summarize", name="api_chat_summarize")
async def api_chat_summarize(request: Request):
    """Summarize a list of messages into a single summary string.
    Body: {messages: [{role, content},...], agent_id?}
    Returns: {ok, summary}
    """
    c1 = _urls()["c1"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    messages = body.get("messages") or []
    agent_id = (body.get("agent_id") or "c9-jokes").strip()
    if not messages:
        return JSONResponse({"ok": False, "error": "messages required"}, status_code=400)
    try:
        summary = await asyncio.wait_for(
            _summarize_history(messages, c1, agent_id),
            timeout=45.0
        )
    except asyncio.TimeoutError:
        lines = [f"[{m['role'].upper()}]: {str(m.get('content',''))[:200]}" for m in messages[-4:]]
        summary = "[Summarize timed out — last turns]:\n" + "\n".join(lines)
    return JSONResponse({"ok": True, "summary": summary})


# ── Token Usage tracking ──────────────────────────────────────────────────────

@app.post("/api/token-usage/record", name="api_token_usage_record")
async def api_token_usage_record(request: Request):
    """Record a token-usage event.
    Body: {agent_id, page, tokens, model?, session_id?, status?}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    agent_id   = (body.get("agent_id") or "unknown").strip()
    page       = (body.get("page")     or "unknown").strip()
    tokens     = int(body.get("tokens") or 0)
    model      = (body.get("model")      or "").strip()
    session_id = (body.get("session_id") or "").strip()
    status     = (body.get("status")     or "ok").strip()
    ts = datetime.utcnow().isoformat() + "Z"
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO token_usage (ts, agent_id, page, tokens, model, session_id, status) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, agent_id, page, tokens, model, session_id, status)
            )
        return JSONResponse({"ok": True, "recorded": tokens})
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/token-usage/summary", name="api_token_usage_summary")
async def api_token_usage_summary():
    """Return today's total and per-agent totals for the global badge."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(tokens),0) FROM token_usage WHERE ts >= ?",
                (today,)
            ).fetchone()
            today_total = row[0] if row else 0
            rows = conn.execute(
                "SELECT agent_id, COALESCE(SUM(tokens),0) FROM token_usage "
                "WHERE ts >= ? GROUP BY agent_id ORDER BY 2 DESC",
                (today,)
            ).fetchall()
            by_agent = {r[0]: r[1] for r in rows}
        return JSONResponse({"ok": True, "today_total": today_total, "by_agent": by_agent})
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e), "today_total": 0, "by_agent": {}}, status_code=500)


@app.get("/api/token-usage/history", name="api_token_usage_history")
async def api_token_usage_history(
    agent_id: str = "",
    page: str = "",
    days: int = 30,
    limit: int = 200
):
    """Return token usage rows for the dashboard table."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    clauses, params = ["ts >= ?"], [cutoff]
    if agent_id:
        clauses.append("agent_id = ?"); params.append(agent_id)
    if page:
        clauses.append("page = ?"); params.append(page)
    where = " AND ".join(clauses)
    params.append(limit)
    try:
        with _db() as conn:
            rows = conn.execute(
                f"SELECT id,ts,agent_id,page,tokens,model,session_id,status "
                f"FROM token_usage WHERE {where} ORDER BY ts DESC LIMIT ?",
                params
            ).fetchall()
            cols = ["id","ts","agent_id","page","tokens","model","session_id","status"]
            data = [dict(zip(cols, r)) for r in rows]
        return JSONResponse({"ok": True, "rows": data, "count": len(data)})
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e), "rows": []}, status_code=500)


@app.get("/api/token-usage/agents", name="api_token_usage_agents")
async def api_token_usage_agents(days: int = 30):
    """Per-agent aggregated stats with % share, status breakdown, daily trend."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT agent_id, page, "
                "  COUNT(*) as calls, "
                "  COALESCE(SUM(tokens),0) as total_tokens, "
                "  COALESCE(MAX(tokens),0) as max_tokens, "
                "  COALESCE(AVG(tokens),0) as avg_tokens, "
                "  MAX(ts) as last_used, "
                "  SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_calls, "
                "  SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) as err_calls "
                "FROM token_usage WHERE ts >= ? "
                "GROUP BY agent_id, page ORDER BY total_tokens DESC",
                (cutoff,)
            ).fetchall()
            cols = ["agent_id","page","calls","total_tokens","max_tokens","avg_tokens",
                    "last_used","ok_calls","err_calls"]
            data = [dict(zip(cols, r)) for r in rows]
            grand_total = sum(r["total_tokens"] for r in data) or 1
            for r in data:
                r["pct"] = round(r["total_tokens"] / grand_total * 100, 1)
                r["avg_tokens"] = round(r["avg_tokens"])
            # Daily totals for sparkline
            daily = conn.execute(
                "SELECT substr(ts,1,10) as day, agent_id, COALESCE(SUM(tokens),0) as t "
                "FROM token_usage WHERE ts >= ? "
                "GROUP BY day, agent_id ORDER BY day",
                (cutoff,)
            ).fetchall()
            daily_data = [{"day": r[0], "agent_id": r[1], "tokens": r[2]} for r in daily]
        return JSONResponse({"ok": True, "agents": data, "daily": daily_data, "grand_total": grand_total})
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e), "agents": [], "daily": []}, status_code=500)


@app.get("/token-counter", response_class=HTMLResponse, name="page_token_counter")
async def page_token_counter(request: Request):
    return templates.TemplateResponse(request, "token_counter.html", {})


@app.post("/api/validate", name="api_validate")
async def api_validate(request: Request):
    """Run all agents with a prompt concurrently, persist to validation_runs + pair_results."""
    c1 = _urls()["c1"]
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    prompt = (payload.get("prompt") or "Tell me a joke").strip()
    chat_mode = (payload.get("chat_mode") or "").strip().lower()
    work_mode = (payload.get("work_mode") or "").strip().lower()
    attachments = payload.get("attachments") or []
    requested_ids = payload.get("agent_ids") or [a["id"] for a in AGENTS]
    agents_to_run = [a for a in AGENTS if a["id"] in requested_ids]
    if not agents_to_run:
        return JSONResponse({"ok": False, "error": "no matching agents"}, status_code=400)

    parallel = payload.get("parallel", True)
    mode = "parallel" if parallel else "sequential"

    # Pre-warm C3 pool to 12 tabs before parallel run so all agents get pre-created tabs.
    # Non-fatal — on-demand tab creation is the fallback if this fails.
    if parallel and len(agents_to_run) > 1:
        c3 = _urls().get("c3", "http://browser-auth:8001")
        parallel_pool_size = max(1, int(os.environ.get("C3_POOL_SIZE_PARALLEL", "12")))
        try:
            _expand_r = await _get_http().post(
                f"{c3}/pool-expand",
                params={"target_size": parallel_pool_size},
                timeout=90,
            )
            _expand_data = _expand_r.json() if _expand_r.status_code == 200 else {}
            print(f"[validate] pool-expand → {_expand_data}")
        except Exception as _expand_exc:
            print(f"[validate] pool-expand non-fatal: {_expand_exc}")

    started_at = datetime.now(timezone.utc).isoformat()
    wall_t0 = time.monotonic()
    run_id = None
    try:
        with _db() as conn:
            cur = conn.execute(
                "INSERT INTO validation_runs (started_at, mode, passed, failed) VALUES (?,?,0,0)",
                (started_at, mode),
            )
            run_id = cur.lastrowid
    except sqlite3.Error:
        pass

    async def _run_one(agent: dict) -> dict:
        r = await _chat_one(agent["id"], prompt, c1, chat_mode=chat_mode, work_mode=work_mode, attachments=attachments)
        ok = r["ok"] and bool((r.get("text") or "").strip())
        detail = r.get("text") or r.get("error") or r.get("raw") or ""
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with _db() as conn:
                if run_id:
                    conn.execute(
                        "INSERT INTO pair_results (run_id, pair_name, ok, detail, duration_ms) "
                        "VALUES (?,?,?,?,?)",
                        (run_id, agent["id"], 1 if ok else 0, detail[:500], r.get("elapsed_ms")),
                    )
                # Also write to chat_logs so /logs shows all AI calls regardless of source
                conn.execute(
                    "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ts, agent["id"], prompt[:200], detail[:500], r.get("http_status"), r.get("elapsed_ms"), "validate"),
                )
        except sqlite3.Error:
            pass
        return {
            "agent_id": agent["id"],
            "label": agent["label"],
            "ok": ok,
            "http_status": r.get("http_status"),
            "text": r.get("text", ""),
            "elapsed_ms": r.get("elapsed_ms"),
            "error": r.get("error"),
        }

    agent_tasks = [_run_one(agent) for agent in agents_to_run]
    raw_results = await asyncio.gather(*agent_tasks, return_exceptions=True)

    results = []
    for agent, res in zip(agents_to_run, raw_results):
        if isinstance(res, Exception):
            results.append({
                "agent_id": agent["id"], "label": agent["label"],
                "ok": False, "http_status": None,
                "text": "", "elapsed_ms": None,
                "error": str(res),
            })
        else:
            results.append(res)

    wall_ms = int((time.monotonic() - wall_t0) * 1000)
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    finished_at = datetime.now(timezone.utc).isoformat()

    if run_id:
        try:
            with _db() as conn:
                conn.execute(
                    "UPDATE validation_runs SET finished_at=?, passed=?, failed=?, raw_summary=? WHERE id=?",
                    (finished_at, passed, failed, f"{passed}/{len(results)} passed ({mode})", run_id),
                )
        except sqlite3.Error:
            pass

    return JSONResponse({
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at,
        "finished_at": finished_at,
        "wall_ms": wall_ms,
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "prompt": prompt,
        "results": results,
    })


@app.get("/api/validation-runs", name="api_validation_runs")
async def api_validation_runs(limit: int = 10):
    """Return last N validation runs with their pair results."""
    limit = max(1, min(50, limit))
    runs = []
    try:
        with _db() as conn:
            run_rows = conn.execute(
                "SELECT id, started_at, finished_at, mode, passed, failed, raw_summary "
                "FROM validation_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for run in run_rows:
                run_dict = dict(run)
                pairs = conn.execute(
                    "SELECT pair_name, ok, detail, duration_ms FROM pair_results WHERE run_id=? ORDER BY id",
                    (run["id"],),
                ).fetchall()
                run_dict["pairs"] = [dict(p) for p in pairs]
                runs.append(run_dict)
    except sqlite3.Error:
        pass
    return JSONResponse(runs)


@app.get("/api/logs", name="api_logs")
async def api_logs(agent: str = "", limit: int = 20, offset: int = 0):
    """JSON log rows filterable by agent_id with pagination."""
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    rows = []
    total = 0
    try:
        with _db() as conn:
            if agent:
                total = conn.execute(
                    "SELECT COUNT(*) FROM chat_logs WHERE agent_id=?", (agent,)
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source "
                    "FROM chat_logs WHERE agent_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (agent, limit, offset),
                ).fetchall()
            else:
                total = conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0]
                rows = conn.execute(
                    "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source "
                    "FROM chat_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
    except sqlite3.Error:
        pass
    return JSONResponse({
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": [dict(r) for r in rows],
    })


# ─────────────────────────────────────────────────────────────────────────────
# NOTES.md helpers — shared persistent memory for agent sessions
# ─────────────────────────────────────────────────────────────────────────────

NOTES_FILE = "NOTES.md"


async def _notes_init(session_id: str, task: str) -> None:
    """Create NOTES.md in C10 workspace with initial task info."""
    content = f"# Agent Session Notes\n\n**Task:** {task}\n\n## Progress Log\n\n"
    await _c10_write_file(NOTES_FILE, content)


async def _notes_append(step: int, summary: str) -> None:
    """Append a one-line step summary to NOTES.md in C10."""
    existing = await _c10_read_file(NOTES_FILE)
    current = existing.get("content") or ""
    line = f"- [step {step}] {summary.strip()[:300]}\n"
    await _c10_write_file(NOTES_FILE, current + line)


async def _notes_read() -> str:
    """Read NOTES.md from C10. Returns empty string if missing."""
    r = await _c10_read_file(NOTES_FILE)
    return r.get("content") or ""


# ─────────────────────────────────────────────────────────────────────────────
# AGENT WORKSPACE API ROUTES  (/api/agent/*)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/agent/run", name="api_agent_run")
async def api_agent_run(
    request: Request,
    task: str = "",
    session_id: str = "",
    agent_id: str = "c9-jokes",
    chat_mode: str = "auto",
    work_mode: str = "work",
    max_steps: int = 15,
):
    """
    SSE stream of the full agentic execution loop.

    Query params:
      task       — the user task description
      agent_id   — which agent session to use with C1 (default: c9-jokes)
      chat_mode  — thinking depth: auto | quick | deep
      work_mode  — scope: work | web
      max_steps  — max ReAct iterations (default 15, max 20)

    SSE events (each line: 'event: TYPE\\ndata: JSON\\n\\n'):
      thinking    — LLM reasoning text before tool call
      tool_call   — tool being dispatched
      observation — tool execution result
      file_update — file created/modified in workspace
      step_done   — step N complete
      final       — task complete with summary
      error       — unrecoverable error
    """
    task = task.strip()
    session_id = session_id.strip()
    max_steps = max(1, min(20, max_steps))
    c1 = _urls()["c1"]

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def generate():
        nonlocal task, session_id

        # Check C10 is reachable
        client = _get_http()
        try:
            health_r = await client.get(f"{C10_URL}/health", timeout=5)
            if health_r.status_code != 200:
                yield _sse("error", {"message": f"C10 sandbox unhealthy (HTTP {health_r.status_code}). Is c10-sandbox running?"})
                return
        except Exception as exc:
            yield _sse("error", {"message": f"C10 sandbox unreachable: {exc}. Run: docker compose up c10-sandbox -d"})
            return

        # ── Auth pre-flight: verify M365 session BEFORE sending any task ─────
        # Checks C3's /session-health, then sends a short "hi" ping to confirm
        # Copilot is actually reachable and responding (not just authenticated).
        c3_url = _urls().get("c3", "http://browser-auth:8001")
        _session_status = "unknown"
        try:
            _auth_r = await client.get(f"{c3_url}/session-health", timeout=8)
            _auth_data = _auth_r.json() if _auth_r.status_code == 200 else {}
            _session_status = _auth_data.get("session", "unknown")
        except Exception as _auth_exc:
            _auth_data = {"reason": str(_auth_exc)}

        if _session_status != "active":
            _reason = _auth_data.get("reason", "Session not active")
            yield _sse("auth_required", {
                "message": (
                    f"M365 Copilot is not authenticated (status: {_session_status}). "
                    f"Please sign in via the browser at localhost:6080"
                ),
                "session_status": _session_status,
                "reason": _reason,
                "auth_url": "http://localhost:6080/?resize=scale&autoconnect=true",
            })
            return

        # Session is active — send a quick "hi" to verify Copilot is actually responding
        yield _sse("thinking", {"step": 0, "text": "🔐 Auth OK — pinging Copilot...", "total_steps": max_steps})
        _HI_SERVICE_PHRASES = (
            "something went wrong", "please try again later",
            "experiencing high demand", "we're experiencing",
        )
        try:
            _hi_r = await client.post(
                f"{c1}/v1/chat/completions",
                headers={"Content-Type": "application/json", "X-Agent-ID": f"{agent_id}-preflight"},
                json={"model": "copilot", "messages": [{"role": "user", "content": "hi"}], "stream": False},
                timeout=30,
            )
            if _hi_r.status_code == 200:
                _hi_text = (_hi_r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
                if any(p in _hi_text.lower() for p in _HI_SERVICE_PHRASES):
                    # Copilot responded but with a service error — warn and continue
                    yield _sse("thinking", {"step": 0, "text":
                        f"⚠️ Copilot is under load: \"{_hi_text[:80]}\" — task will retry automatically if needed"})
                elif _hi_text:
                    yield _sse("thinking", {"step": 0, "text": f"✅ Copilot OK — starting task..."})
                else:
                    yield _sse("thinking", {"step": 0, "text": "⚠️ Copilot ping returned empty — proceeding anyway"})
            else:
                yield _sse("thinking", {"step": 0, "text": f"⚠️ Copilot ping HTTP {_hi_r.status_code} — proceeding anyway"})
        except Exception as _hi_exc:
            yield _sse("thinking", {"step": 0, "text": f"⚠️ Copilot ping failed ({str(_hi_exc)[:60]}) — may be slow"})

        # ── Session management ───────────────────────────────────────────────
        now = datetime.now(timezone.utc).isoformat()
        history: list[dict] = []
        files_created: list[str] = []
        commands_run:  list[str] = []
        is_followup = False

        if session_id:
            # Resume existing session — load conversation history from DB
            try:
                with _db() as conn:
                    sess = conn.execute(
                        "SELECT task, agent_id, files_created FROM agent_sessions WHERE id=?",
                        (session_id,)
                    ).fetchone()
                    if sess:
                        is_followup = True
                        files_created = json.loads(sess["files_created"] or "[]")
                        msgs = conn.execute(
                            "SELECT role, content FROM agent_messages WHERE session_id=? ORDER BY turn, id",
                            (session_id,)
                        ).fetchall()
                        history = [{"role": r["role"], "content": r["content"]} for r in msgs]
                        if not task:
                            task = sess["task"]
            except sqlite3.Error:
                pass

        if not task:
            yield _sse("error", {"message": "No task provided."})
            return

        if not session_id:
            session_id = "sess_" + uuid.uuid4().hex[:8]
            # Create new session row
            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO agent_sessions (id, created_at, updated_at, task, agent_id, chat_mode, work_mode, status) "
                        "VALUES (?,?,?,?,?,?,?,'running')",
                        (session_id, now, now, task[:1000], agent_id, chat_mode, work_mode),
                    )
            except sqlite3.Error:
                pass

        yield _sse("session", {"session_id": session_id, "is_followup": is_followup})

        # ── NOTES.md: initialise or load existing notes ──────────────────────
        if not is_followup:
            await _notes_init(session_id, task)
            yield _sse("notes_updated", {"action": "created", "path": NOTES_FILE})
        else:
            # For follow-ups, prepend existing NOTES.md content so LLM remembers
            _prior_notes = await _notes_read()
            if _prior_notes:
                yield _sse("notes_updated", {"action": "loaded", "path": NOTES_FILE,
                                              "preview": _prior_notes[:300]})

        # Copilot (C1) does not honour the OpenAI `system` role — it strips it.
        # Fix: fold task first, then format guide — task-first keeps Copilot
        # focused on executing rather than acknowledging protocol rules.
        # Extract filename hint from task for the opening example
        _fn_hint = "script.py"
        _fn_m = re.search(r'\b(\w+\.(?:py|js|sh|ts|rb|go))\b', task)
        if _fn_m:
            _fn_hint = _fn_m.group(1)
        initial_user_msg = (
            f"TASK: {task}\n\n"
            f"Sandbox: Python 3.11, Node.js 20, bash.\n"
            f"Reply with ONE action per message in order: write file first, then run it.\n\n"
            f"Step 1 — write the file:\n"
            f"FILE: {_fn_hint}\n"
            f"[complete file content on the following lines]\n\n"
            f"Step 2 — run it after writing:\n"
            f"RUN: python3 {_fn_hint}\n\n"
            f"Step 3 — install packages if needed:\n"
            f"INSTALL: flask\n\n"
            f"Step 4 — confirm done:\n"
            f"DONE: description of what ran and output\n\n"
            f"For web servers: RUN: nohup python3 app.py > server.log 2>&1 &\n"
            f"Then verify: RUN: sleep 2 && curl -sf http://localhost:5001/ && echo OK\n"
            f"Include port in DONE.\n\n"
            f"Begin with Step 1 now: FILE: {_fn_hint}"
        )
        if is_followup and history:
            # For follow-ups, append the new instruction as a user turn
            # Seed with NOTES.md content so LLM has persistent memory
            _prior_notes = await _notes_read()
            _notes_context = f"\n\n[Session Notes from NOTES.md]:\n{_prior_notes[:800]}" if _prior_notes else ""
            followup_msg = (
                f"FOLLOW-UP TASK: {task}\n\n"
                f"Continue from where you left off. The workspace files still exist. "
                f"Use FILE:/RUN:/INSTALL: actions as before. "
                f"When done, write DONE: summary."
                f"{_notes_context}"
            )
            history.append({"role": "user", "content": followup_msg})

        yield _sse("thinking", {"step": 0, "text": f"🚀 Starting agent task: {task[:120]}...", "total_steps": max_steps})
        turn_counter = len(history)  # track DB turn numbers
        service_error_retries = 0    # consecutive "Something went wrong" retries

        for step in range(1, max_steps + 1):
            yield _sse("step_done", {"step": step, "max_steps": max_steps, "status": "running"})

            # Build messages for C1 — no system role (Copilot strips it).
            # Step 1: send initial_user_msg which has system prompt + task baked in.
            # Subsequent steps: replay conversation history (max 6 turns to avoid
            # bloated context with [Assistant]: prefixes confusing Copilot).
            if not history:
                messages: list[dict] = [{"role": "user", "content": initial_user_msg}]
            else:
                # Keep initial user message + last 5 turns to limit context size
                _hist = list(history)
                if len(_hist) > 6:
                    _hist = [_hist[0]] + _hist[-5:]  # always keep initial prompt
                messages = _hist

            # ── Token budget check ───────────────────────────────────────────
            _token_est = _estimate_tokens(messages)
            yield _sse("token_estimate", {"step": step, "tokens": _token_est,
                                          "budget": TOKEN_BUDGET, "hard_cap": TOKEN_HARD_CAP})
            if _token_est >= TOKEN_HARD_CAP:
                # Auto-compress: summarize all but the first message
                _to_compress = messages[1:] if len(messages) > 1 else messages
                yield _sse("context_compressed", {
                    "step": step, "tokens_before": _token_est,
                    "message": "Context near limit — auto-compressing history..."
                })
                _summary_text = await _summarize_history(_to_compress, c1, agent_id)
                messages = [
                    messages[0],  # keep initial task message
                    {"role": "user", "content": f"[Context summary — earlier steps compressed]:\n{_summary_text}"}
                ]
                # Rebuild history to match compressed messages
                history = messages[:]
                _new_est = _estimate_tokens(messages)
                yield _sse("context_compressed", {
                    "step": step, "tokens_after": _new_est,
                    "message": f"History compressed: ~{_token_est}→~{_new_est} tokens. Continuing..."
                })
            elif _token_est >= TOKEN_BUDGET:
                yield _sse("token_warning", {
                    "step": step, "tokens": _token_est, "budget": TOKEN_BUDGET,
                    "message": f"Context nearing limit (~{_token_est:,} tokens). Will auto-compress at {TOKEN_HARD_CAP:,}."
                })

            # Call C1 (Copilot LLM)
            headers = {
                "Content-Type": "application/json",
                "X-Agent-ID": agent_id,
            }
            if chat_mode:
                headers["X-Chat-Mode"] = chat_mode
            if work_mode in ("work", "web"):
                headers["X-Work-Mode"] = work_mode

            body = {
                "model": "copilot",
                "messages": messages,
                "stream": False,
            }

            try:
                llm_r = await client.post(
                    f"{c1}/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=180,
                )
                if llm_r.status_code != 200:
                    raw = llm_r.text[:400]
                    yield _sse("error", {"message": f"C1 returned HTTP {llm_r.status_code}: {raw}"})
                    return
                llm_data = llm_r.json()
                response_text: str = llm_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as exc:
                err_detail = str(exc) or type(exc).__name__
                # Transient errors (ReadTimeout, ConnectError, etc.) — retry up to 3 times
                service_error_retries += 1
                if service_error_retries <= 3:
                    wait_s = service_error_retries * 15  # 15s, 30s, 45s back-off
                    yield _sse("thinking", {"step": step, "text":
                        f"⚠️ M365 Copilot unreachable ({err_detail[:80]}) — "
                        f"retrying in {wait_s}s (attempt {service_error_retries}/3)..."})
                    await asyncio.sleep(wait_s)
                    continue
                yield _sse("error", {"message":
                    f"M365 Copilot is not reachable after 3 retries. "
                    f"Check the browser session at :6080 or verify internet/auth. "
                    f"Last error: {err_detail[:200]}"})
                return

            # ── Content-filter detection ────────────────────────────────────────────
            # Copilot M365 sometimes refuses with "Sorry, I can't chat about this."
            # This response must NOT be added to history — it corrupts context because
            # server.py flattens history as "[Assistant]: Sorry..." in the next prompt,
            # which triggers further refusals. Instead, restart with a fresh prompt.
            _REFUSAL_PHRASES = (
                "can't chat about this", "can't respond to this",
                "let's try a different topic", "i can't discuss",
                "generating response",  # stuck loading page
                "copilot\ncopilot",     # DOM sender label only, no real content
            )
            if any(p in response_text.lower() for p in _REFUSAL_PHRASES):
                # Wait before retrying — let C3's page pool fully reset
                await asyncio.sleep(4)
                # Reset history to just the initial prompt (drops the bad context)
                history = [{"role": "user", "content": initial_user_msg}]
                yield _sse("thinking", {"step": step, "text":
                    "⚠️ Copilot content filter — retrying with fresh context..."})
                continue

            # ── M365 service-error detection ────────────────────────────────────────
            # Copilot M365 browser UI shows "Something went wrong. Please try again
            # later." when the service is overloaded or has a transient fault.
            # C3 extracts this as the response text, which is NOT a real answer.
            # We must NOT add it to history. Instead: wait, then retry same step.
            # Three states handled:
            #   1) Valid response         → normal flow below
            #   2) "Something went wrong" → wait + retry (up to 3 times)
            #   3) Empty response         → auth down / no internet → abort
            _SERVICE_ERROR_PHRASES = (
                "something went wrong",
                "please try again later",
                "please retry",
                "try again later",
                "experiencing high demand",
                "we're experiencing",
                "high demand",
            )
            if not response_text.strip():
                # Empty response = auth session expired or Copilot unreachable
                yield _sse("error", {"message":
                    "M365 Copilot returned an empty response. "
                    "Auth may be expired or internet is down. "
                    "Check the browser session at :6080."})
                return

            if any(p in response_text.lower() for p in _SERVICE_ERROR_PHRASES):
                service_error_retries += 1
                if service_error_retries > 3:
                    yield _sse("error", {"message":
                        "M365 Copilot is unavailable (service error) after 3 retries. "
                        "Check the browser session at :6080 or wait and try again."})
                    return
                wait_s = service_error_retries * 15  # 15s, 30s, 45s
                yield _sse("thinking", {"step": step, "text":
                    f"⚠️ M365 Copilot service error — waiting {wait_s}s then retrying "
                    f"(attempt {service_error_retries}/3)..."})
                await asyncio.sleep(wait_s)
                # Do NOT advance history — retry the exact same step
                continue

            # Good response received — reset service error counter
            service_error_retries = 0

            # ── Inter-step delay ────────────────────────────────────────────────────
            # Give Copilot's browser page 6 seconds to finish rendering before the
            # next API call types a new message into the still-active chat box.
            # Copilot "Coding and executing" responses need extra time to complete.
            await asyncio.sleep(6)

            # Emit thinking text (stripped of XML)
            thinking_text = _strip_tool_xml(response_text)
            if thinking_text:
                yield _sse("thinking", {"step": step, "text": thinking_text})

            # Check for final answer — require both a file write AND an exec
            # to have occurred. This prevents the LLM from hallucinating execution
            # and declaring DONE without actually running anything in C10.
            final_answer = _parse_final_answer(response_text)
            if final_answer and files_created and commands_run:
                # Detect port in DONE summary for web preview
                port_m2 = re.search(r'port[= :]?\s*(\d{4,5})', final_answer, re.IGNORECASE)
                web_port = int(port_m2.group(1)) if port_m2 else None
                # Append DONE summary to NOTES.md for cross-session memory
                await _notes_append(step, final_answer[:200])
                yield _sse("notes_updated", {
                    "action": "appended", "path": NOTES_FILE, "step": step,
                    "preview": final_answer[:120]
                })
                # Save session as completed
                try:
                    with _db() as conn:
                        conn.execute(
                            "UPDATE agent_sessions SET status='completed', updated_at=?, "
                            "steps_taken=?, files_created=?, summary=? WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(), step,
                             json.dumps(files_created), final_answer[:500], session_id),
                        )
                except sqlite3.Error:
                    pass
                ev = {
                    "summary": final_answer,
                    "steps_taken": step,
                    "files_created": files_created,
                    "session_id": session_id,
                }
                if web_port:
                    ev["web_port"] = web_port
                yield _sse("final", ev)
                return
            elif final_answer:
                # DONE claimed too early (no exec yet) — push back with explicit nudge
                final_answer = None

            # ── Detect Copilot's built-in code-executor responses ─────────────────
            # Copilot M365 may execute code itself and return results in two formats:
            # 1) JSON: {"executedCode":"...","status":"...","stdout":"...","outputFiles":[...]}
            # 2) Markdown with "Coding and executing" banner or "**RUN: cmd**" blocks
            # For JSON: download any outputFiles and write to C10, then continue.
            # For Markdown: only redirect if NO standard FILE:/RUN: keywords present.
            stripped_resp = response_text.strip()
            _is_copilot_exec = False
            _exec_data: dict = {}
            if stripped_resp.startswith('{') and '"executedCode"' in stripped_resp:
                try:
                    _exec_data = json.loads(stripped_resp)
                    _is_copilot_exec = bool(_exec_data.get("executedCode"))
                except json.JSONDecodeError:
                    pass
            elif ("Coding and executing" in response_text
                  or ("**RUN:" in response_text and "Commands executed" in response_text)):
                has_protocol = bool(re.search(r"^(FILE|RUN|INSTALL|DONE):", response_text, re.MULTILINE))
                if not has_protocol:
                    _is_copilot_exec = True
            if _is_copilot_exec:
                # Try to download outputFiles from Copilot's AMS storage to C10
                _downloaded: list[str] = []
                if _exec_data.get("outputFiles"):
                    for of in _exec_data["outputFiles"]:
                        furl = of.get("codeResultFileUrl", "")
                        fname = of.get("fileName", "")
                        if furl and fname and not fname.startswith("."):
                            try:
                                dl = await client.get(furl, timeout=15,
                                    headers={"User-Agent": "Mozilla/5.0"})
                                if dl.status_code == 200:
                                    wr = await _c10_write_file(fname, dl.text)
                                    if wr.get("ok"):
                                        _downloaded.append(fname)
                                        if fname not in files_created:
                                            files_created.append(fname)
                                        yield _sse("file_update",
                                            {"path": fname, "action": "created", "source": "copilot-exec"})
                            except Exception:
                                pass
                exec_stdout = _exec_data.get("stdout", "")
                exec_status = _exec_data.get("status", "")
                if _downloaded:
                    # Files downloaded — treat as a successful file write + run
                    commands_run.append("(copilot-exec)")
                    _dl_list = ", ".join(_downloaded)
                    yield _sse("thinking", {"step": step, "text":
                        f"📥 Downloaded from Copilot executor: {_dl_list}\nstdout: {exec_stdout[:300]}"})
                    next_obs = f"Files written: {_dl_list}. Exec output: {exec_stdout[:400]}"
                    if not history:
                        history.append({"role": "user", "content": initial_user_msg})
                    history.append({"role": "assistant", "content": response_text})
                    history.append({"role": "user", "content":
                        f"Files saved. Output: {exec_stdout[:300]}\n"
                        f"Now RUN: python3 {_downloaded[0]} to verify, or DONE: summary."})
                    continue
                else:
                    # No files to download — redirect to FILE: protocol
                    yield _sse("thinking", {"step": step, "text":
                        "⚠️ Copilot ran code in its own environment. Requesting FILE: action..."})
                    if not history:
                        history.append({"role": "user", "content": initial_user_msg})
                    history.append({"role": "assistant", "content": response_text})
                    history.append({"role": "user", "content":
                        "Write the file using FILE: filename then the content below. "
                        "Then RUN: command to execute it."})
                    continue

            # Parse ALL actions from this response (LLM often writes FILE: + RUN: together)
            tools = _parse_all_actions(response_text)
            if not tools:
                # No action found — nudge LLM to produce one
                if not history:
                    history.append({"role": "user", "content": initial_user_msg})
                history.append({"role": "assistant", "content": response_text})
                history.append({
                    "role": "user",
                    "content": (
                        "Write your next action: FILE: filename (then content), "
                        "or RUN: command, or INSTALL: package, or DONE: summary."
                    ),
                })
                continue

            # Execute all actions from this response sequentially
            observations: list[str] = []
            last_tool_name = ""
            last_meta: dict = {}

            if not history:
                history.append({"role": "user", "content": initial_user_msg})
            history.append({"role": "assistant", "content": response_text})
            # Persist assistant turn to DB
            turn_counter += 1
            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO agent_messages (session_id, turn, role, content) VALUES (?,?,?,?)",
                        (session_id, turn_counter, "assistant", response_text[:4000]),
                    )
                    conn.execute(
                        "UPDATE agent_sessions SET updated_at=?, steps_taken=? WHERE id=?",
                        (datetime.now(timezone.utc).isoformat(), step, session_id),
                    )
            except sqlite3.Error:
                pass

            for tool in tools:
                # Emit tool_call SSE
                tool_event: dict = {"step": step, "tool": tool["tool"]}
                if tool.get("command"):   tool_event["command"] = tool["command"]
                if tool.get("path"):      tool_event["path"]    = tool["path"]
                if tool.get("package"):   tool_event["package"] = tool["package"]
                if tool.get("content"):   tool_event["preview"] = tool["content"][:200]
                yield _sse("tool_call", tool_event)

                # Execute in C10
                observation, meta = await _execute_tool(tool)
                last_tool_name = tool["tool"]
                last_meta = meta

                # Track commands & files
                if tool["tool"] == "exec":
                    cmd = tool.get("command", "")
                    if cmd and cmd not in commands_run:
                        commands_run.append(cmd)
                    # Detect background web server launch — emit web_server event
                    # Also check background=True from C10 (returned for nohup cmds)
                    is_bg = meta.get("background", False) or bool(
                        cmd and ("nohup " in cmd or cmd.strip().endswith("&"))
                    )
                    if is_bg:
                        # Scan command + response text + all written file contents for port
                        search_corpus = cmd + " " + response_text
                        for fc in files_created:
                            fr = await _c10_read_file(fc)
                            search_corpus += " " + (fr.get("content") or "")
                        port_m = re.search(
                            r'port\s*[=:,( ]\s*(\d{4,5})|\.run\s*\([^)]*port\s*=\s*(\d{4,5})',
                            search_corpus, re.IGNORECASE
                        )
                        detected_port = None
                        if port_m:
                            detected_port = int(port_m.group(1) or port_m.group(2))
                        if detected_port:
                            yield _sse("web_server", {"port": detected_port})

                if tool["tool"] == "write_file" and meta.get("ok"):
                    path = meta.get("path", "")
                    file_content = tool.get("content", "")
                    if path and path not in files_created:
                        files_created.append(path)
                    yield _sse("file_update", {"path": path, "action": "created"})

                    # Auto-run the file immediately after writing if no RUN: follows
                    # BUT: suppress auto-run for web server files (Flask/Express/Fastapi)
                    # to avoid blocking the event loop with a long-running process.
                    _web_server_patterns = (
                        "flask", "fastapi", "uvicorn", "express()", "http.createserver",
                        "app.listen(", "app.run(", "socketio", "tornado", "django",
                    )
                    is_web_server_file = any(p in file_content.lower() for p in _web_server_patterns)

                    remaining_tools = tools[tools.index(tool)+1:]
                    has_exec_following = any(t["tool"] == "exec" for t in remaining_tools)
                    if not has_exec_following and path and not is_web_server_file:
                        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                        auto_cmd = {"py": f"python3 {path}", "js": f"node {path}",
                                    "sh": f"bash {path}"}.get(ext)
                        if auto_cmd:
                            yield _sse("tool_call", {"step": step, "tool": "exec",
                                                     "command": auto_cmd, "auto": True})
                            run_result, run_meta = await _execute_tool(
                                {"tool": "exec", "command": auto_cmd}
                            )
                            if auto_cmd not in commands_run:
                                commands_run.append(auto_cmd)
                            obs_event2 = {"step": step, "tool": "exec",
                                          "result": run_result[:800], "auto": True}
                            if "exit_code" in run_meta:
                                obs_event2["exit_code"] = run_meta["exit_code"]
                            yield _sse("observation", obs_event2)
                            observations.append(f"[auto-run {auto_cmd}]\n{run_result}")
                            last_tool_name = "exec"
                            last_meta = run_meta

                # Emit observation SSE
                obs_event: dict = {"step": step, "tool": tool["tool"],
                                   "result": observation[:800]}
                # Background processes return exit_code=0 always; don't show as error
                if meta.get("background"):
                    obs_event["exit_code"] = 0
                    obs_event["background"] = True
                elif "exit_code" in meta:
                    obs_event["exit_code"] = meta["exit_code"]
                if meta.get("timed_out"): obs_event["timed_out"] = True
                yield _sse("observation", obs_event)
                observations.append(observation)

            # Build combined feedback — sanitize paths to avoid content filter triggers
            def _sanitize_obs(text: str) -> str:
                """Remove absolute paths like /workspace/ from shell output."""
                return re.sub(r'/workspace/', '', text)
            combined_obs = _sanitize_obs("\n---\n".join(observations))
            if last_tool_name == "exec":
                ec = last_meta.get("exit_code", 0)
                is_bg = last_meta.get("background", False)
                if is_bg or ec == 0:
                    next_hint = (
                        f"Output: {combined_obs[:600]}\n\n"
                        + (
                            f"Server started. Verify: RUN: sleep 2 && curl -sf http://localhost:{detected_port or 5001}/ && echo OK\n"
                            if is_bg else
                            f"Output looks correct. Write DONE: summary, or fix issues."
                        )
                    )
                else:
                    _err_path = tools[-1].get('path', _fn_hint) if tools else _fn_hint
                    next_hint = (
                        f"Error: {combined_obs[:500]}\n\n"
                        f"Fix it: FILE: {_err_path}\n[corrected file content]"
                    )
            elif last_tool_name == "install":
                next_hint = f"Installed. Now FILE: {_fn_hint}\n[file content]"
            else:
                next_hint = (
                    f"Result: {combined_obs[:500]}\n\n"
                    f"Next action: FILE: filename, RUN: command, INSTALL: package, or DONE: summary."
                )
            history.append({"role": "user", "content": next_hint})

        # Reached max_steps without final_answer
        try:
            with _db() as conn:
                conn.execute(
                    "UPDATE agent_sessions SET status='failed', updated_at=?, steps_taken=?, files_created=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), max_steps, json.dumps(files_created), session_id),
                )
        except sqlite3.Error:
            pass
        yield _sse("final", {
            "summary": f"Reached maximum steps ({max_steps}). Task may be partially complete. Check the file tree for created files.",
            "steps_taken": max_steps,
            "files_created": files_created,
            "session_id": session_id,
            "max_steps_reached": True,
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/agent/reset", name="api_agent_reset")
async def api_agent_reset():
    """Reset (wipe) the C10 workspace. Also clears workspace_projects DB rows."""
    result = await _c10_reset()
    # Keep projects DB in sync with filesystem
    try:
        with _db() as conn:
            conn.execute("DELETE FROM workspace_projects")
    except sqlite3.Error:
        pass
    return JSONResponse(result)


@app.get("/api/agent/files", name="api_agent_files")
async def api_agent_files():
    """List all files currently in the C10 workspace."""
    result = await _c10_list_files()
    return JSONResponse(result)


@app.get("/api/agent/file", name="api_agent_file")
async def api_agent_file(path: str = ""):
    """Read a single file from the C10 workspace. Query param: path="""
    if not path:
        return JSONResponse({"ok": False, "error": "path required"}, status_code=400)
    result = await _c10_read_file(path)
    return JSONResponse(result)


@app.delete("/api/agent/file", name="api_agent_file_delete")
async def api_agent_file_delete(path: str = ""):
    """Delete a single file or directory from the C10 workspace. Query param: path="""
    if not path:
        return JSONResponse({"ok": False, "error": "path required"}, status_code=400)
    result = await _c10_delete(path)
    return JSONResponse(result)


@app.post("/api/agent/mkdir", name="api_agent_mkdir")
async def api_agent_mkdir(body: dict):
    """Create a directory in the C10 workspace. Body: {path: str}"""
    path = (body.get("path") or "").strip().strip("/")
    if not path:
        return JSONResponse({"ok": False, "error": "path required"}, status_code=400)
    result = await _c10_mkdir(path)
    return JSONResponse(result)


@app.get("/api/agent/projects", name="api_agent_projects")
async def api_agent_projects():
    """Return all workspace projects from DB."""
    rows = []
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, name, display_name, description, status "
                "FROM workspace_projects ORDER BY created_at DESC"
            ).fetchall()
    except sqlite3.Error:
        pass
    return JSONResponse([dict(r) for r in rows])


@app.post("/api/agent/project", name="api_agent_project_create")
async def api_agent_project_create(body: dict):
    """Create a named project (subdirectory) in the C10 workspace.
    Body: {name: str, display_name?: str, description?: str}"""
    raw_name = (body.get("name") or "").strip()
    display   = (body.get("display_name") or raw_name).strip()
    desc      = (body.get("description") or "").strip()
    if not raw_name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    # Slugify: lowercase, spaces→hyphens, strip non-alphanumeric/hyphens/underscores
    slug = re.sub(r"[^a-z0-9_\-]", "", raw_name.lower().replace(" ", "-"))
    if not slug:
        return JSONResponse({"ok": False, "error": "invalid name — use letters, numbers, hyphens"}, status_code=400)
    mkdir_r = await _c10_mkdir(slug)
    if not mkdir_r.get("ok"):
        return JSONResponse({"ok": False, "error": "mkdir failed: " + (mkdir_r.get("error") or "unknown")}, status_code=500)
    proj_id = "proj_" + uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO workspace_projects (id, created_at, name, display_name, description) VALUES (?,?,?,?,?)",
                (proj_id, now, slug, display or slug, desc),
            )
    except sqlite3.IntegrityError:
        return JSONResponse({"ok": False, "error": f"Project '{slug}' already exists"}, status_code=409)
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "id": proj_id, "name": slug, "display_name": display or slug})


@app.delete("/api/agent/project", name="api_agent_project_delete")
async def api_agent_project_delete(name: str = ""):
    """Delete a project directory and its DB record. Query param: name="""
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    c10_r = await _c10_delete(name)
    try:
        with _db() as conn:
            conn.execute("DELETE FROM workspace_projects WHERE name=?", (name,))
    except sqlite3.Error:
        pass
    return JSONResponse({"ok": c10_r.get("ok", False), "name": name})


@app.get("/api/agent/sessions", name="api_agent_sessions")
async def api_agent_sessions(limit: int = 20):
    """Return last N agent sessions for the history sidebar."""
    limit = max(1, min(50, limit))
    rows = []
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at, task, agent_id, status, steps_taken, files_created, summary "
                "FROM agent_sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.Error:
        pass
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/agent/preview", name="api_agent_preview")
async def api_agent_preview(port: int = 3000, path: str = "/"):
    """Proxy HTTP requests into C10's running web server on the given port.
    The sandbox and C9 share copilot-net, so http://c10-sandbox:PORT is reachable."""
    c10_host = C10_URL.split("://")[-1].split(":")[0]  # e.g. "c10-sandbox"
    target = f"http://{c10_host}:{port}{path}"
    client = _get_http()
    try:
        r = await client.get(target, timeout=5)
        content_type = r.headers.get("content-type", "text/html")
        return Response(
            content=r.content,
            media_type=content_type,
            status_code=r.status_code,
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;background:#0f1419;color:#e6edf3;padding:2rem'>"
            f"<h3>🔌 Preview not available</h3>"
            f"<p>Could not reach <code>http://c10-sandbox:{port}/</code></p>"
            f"<p style='color:#8b949e'>{exc}</p>"
            f"<p>The web server may still be starting. Wait a moment and refresh.</p>"
            f"</body></html>",
            status_code=502,
        )


@app.post("/api/agent/upload-to-workspace", name="api_agent_upload_workspace")
async def api_agent_upload_workspace(file: UploadFile = File(...)):
    """Write an uploaded file directly into the C10 workspace.
    Useful for giving the agent reference files, images, or existing code."""
    raw = await file.read()
    filename = file.filename or "uploaded_file"
    # Try to decode as text; fall back to writing raw bytes
    try:
        content = raw.decode("utf-8")
        result = await _c10_write_file(filename, content)
    except UnicodeDecodeError:
        # Binary file — write via base64 round-trip won't work for agent context;
        # store as binary directly using exec + redirect
        import base64
        b64 = base64.b64encode(raw).decode()
        cmd = f"echo '{b64}' | base64 -d > {filename}"
        result = await _c10_exec(cmd)
        result["path"] = filename
    return JSONResponse({
        "ok": result.get("ok", True),
        "filename": filename,
        "size": len(raw),
        "path": result.get("path", filename),
    })


# ── User-facing sandbox terminal API ─────────────────────────────────────────

@app.post("/api/sandbox/exec", name="api_sandbox_exec")
async def api_sandbox_exec(request: Request):
    """Execute a shell command in C10 (agent) or C11 (multi-agento) sandbox.
    Body: {command: str, sandbox: "c10"|"c11", timeout?: int, cwd?: str, session_id?: str}
    Returns: {stdout, stderr, exit_code, timed_out}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    command = (body.get("command") or "").strip()
    if not command:
        return JSONResponse({"ok": False, "error": "command required"}, status_code=400)
    sandbox = (body.get("sandbox") or "c10").lower()
    timeout = min(max(int(body.get("timeout", 30)), 1), 120)
    cwd = body.get("cwd", ".")
    session_id = body.get("session_id", "")
    if sandbox == "c11":
        result = await _c11_exec(command, timeout=timeout, cwd=cwd, session_id=session_id)
    else:
        result = await _c10_exec(command, timeout=timeout, cwd=cwd)
    return JSONResponse(result)


# ── Container control API (start/stop optional containers) ───────────────────

# Containers that can be toggled on/off to save resources
_OPTIONAL_CONTAINERS = {"C2_agent-terminal", "C5_claude-code", "C7a_openclaw-gateway", "C7b_openclaw-cli", "C8_hermes-agent"}
# Containers that must stay running
_CORE_CONTAINERS = {"C1_copilot-api", "C3_browser-auth", "C6_kilocode", "C9_jokes", "C10_sandbox", "C11_sandbox"}


@app.get("/api/containers", name="api_containers")
async def api_containers():
    """Return status of all Docker containers with toggleable flag."""
    import subprocess
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.State}}"],
            capture_output=True, text=True, timeout=10,
        )
        containers = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                name, status, state = parts[0], parts[1], parts[2]
                containers.append({
                    "name": name,
                    "status": status,
                    "state": state,
                    "toggleable": name in _OPTIONAL_CONTAINERS,
                    "core": name in _CORE_CONTAINERS,
                })
        return JSONResponse({"ok": True, "containers": containers})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "containers": []})


@app.post("/api/container/toggle", name="api_container_toggle")
async def api_container_toggle(request: Request):
    """Start or stop an optional container.
    Body: {name: str, action: "start"|"stop"}
    Only works for containers in _OPTIONAL_CONTAINERS.
    """
    import subprocess
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    action = (body.get("action") or "").strip().lower()
    if name not in _OPTIONAL_CONTAINERS:
        return JSONResponse({"ok": False, "error": f"Container '{name}' is not toggleable (core container)"}, status_code=400)
    if action not in ("start", "stop"):
        return JSONResponse({"ok": False, "error": "action must be 'start' or 'stop'"}, status_code=400)
    try:
        r = subprocess.run(
            ["docker", action, name],
            capture_output=True, text=True, timeout=30,
        )
        return JSONResponse({
            "ok": r.returncode == 0,
            "name": name,
            "action": action,
            "output": (r.stdout + r.stderr).strip()[:500],
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


# ── Multi-Agent (smux-style) Workspace ───────────────────────────────────────

# Role definitions: id, label, emoji, system prompt focus
_MA_ROLES = {
    "supervisor": {
        "label": "Supervisor",
        "emoji": "🧭",
        "desc": "Breaks the overall task into sub-tasks and assigns them to specialist roles.",
    },
    "builder": {
        "label": "Builder",
        "emoji": "🔨",
        "desc": "Writes source files, installs dependencies, structures the project.",
    },
    "ui": {
        "label": "UI Agent",
        "emoji": "🎨",
        "desc": "Implements HTML/CSS/JS, templates, and visual front-end components.",
    },
    "executor": {
        "label": "Executor",
        "emoji": "⚡",
        "desc": "Runs code, starts servers, verifies processes are alive with curl.",
    },
    "tester": {
        "label": "Tester",
        "emoji": "🧪",
        "desc": "Validates output, runs test scripts, compares expected vs actual results.",
    },
    "debugger": {
        "label": "Debugger",
        "emoji": "🐛",
        "desc": "Reads logs, patches broken files, re-runs commands to fix errors.",
    },
    "reporter": {
        "label": "Reporter",
        "emoji": "📊",
        "desc": "Monitors agent progress and produces a structured status report: % complete, what's done, what's pending, any blockers.",
    },
}

# Default roles to activate when none specified
_MA_DEFAULT_ROLES = ["builder", "executor", "tester"]

# In-memory per-session control: pause events and injection queues
_ma_pause_flags: dict[str, asyncio.Event] = {}       # session_id → Event (set=running, clear=paused)
_ma_inject_queues: dict[str, asyncio.Queue] = {}     # "{session_id}/{pane_id}" → Queue[str]


def _ma_role_system_prompt(role: str, task: str, assignment: str) -> str:
    """Build a focused system prompt for a specific multi-agent role."""
    info = _MA_ROLES.get(role, {"desc": "complete your assigned task"})
    return (
        f"You are the {info['label']} agent in a multi-agent workspace.\n"
        f"Your specialty: {info['desc']}\n\n"
        f"OVERALL TASK: {task}\n\n"
        f"YOUR SPECIFIC ASSIGNMENT: {assignment}\n\n"
        f"Sandbox: Python 3.11, Node.js 20, bash.\n"
        f"Reply with ONE action per message using the protocol:\n"
        f"  FILE: filename.py\n```\n...content...\n```\n"
        f"  RUN: command\n"
        f"  INSTALL: package\n"
        f"  DONE: summary of what you accomplished\n\n"
        f"Focus only on your assignment. Be concise and execute immediately."
    )


async def _ma_role_loop(
    pane_id: str,
    role: str,
    assignment: str,
    overall_task: str,
    session_id: str,
    c1: str,
    agent_id: str,
    chat_mode: str,
    work_mode: str,
    max_steps: int,
    queue: asyncio.Queue,
    pause_event: asyncio.Event | None = None,
    inject_queue: asyncio.Queue | None = None,
) -> dict:
    """Run a single role-agent's ReAct loop and push SSE events to queue.

    Returns a summary dict with {role, pane_id, done, summary, files, steps}.
    """
    def _q(event: str, data: dict) -> None:
        data["pane_id"] = pane_id
        data["role"] = role
        queue.put_nowait(f"event: {event}\ndata: {json.dumps(data)}\n\n")

    client = _get_http()
    files_created: list[str] = []
    commands_run: list[str] = []
    history: list[dict] = []
    service_error_retries = 0
    turn_counter = 0

    initial_msg = _ma_role_system_prompt(role, overall_task, assignment)
    _q("pane_thinking", {"step": 0, "text": f"📋 Assignment: {assignment[:150]}"})

    for step in range(1, max_steps + 1):
        if not history:
            messages = [{"role": "user", "content": initial_msg}]
        else:
            _hist = list(history)
            if len(_hist) > 6:
                _hist = [_hist[0]] + _hist[-5:]
            messages = _hist

        headers = {
            "Content-Type": "application/json",
            "X-Agent-ID": agent_id,
        }
        if chat_mode:
            headers["X-Chat-Mode"] = chat_mode
        if work_mode in ("work", "web"):
            headers["X-Work-Mode"] = work_mode

        try:
            llm_r = await client.post(
                f"{c1}/v1/chat/completions",
                headers=headers,
                json={"model": "copilot", "messages": messages, "stream": False},
                timeout=180,
            )
            if llm_r.status_code != 200:
                _q("pane_error", {"message": f"C1 HTTP {llm_r.status_code}: {llm_r.text[:200]}"})
                return {"role": role, "pane_id": pane_id, "done": False, "summary": "C1 error", "files": files_created, "steps": step}
            response_text: str = llm_r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            service_error_retries += 1
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ Copilot unreachable — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": f"Copilot unreachable after retries: {exc}"})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": str(exc), "files": files_created, "steps": step}

        _SERVICE_PHRASES = ("something went wrong", "please try again", "experiencing high demand", "we're experiencing")
        if not response_text.strip():
            _q("pane_error", {"message": "Empty response from Copilot — auth may be expired."})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": "empty response", "files": files_created, "steps": step}

        if any(p in response_text.lower() for p in _SERVICE_PHRASES):
            service_error_retries += 1
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ Service error — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": "Copilot service error after retries."})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": "service error", "files": files_created, "steps": step}

        service_error_retries = 0
        await asyncio.sleep(4)  # settle delay

        # Emit thinking text
        thinking = _strip_tool_xml(response_text)
        if thinking:
            _q("pane_thinking", {"step": step, "text": thinking[:400]})

        # Check for DONE
        final = _parse_final_answer(response_text)
        if final:
            _q("pane_done", {"step": step, "summary": final[:300], "files": files_created})
            # Persist to DB
            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO multi_agent_pane_messages (session_id, pane_id, role, turn, role_type, content) VALUES (?,?,?,?,?,?)",
                        (session_id, pane_id, role, turn_counter + 1, "assistant", response_text[:2000]),
                    )
            except sqlite3.Error:
                pass
            return {"role": role, "pane_id": pane_id, "done": True, "summary": final, "files": files_created, "steps": step}

        # Check for injected user message between steps
        if inject_queue is not None:
            try:
                injected = inject_queue.get_nowait()
                if injected:
                    history.append({"role": "assistant", "content": response_text})
                    history.append({"role": "user", "content": f"[USER OVERRIDE]: {injected}"})
                    _q("pane_thinking", {"step": step, "text": f"💬 [Injected]: {injected[:200]}"})
            except asyncio.QueueEmpty:
                pass

        # Pause check between steps (asyncio.Event: set=running, clear=paused)
        if pause_event is not None:
            await pause_event.wait()

        # Parse and execute ALL tools from this response (not just the first)
        tools = _parse_all_actions(response_text)
        if tools:
            obs_parts: list[str] = []
            for tool in tools:
                _q("pane_tool", {"step": step, "type": tool.get("tool"), "content": str(tool.get("path") or tool.get("command") or tool.get("package") or "")[:200]})
                obs, meta = await _execute_tool(tool)
                _q("pane_obs", {"step": step, "stdout": obs[:500], "exit_code": meta.get("exit_code")})
                obs_parts.append(obs)

                if tool.get("tool") == "write_file":
                    fname = tool.get("path", "")
                    if fname and fname not in files_created:
                        files_created.append(fname)
                        _q("pane_file", {"step": step, "path": fname, "action": "created"})
                elif tool.get("tool") == "exec":
                    commands_run.append(tool.get("command", ""))

            turn_content = response_text
            obs_msg = "<observation>" + "\n".join(obs_parts) + "</observation>"
            turn_counter += 1
            if not history:
                history = [
                    {"role": "user", "content": initial_msg},
                    {"role": "assistant", "content": turn_content},
                    {"role": "user", "content": obs_msg},
                ]
            else:
                history.append({"role": "assistant", "content": turn_content})
                history.append({"role": "user", "content": obs_msg})

            # Persist
            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO multi_agent_pane_messages (session_id, pane_id, role, turn, role_type, content) VALUES (?,?,?,?,?,?)",
                        (session_id, pane_id, role, turn_counter, "assistant", turn_content[:2000]),
                    )
            except sqlite3.Error:
                pass
        else:
            # No tool and no DONE — push back asking for explicit action
            history.append({"role": "assistant", "content": response_text})
            history.append({"role": "user", "content": "Please use FILE:, RUN:, INSTALL:, or DONE: to take an action."})

    _q("pane_done", {"step": max_steps, "summary": f"Reached {max_steps} step limit.", "files": files_created})
    return {"role": role, "pane_id": pane_id, "done": False, "summary": "step limit reached", "files": files_created, "steps": max_steps}


@app.get("/multi-agent", response_class=HTMLResponse, name="page_multi_agent")
async def page_multi_agent(request: Request):
    """Multi-agent workspace — smux-style pane layout with parallel role agents."""
    return templates.TemplateResponse(request, "multi_agent.html", {
        "agents": AGENTS,
        "ma_roles": _MA_ROLES,
    })


@app.get("/api/multi-agent/run", name="api_multi_agent_run")
async def api_multi_agent_run(
    request: Request,
    task: str = "",
    session_id: str = "",
    roles: str = "",
    max_steps: int = 8,
    chat_mode: str = "auto",
    work_mode: str = "work",
):
    """SSE stream for multi-agent parallel execution.

    Supervisor decomposes the task into role assignments, then all assigned
    role-agents run concurrently in their own panes, sharing the C10 workspace.

    Query params:
      task       — overall task description
      session_id — for session persistence (auto-generated if empty)
      roles      — comma-separated role names (default: builder,executor,tester)
      max_steps  — max ReAct steps per role-agent (default 8, max 12)
      chat_mode  — auto | quick | deep
      work_mode  — work | web

    SSE events (all include pane_id + role fields):
      pane_init     — {pane_id, role, label, emoji}   pane registered
      pane_thinking — {pane_id, role, step, text}     LLM reasoning
      pane_tool     — {pane_id, role, step, type, content}
      pane_obs      — {pane_id, role, step, stdout, exit_code}
      pane_file     — {pane_id, role, step, path, action}
      pane_done     — {pane_id, role, step, summary, files}
      pane_error    — {pane_id, role, message}
      supervisor    — {step, text, assignments}        supervisor output
      final         — {summary, results, session_id}  all panes done
    """
    task = task.strip()
    max_steps = max(1, min(12, max_steps))
    c1 = _urls()["c1"]

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def generate():
        nonlocal task, session_id

        if not task:
            yield _sse("error", {"message": "No task provided."})
            return

        client = _get_http()

        # ── Auth check ─────────────────────────────────────────────────────────
        c3_url = _urls().get("c3", "http://browser-auth:8001")
        try:
            _auth_r = await client.get(f"{c3_url}/session-health", timeout=8)
            _auth_data = _auth_r.json() if _auth_r.status_code == 200 else {}
            _session_status = _auth_data.get("session", "unknown")
        except Exception:
            _session_status = "unknown"

        if _session_status != "active":
            yield _sse("error", {"message": "M365 not authenticated. Open :6080 to sign in.",
                                 "auth_url": "http://localhost:6080/?resize=scale&autoconnect=true"})
            return

        # Expand C3 pool for parallel run
        active_roles = [r.strip() for r in roles.split(",") if r.strip() in _MA_ROLES] if roles else _MA_DEFAULT_ROLES
        active_roles = [r for r in active_roles if r != "supervisor"]  # supervisor is implicit

        if len(active_roles) > 1:
            pool_size = max(1, int(os.environ.get("C3_POOL_SIZE_PARALLEL", "12")))
            try:
                await client.post(f"{c3_url}/pool-expand", params={"target_size": pool_size}, timeout=90)
            except Exception:
                pass

        # ── Create session record ──────────────────────────────────────────────
        now = datetime.now(timezone.utc).isoformat()
        if not session_id:
            session_id = "mas_" + uuid.uuid4().hex[:8]
        try:
            with _db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO multi_agent_sessions (id, created_at, updated_at, task, status, roles) VALUES (?,?,?,?,?,?)",
                    (session_id, now, now, task[:1000], "running", json.dumps(active_roles)),
                )
        except sqlite3.Error:
            pass

        yield _sse("session", {"session_id": session_id, "roles": active_roles})

        # ── Supervisor: decompose task into role assignments ────────────────────
        yield _sse("supervisor", {"step": 0, "text": f"🧭 Supervisor decomposing task: {task[:100]}..."})

        supervisor_prompt = (
            f"You are the Supervisor in a multi-agent coding workspace.\n"
            f"Break the following task into specific assignments for these specialist agents:\n"
            + "\n".join(f"  - {r}: {_MA_ROLES[r]['desc']}" for r in active_roles)
            + f"\n\nTASK: {task}\n\n"
            f"Output ONLY an assignment block like:\n"
            + "\n".join(f"ASSIGN {r}: [specific sub-task for this role]" for r in active_roles)
            + "\n\nBe concise and concrete. Each assignment should be actionable in 1-3 steps."
        )
        try:
            sup_r = await client.post(
                f"{c1}/v1/chat/completions",
                headers={"Content-Type": "application/json", "X-Agent-ID": "ma-supervisor"},
                json={"model": "copilot", "messages": [{"role": "user", "content": supervisor_prompt}], "stream": False},
                timeout=60,
            )
            sup_text = sup_r.json().get("choices", [{}])[0].get("message", {}).get("content", "") if sup_r.status_code == 200 else ""
        except Exception as exc:
            sup_text = ""
            yield _sse("supervisor", {"step": 0, "text": f"⚠️ Supervisor failed: {exc} — using default assignments"})

        # Parse ASSIGN lines from supervisor response
        assignments: dict[str, str] = {}
        for line in sup_text.splitlines():
            m = re.match(r"ASSIGN\s+(\w+)\s*:\s*(.+)", line.strip(), re.IGNORECASE)
            if m and m.group(1).lower() in active_roles:
                assignments[m.group(1).lower()] = m.group(2).strip()

        # Fall back to generic assignments for any missing roles
        for r in active_roles:
            if r not in assignments:
                assignments[r] = f"Complete your part of: {task}"

        yield _sse("supervisor", {
            "step": 1,
            "text": sup_text[:600] if sup_text else "Using default assignments.",
            "assignments": assignments,
        })

        # ── Announce panes ─────────────────────────────────────────────────────
        for r in active_roles:
            info = _MA_ROLES.get(r, {"label": r, "emoji": "🤖"})
            yield _sse("pane_init", {
                "pane_id": f"ma-{r}",
                "role": r,
                "label": info["label"],
                "emoji": info["emoji"],
                "assignment": assignments.get(r, ""),
            })

        # ── Launch role agents in parallel ─────────────────────────────────────
        queue: asyncio.Queue[str] = asyncio.Queue()
        active_panes = set(f"ma-{r}" for r in active_roles)
        pane_results: dict[str, dict] = {}

        # Create pause event (set = running, clear = paused) and injection queues
        pause_event = asyncio.Event()
        pause_event.set()
        _ma_pause_flags[session_id] = pause_event

        inject_qs: dict[str, asyncio.Queue] = {}
        for r in active_roles:
            key = f"{session_id}/ma-{r}"
            iq: asyncio.Queue = asyncio.Queue()
            inject_qs[key] = iq
            _ma_inject_queues[key] = iq

        async def run_role(r: str) -> None:
            inject_key = f"{session_id}/ma-{r}"
            result = await _ma_role_loop(
                pane_id=f"ma-{r}",
                role=r,
                assignment=assignments.get(r, task),
                overall_task=task,
                session_id=session_id,
                c1=c1,
                agent_id=f"ma-{r}",
                chat_mode=chat_mode,
                work_mode=work_mode,
                max_steps=max_steps,
                queue=queue,
                pause_event=pause_event,
                inject_queue=inject_qs.get(inject_key),
            )
            pane_results[r] = result
            active_panes.discard(f"ma-{r}")
            queue.put_nowait("__pane_done__")

        tasks = [asyncio.create_task(run_role(r)) for r in active_roles]

        # Stream queue events until all panes finish
        panes_done = 0
        total_panes = len(active_roles)
        while panes_done < total_panes:
            if await request.is_disconnected():
                for t in tasks:
                    t.cancel()
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if item == "__pane_done__":
                panes_done += 1
            else:
                yield item

        await asyncio.gather(*tasks, return_exceptions=True)

        # Clean up control structures
        _ma_pause_flags.pop(session_id, None)
        for key in list(inject_qs.keys()):
            _ma_inject_queues.pop(key, None)

        # ── Final summary ──────────────────────────────────────────────────────
        done_roles = [r for r, res in pane_results.items() if res.get("done")]
        all_files = list({f for res in pane_results.values() for f in (res.get("files") or [])})
        summary = f"{len(done_roles)}/{total_panes} roles completed. Files: {', '.join(all_files) or 'none'}."

        try:
            with _db() as conn:
                conn.execute(
                    "UPDATE multi_agent_sessions SET status=?, updated_at=?, summary=? WHERE id=?",
                    ("completed", datetime.now(timezone.utc).isoformat(), summary[:500], session_id),
                )
        except sqlite3.Error:
            pass

        yield _sse("final", {
            "summary": summary,
            "session_id": session_id,
            "results": {r: {"done": res.get("done"), "summary": res.get("summary", ""), "files": res.get("files", [])}
                        for r, res in pane_results.items()},
        })

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.post("/api/multi-agent/pause/{session_id}", name="api_ma_pause")
async def api_ma_pause(session_id: str):
    """Pause all role-agents in a running multi-agent session (after their current step)."""
    evt = _ma_pause_flags.get(session_id)
    if evt:
        evt.clear()  # cleared = paused; agents block on await evt.wait()
        return {"ok": True, "state": "paused"}
    return {"ok": False, "error": "session not found or already finished"}


@app.post("/api/multi-agent/resume/{session_id}", name="api_ma_resume")
async def api_ma_resume(session_id: str):
    """Resume a paused multi-agent session."""
    evt = _ma_pause_flags.get(session_id)
    if evt:
        evt.set()  # set = running
        return {"ok": True, "state": "running"}
    return {"ok": False, "error": "session not found or already finished"}


@app.post("/api/multi-agent/inject/{session_id}/{pane_id}", name="api_ma_inject")
async def api_ma_inject(session_id: str, pane_id: str, body: dict = Body(...)):
    """Inject a user message into a specific running pane's context."""
    key = f"{session_id}/{pane_id}"
    iq = _ma_inject_queues.get(key)
    if iq is None:
        return {"ok": False, "error": "pane not active"}
    message = str(body.get("message", "")).strip()
    if not message:
        return {"ok": False, "error": "empty message"}
    await iq.put(message)
    return {"ok": True, "queued": message[:100]}


@app.get("/api/multi-agent/sessions", name="api_multi_agent_sessions")
async def api_multi_agent_sessions(limit: int = 10):
    """List recent multi-agent sessions."""
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, task, status, roles, summary FROM multi_agent_sessions ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return JSONResponse([dict(r) for r in rows])
    except sqlite3.Error as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── C11 multi-agent role loop ─────────────────────────────────────────────────

async def _ma_role_loop_c11(
    pane_id: str,
    role: str,
    assignment: str,
    overall_task: str,
    session_id: str,
    c1: str,
    agent_id: str,
    chat_mode: str,
    work_mode: str,
    max_steps: int,
    queue: asyncio.Queue,
    pause_event: asyncio.Event | None = None,
    inject_queue: asyncio.Queue | None = None,
) -> dict:
    """Like _ma_role_loop but uses C11 (session-scoped workspace)."""
    def _q(event: str, data: dict) -> None:
        data["pane_id"] = pane_id
        data["role"] = role
        queue.put_nowait(f"event: {event}\ndata: {json.dumps(data)}\n\n")

    client = _get_http()
    files_created: list[str] = []
    commands_run: list[str] = []
    history: list[dict] = []
    service_error_retries = 0
    turn_counter = 0

    initial_msg = _ma_role_system_prompt(role, overall_task, assignment)
    _q("pane_thinking", {"step": 0, "text": f"📋 Assignment: {assignment[:150]}"})

    for step in range(1, max_steps + 1):
        if not history:
            messages = [{"role": "user", "content": initial_msg}]
        else:
            _hist = list(history)
            if len(_hist) > 6:
                _hist = [_hist[0]] + _hist[-5:]
            messages = _hist

        headers = {"Content-Type": "application/json", "X-Agent-ID": agent_id}
        if chat_mode:
            headers["X-Chat-Mode"] = chat_mode
        if work_mode in ("work", "web"):
            headers["X-Work-Mode"] = work_mode

        try:
            llm_r = await client.post(
                f"{c1}/v1/chat/completions",
                headers=headers,
                json={"model": "copilot", "messages": messages, "stream": False},
                timeout=180,
            )
            if llm_r.status_code != 200:
                _q("pane_error", {"message": f"C1 HTTP {llm_r.status_code}: {llm_r.text[:200]}"})
                return {"role": role, "pane_id": pane_id, "done": False, "summary": "C1 error", "files": files_created, "steps": step}
            response_text: str = llm_r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            service_error_retries += 1
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ Copilot unreachable — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": f"Copilot unreachable after retries: {exc}"})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": str(exc), "files": files_created, "steps": step}

        _SERVICE_PHRASES = ("something went wrong", "please try again", "experiencing high demand", "we're experiencing")
        if not response_text.strip():
            _q("pane_error", {"message": "Empty response from Copilot — auth may be expired."})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": "empty response", "files": files_created, "steps": step}

        if any(p in response_text.lower() for p in _SERVICE_PHRASES):
            service_error_retries += 1
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ Service error — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": "Copilot service error after retries."})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": "service error", "files": files_created, "steps": step}

        service_error_retries = 0
        await asyncio.sleep(4)

        thinking = _strip_tool_xml(response_text)
        if thinking:
            _q("pane_thinking", {"step": step, "text": thinking[:400]})

        final = _parse_final_answer(response_text)
        if final:
            _q("pane_done", {"step": step, "summary": final[:300], "files": files_created})
            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO ma_pane_messages (session_id, pane_id, role, turn, role_type, content) VALUES (?,?,?,?,?,?)",
                        (session_id, pane_id, role, turn_counter + 1, "assistant", response_text[:2000]),
                    )
            except sqlite3.Error:
                pass
            return {"role": role, "pane_id": pane_id, "done": True, "summary": final, "files": files_created, "steps": step}

        if inject_queue is not None:
            try:
                injected = inject_queue.get_nowait()
                if injected:
                    history.append({"role": "assistant", "content": response_text})
                    history.append({"role": "user", "content": f"[USER OVERRIDE]: {injected}"})
                    _q("pane_thinking", {"step": step, "text": f"💬 [Injected]: {injected[:200]}"})
            except asyncio.QueueEmpty:
                pass

        if pause_event is not None:
            await pause_event.wait()

        # Execute all tools via C11 (session-scoped workspace)
        tools = _parse_all_actions(response_text)
        if tools:
            obs_parts: list[str] = []
            for tool in tools:
                _q("pane_tool", {"step": step, "type": tool.get("tool"), "content": str(tool.get("path") or tool.get("command") or tool.get("package") or "")[:200]})
                obs, meta = await _execute_tool_c11(tool, session_id)
                _q("pane_obs", {"step": step, "stdout": obs[:500], "exit_code": meta.get("exit_code")})
                obs_parts.append(obs)

                if tool.get("tool") == "write_file":
                    fname = tool.get("path", "")
                    if fname and fname not in files_created:
                        files_created.append(fname)
                        _q("pane_file", {"step": step, "path": fname, "action": "created"})
                elif tool.get("tool") == "exec":
                    commands_run.append(tool.get("command", ""))

            turn_content = response_text
            obs_msg = "<observation>" + "\n".join(obs_parts) + "</observation>"
            turn_counter += 1
            if not history:
                history = [
                    {"role": "user", "content": initial_msg},
                    {"role": "assistant", "content": turn_content},
                    {"role": "user", "content": obs_msg},
                ]
            else:
                history.append({"role": "assistant", "content": turn_content})
                history.append({"role": "user", "content": obs_msg})

            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO ma_pane_messages (session_id, pane_id, role, turn, role_type, content) VALUES (?,?,?,?,?,?)",
                        (session_id, pane_id, role, turn_counter, "assistant", turn_content[:2000]),
                    )
            except sqlite3.Error:
                pass
        else:
            history.append({"role": "assistant", "content": response_text})
            history.append({"role": "user", "content": "Please use FILE:, RUN:, INSTALL:, or DONE: to take an action."})

    _q("pane_done", {"step": max_steps, "summary": f"Reached {max_steps} step limit.", "files": files_created})
    return {"role": role, "pane_id": pane_id, "done": False, "summary": "step limit reached", "files": files_created, "steps": max_steps}


# ── /multi-Agento routes ──────────────────────────────────────────────────────

@app.get("/multi-agento", response_class=HTMLResponse, include_in_schema=False)
async def page_multi_agento_lower(request: Request, task: str = ""):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/multi-Agento" + (f"?task={task}" if task else ""), status_code=301)

@app.get("/multi-Agento", response_class=HTMLResponse, name="page_multi_agento")
async def page_multi_agento(request: Request, task: str = ""):
    """Full-featured multi-agent IDE with C11 session-scoped workspace."""
    return templates.TemplateResponse(request, "multi_agento.html", {
        "agents": AGENTS,
        "ma_roles": _MA_ROLES,
        "task": task,
    })


@app.get("/api/ma/run", name="api_ma_run")
async def api_ma_run(
    request: Request,
    task: str = "",
    session_id: str = "",
    roles: str = "",
    max_steps: int = 8,
    chat_mode: str = "auto",
    work_mode: str = "work",
):
    """SSE stream for /multi-Agento parallel execution using C11 session-scoped workspace."""
    if not task.strip():
        return JSONResponse({"error": "task required"}, status_code=400)

    c1 = _urls().get("c1", "http://localhost:8000")
    agent_id = "c9-jokes"
    max_steps = max(2, min(12, max_steps))

    if not session_id:
        session_id = "ma-" + uuid.uuid4().hex[:8]

    active_roles = [r.strip() for r in roles.split(",") if r.strip() in _MA_ROLES] if roles else _MA_DEFAULT_ROLES[:]

    async def generate():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        yield _sse("session", {"session_id": session_id, "roles": active_roles})

        # Persist session start
        now = datetime.now(timezone.utc).isoformat()
        try:
            with _db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO ma_sessions (id, created_at, updated_at, task, status, roles) VALUES (?,?,?,?,?,?)",
                    (session_id, now, now, task, "running", json.dumps(active_roles)),
                )
        except sqlite3.Error:
            pass

        # Set up pause/inject per-session state
        pause_event = asyncio.Event()
        pause_event.set()
        _ma_pause_flags[session_id] = pause_event
        inject_qs: dict[str, asyncio.Queue] = {}
        for r in active_roles:
            key = f"{session_id}/ma-{r}"
            iq: asyncio.Queue = asyncio.Queue()
            inject_qs[key] = iq
            _ma_inject_queues[key] = iq

        # Supervisor decomposition
        yield _sse("supervisor", {"text": f"🧭 Decomposing task for {len(active_roles)} role(s)…"})
        assignments: dict[str, str] = {}
        try:
            client = _get_http()
            sup_prompt = (
                f"You are a supervisor AI coordinating a multi-agent team to complete this task:\n\n"
                f"TASK: {task}\n\n"
                f"Active roles: {', '.join(active_roles)}\n\n"
                f"For each role, write a focused assignment (1-2 sentences max).\n"
                f"Format: ROLE_NAME: assignment text\n"
                f"Be specific. Each role has distinct responsibilities."
            )
            sup_r = await client.post(
                f"{c1}/v1/chat/completions",
                headers={"Content-Type": "application/json", "X-Agent-ID": agent_id},
                json={"model": "copilot", "messages": [{"role": "user", "content": sup_prompt}], "stream": False},
                timeout=60,
            )
            if sup_r.status_code == 200:
                sup_text = sup_r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                for line in sup_text.splitlines():
                    for r in active_roles:
                        if line.lower().startswith(r + ":"):
                            assignments[r] = line[len(r)+1:].strip()
                            break
        except Exception as exc:
            yield _sse("supervisor", {"text": f"⚠️ Supervisor error: {exc} — using default assignments"})

        for r in active_roles:
            if r not in assignments:
                assignments[r] = f"Complete the {r} portion of: {task}"

        # Initialize panes
        for r in active_roles:
            pane_id = f"ma-{r}"
            yield _sse("pane_init", {"pane_id": pane_id, "role": r, "assignment": assignments[r],
                                      "label": _MA_ROLES.get(r, {}).get("label", r.title())})

        # Run all roles concurrently
        event_queue: asyncio.Queue = asyncio.Queue()
        all_files: list[str] = []

        async def run_role(r: str) -> dict:
            pane_id = f"ma-{r}"
            return await _ma_role_loop_c11(
                pane_id=pane_id, role=r, assignment=assignments[r],
                overall_task=task, session_id=session_id,
                c1=c1, agent_id=agent_id,
                chat_mode=chat_mode, work_mode=work_mode,
                max_steps=max_steps, queue=event_queue,
                pause_event=pause_event,
                inject_queue=inject_qs.get(f"{session_id}/ma-{r}"),
            )

        tasks = [asyncio.create_task(run_role(r)) for r in active_roles]
        done_count = 0
        total = len(tasks)

        while done_count < total:
            pending = [t for t in tasks if not t.done()]
            try:
                evt_text = event_queue.get_nowait()
                yield evt_text
                continue
            except asyncio.QueueEmpty:
                pass
            if not pending:
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                break
            await asyncio.sleep(0.05)
            # Drain queue
            try:
                while True:
                    yield event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            # Check completed tasks
            newly_done = [t for t in tasks if t.done() and not getattr(t, "_reported", False)]
            for t in newly_done:
                t._reported = True  # type: ignore[attr-defined]
                done_count += 1
                try:
                    res = t.result()
                    all_files.extend(res.get("files", []))
                except Exception:
                    pass

        # Final drain
        while not event_queue.empty():
            yield event_queue.get_nowait()

        # Cleanup pause/inject state
        _ma_pause_flags.pop(session_id, None)
        for key in list(inject_qs.keys()):
            _ma_inject_queues.pop(key, None)

        # Gather results
        results = []
        for t in tasks:
            try:
                results.append(t.result())
            except Exception:
                pass

        # Persist session completion
        summary = " | ".join(r.get("summary", "")[:80] for r in results if r.get("summary"))
        try:
            with _db() as conn:
                conn.execute(
                    "UPDATE ma_sessions SET status=?, updated_at=?, summary=?, files_created=?, steps_taken=? WHERE id=?",
                    ("completed", datetime.now(timezone.utc).isoformat(), summary[:500],
                     json.dumps(list(dict.fromkeys(all_files))),
                     sum(r.get("steps", 0) for r in results),
                     session_id),
                )
        except sqlite3.Error:
            pass

        yield _sse("final", {
            "session_id": session_id,
            "summary": summary or "All agents completed.",
            "files_created": list(dict.fromkeys(all_files)),
            "roles_done": len(results),
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /multi-Agento pause, resume, inject (reuse same state dicts) ──────────────

@app.post("/api/ma/pause/{session_id}", name="api_ma_agento_pause")
async def api_ma_agento_pause(session_id: str):
    evt = _ma_pause_flags.get(session_id)
    if evt:
        evt.clear()
        return {"ok": True, "state": "paused"}
    return {"ok": False, "error": "session not found"}


@app.post("/api/ma/resume/{session_id}", name="api_ma_agento_resume")
async def api_ma_agento_resume(session_id: str):
    evt = _ma_pause_flags.get(session_id)
    if evt:
        evt.set()
        return {"ok": True, "state": "running"}
    return {"ok": False, "error": "session not found"}


@app.post("/api/ma/inject/{session_id}/{pane_id}", name="api_ma_agento_inject")
async def api_ma_agento_inject(session_id: str, pane_id: str, body: dict = Body(...)):
    key = f"{session_id}/{pane_id}"
    iq = _ma_inject_queues.get(key)
    if iq is None:
        return {"ok": False, "error": "pane not active"}
    message = str(body.get("message", "")).strip()
    if not message:
        return {"ok": False, "error": "empty message"}
    await iq.put(message)
    return {"ok": True, "queued": message[:100]}


# ── /multi-Agento file management API (C11) ───────────────────────────────────

@app.get("/api/ma/files", name="api_ma_files")
async def api_ma_files(session_id: str = ""):
    result = await _c11_list_files(session_id=session_id)
    return JSONResponse(result)


@app.get("/api/ma/file", name="api_ma_file")
async def api_ma_file(path: str = "", session_id: str = ""):
    if not path:
        return JSONResponse({"ok": False, "error": "path required"}, status_code=400)
    result = await _c11_read_file(path, session_id=session_id)
    return JSONResponse(result)


@app.delete("/api/ma/file", name="api_ma_file_delete")
async def api_ma_file_delete(path: str = "", session_id: str = ""):
    if not path:
        return JSONResponse({"ok": False, "error": "path required"}, status_code=400)
    result = await _c11_delete(path, session_id=session_id)
    return JSONResponse(result)


@app.post("/api/ma/reset/{session_id}", name="api_ma_reset")
async def api_ma_reset(session_id: str):
    """Reset C11 workspace for a specific session only."""
    result = await _c11_reset(session_id)
    # Remove session's projects from DB
    try:
        with _db() as conn:
            conn.execute("DELETE FROM ma_projects WHERE session_id=?", (session_id,))
    except sqlite3.Error:
        pass
    return JSONResponse(result)


@app.get("/api/ma/sessions", name="api_ma_sessions")
async def api_ma_sessions(limit: int = 20):
    """List recent /multi-Agento sessions with C11 workspace info."""
    limit = max(1, min(50, limit))
    rows = []
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at, task, roles, status, steps_taken, files_created, summary "
                "FROM ma_sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.Error:
        pass
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/ma/projects", name="api_ma_projects")
async def api_ma_projects(session_id: str = ""):
    """List projects for a /multi-Agento session."""
    rows = []
    try:
        with _db() as conn:
            q = "SELECT id, session_id, created_at, name, display_name, description, status FROM ma_projects"
            params: list = []
            if session_id:
                q += " WHERE session_id=?"
                params.append(session_id)
            q += " ORDER BY created_at DESC"
            rows = conn.execute(q, params).fetchall()
    except sqlite3.Error:
        pass
    return JSONResponse([dict(r) for r in rows])


@app.post("/api/ma/project", name="api_ma_project_create")
async def api_ma_project_create(body: dict):
    """Create a project directory in the C11 session workspace."""
    raw_name   = (body.get("name") or "").strip()
    display    = (body.get("display_name") or raw_name).strip()
    desc       = (body.get("description") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    if not raw_name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    slug = re.sub(r"[^a-z0-9_\-]", "", raw_name.lower().replace(" ", "-"))
    if not slug:
        return JSONResponse({"ok": False, "error": "invalid name"}, status_code=400)
    mkdir_r = await _c11_mkdir(slug, session_id=session_id)
    if not mkdir_r.get("ok"):
        return JSONResponse({"ok": False, "error": "mkdir failed: " + (mkdir_r.get("error") or "unknown")}, status_code=500)
    proj_id = "map_" + uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO ma_projects (id, session_id, created_at, name, display_name, description) VALUES (?,?,?,?,?,?)",
                (proj_id, session_id, now, slug, display or slug, desc),
            )
    except sqlite3.IntegrityError:
        return JSONResponse({"ok": False, "error": f"Project '{slug}' already exists in this session"}, status_code=409)
    except sqlite3.Error as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "id": proj_id, "name": slug, "display_name": display or slug, "session_id": session_id})


@app.delete("/api/ma/project", name="api_ma_project_delete")
async def api_ma_project_delete(name: str = "", session_id: str = ""):
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    c11_r = await _c11_delete(name, session_id=session_id)
    try:
        with _db() as conn:
            conn.execute("DELETE FROM ma_projects WHERE name=? AND session_id=?", (name, session_id))
    except sqlite3.Error:
        pass
    return JSONResponse({"ok": c11_r.get("ok", False), "name": name})


@app.get("/api/ma/preview", name="api_ma_preview")
async def api_ma_preview(port: int = 3000, path: str = "/"):
    """Proxy to a web server running inside C11 sandbox."""
    c11_host = C11_URL.split("://")[-1].split(":")[0]  # e.g. "c11-sandbox"
    target = f"http://{c11_host}:{port}{path}"
    client = _get_http()
    try:
        r = await client.get(target, timeout=5)
        content_type = r.headers.get("content-type", "text/html")
        return Response(content=r.content, media_type=content_type, status_code=r.status_code)
    except Exception as exc:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;background:#0f1419;color:#e6edf3;padding:2rem'>"
            f"<h3>🔌 Preview not available</h3>"
            f"<p>Could not reach <code>http://c11-sandbox:{port}/</code></p>"
            f"<p style='color:#8b949e'>{exc}</p>"
            f"<p>The web server may still be starting. Wait a moment and refresh.</p>"
            f"</body></html>",
            status_code=502,
        )


@app.post("/api/ma/upload", name="api_ma_upload")
async def api_ma_upload(session_id: str = "", file: UploadFile = File(...)):
    """Upload a file to C11 session workspace."""
    raw = await file.read()
    filename = file.filename or "uploaded_file"
    try:
        content = raw.decode("utf-8")
        result = await _c11_write_file(filename, content, session_id=session_id)
    except UnicodeDecodeError:
        import base64
        b64 = base64.b64encode(raw).decode()
        cmd = f"echo '{b64}' | base64 -d > {filename}"
        result = await _c11_exec(cmd, session_id=session_id)
        result["path"] = filename
    return JSONResponse({
        "ok": result.get("ok", True),
        "filename": filename,
        "size": len(raw),
        "path": result.get("path", filename),
        "session_id": session_id,
    })


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "6090"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=os.environ.get("FLASK_DEBUG") == "1")
