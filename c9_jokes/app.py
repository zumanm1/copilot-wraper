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
from urllib.parse import quote, urlencode

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

# ── C12b Sandbox URL (lean coding/test sandbox) ──────────────────────────────
C12B_URL = os.environ.get("C12B_URL", "http://c12b-sandbox:8210").rstrip("/")

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
    "c12b": {"env": "C12B_URL", "default": "http://c12b-sandbox:8210", "label": "C12b lean sandbox", "health": "/health"},
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

TASK_MODE_OPTIONS = [
    {"id": "chat", "label": "Chat"},
    {"id": "sandbox", "label": "Sandbox"},
    {"id": "agent", "label": "Agent"},
    {"id": "multi-agent", "label": "Multi-Agent"},
    {"id": "multi-agento", "label": "multi-Agento"},
]

TASK_EXECUTOR_TARGET_OPTIONS = [
    {"id": "c12b", "label": "C12b Lean Sandbox"},
]

TASK_WORKFLOW_STEP_KINDS = [
    {"id": "trigger", "label": "Trigger"},
    {"id": "condition", "label": "Condition"},
    {"id": "chat", "label": "Chat"},
    {"id": "sandbox", "label": "Sandbox"},
    {"id": "agent", "label": "Agent"},
    {"id": "multi-agent", "label": "Multi-Agent"},
    {"id": "multi-agento", "label": "multi-Agento"},
    {"id": "alert", "label": "Alert"},
    {"id": "complete", "label": "Complete"},
]

TASK_AGENT_TARGET_OPTIONS = [
    {"id": "c2-aider", "label": "C2"},
    {"id": "c5-claude-code", "label": "C5"},
    {"id": "c6-kilocode", "label": "C6"},
    {"id": "c7-openclaw", "label": "C7b"},
    {"id": "c8-hermes", "label": "C8"},
    {"id": "c9-jokes", "label": "C9 Generic"},
]

TASK_SANDBOX_DEFAULTS = {
    "c12b": "/workspace",
}

TASK_TEMPLATES = [
    {
        "key": "weather-dublin",
        "name": "Weather in Dublin",
        "description": "Every 10 minutes, check Dublin weather and raise an alert if temperature is above 10C.",
        "mode": "chat",
        "schedule_kind": "recurring",
        "interval_minutes": 10,
        "tabs_required": 2,
        "planner_prompt": "Planner: weather check, threshold evaluation, alert generation, and context handoff between two tabs.",
        "executor_prompt": (
            "Check the current weather in Dublin, Ireland. If the temperature is above 10C, return strict JSON only: "
            "{\"triggered\": true|false, \"trigger\": \"Dublin weather\", \"title\": \"...\", "
            "\"summary\": \"...\", \"details\": {\"location\": \"Dublin\", \"temperature_c\": number, \"condition\": \"...\"}}. "
            "If the temperature is not above 10C, still return the same JSON with triggered=false."
        ),
        "context_handoff": "Tab 1 checks the weather. Copy the temperature and condition into Tab 2 for alert generation and visibility on the alerts page.",
        "trigger_mode": "json",
        "trigger_text": "",
    },
    {
        "key": "gmail-sender",
        "name": "Email from sampelexample",
        "description": "Recurring email watch for sampelexample@example.com with alert details.",
        "mode": "chat",
        "schedule_kind": "recurring",
        "interval_minutes": 10,
        "tabs_required": 2,
        "planner_prompt": "Planner: detect new email, extract sender/subject/time, create visible alert, and share context across two tabs.",
        "executor_prompt": (
            "Check Gmail or Outlook for a new email from sampelexample@example.com. Return strict JSON only: "
            "{\"triggered\": true|false, \"trigger\": \"incoming email from sampelexample\", "
            "\"title\": \"...\", \"summary\": \"...\", \"details\": {\"sender\": \"...\", \"subject\": \"...\", "
            "\"received_at\": \"...\"}}. If no matching email is found, return triggered=false with a summary."
        ),
        "context_handoff": "Tab 1 detects the email and extracts sender, subject, and time. Tab 2 creates the alert record using the copied email details.",
        "trigger_mode": "json",
        "trigger_text": "",
    },
    {
        "key": "sharepoint-new-file",
        "name": "New SharePoint file",
        "description": "Recurring SharePoint folder watcher with alert output.",
        "mode": "chat",
        "schedule_kind": "recurring",
        "interval_minutes": 10,
        "tabs_required": 2,
        "planner_prompt": "Planner: detect file, extract path/name/time, generate alert, and hand off details between tabs.",
        "executor_prompt": (
            "Check an M365 SharePoint folder for a newly added file. Return strict JSON only: "
            "{\"triggered\": true|false, \"trigger\": \"new SharePoint file event\", \"title\": \"...\", "
            "\"summary\": \"...\", \"details\": {\"file_name\": \"...\", \"folder\": \"...\", \"detected_at\": \"...\"}}. "
            "If no new file is found, return triggered=false with the same schema."
        ),
        "context_handoff": "Tab 1 gathers file metadata. Tab 2 uses the copied file name and folder path to create the visible alert.",
        "trigger_mode": "json",
        "trigger_text": "",
    },
    {
        "key": "m365-outlook-alert",
        "name": "M365 Outlook alert email",
        "description": "Recurring M365 Outlook watcher for alerts@company.com.",
        "mode": "chat",
        "schedule_kind": "recurring",
        "interval_minutes": 10,
        "tabs_required": 2,
        "planner_prompt": "Planner: detect matching Outlook email, extract core details, create alert, and share context between tabs.",
        "executor_prompt": (
            "Check M365 Outlook for a new email from alerts@company.com. Return strict JSON only: "
            "{\"triggered\": true|false, \"trigger\": \"M365 Outlook email\", \"title\": \"...\", "
            "\"summary\": \"...\", \"details\": {\"sender\": \"...\", \"subject\": \"...\", \"received_at\": \"...\"}}. "
            "If no matching email is found, return triggered=false."
        ),
        "context_handoff": "Tab 1 extracts Outlook message details. Tab 2 turns those details into a visible alert and keeps the copied context.",
        "trigger_mode": "json",
        "trigger_text": "",
    },
    {
        "key": "outlook-sharepoint-linked",
        "name": "Outlook plus SharePoint",
        "description": "Combined email and SharePoint flow with alert output when both signals match.",
        "mode": "chat",
        "schedule_kind": "recurring",
        "interval_minutes": 10,
        "tabs_required": 2,
        "planner_prompt": "Planner: detect matching email, extract attachment/link, verify SharePoint file, then create a combined alert with copied context.",
        "executor_prompt": (
            "Check M365 Outlook for an email from sampelexample@example.com. If it contains an attachment name or SharePoint link, "
            "check SharePoint for the related file. Return strict JSON only: {\"triggered\": true|false, "
            "\"trigger\": \"email and linked SharePoint document\", \"title\": \"...\", \"summary\": \"...\", "
            "\"details\": {\"sender\": \"...\", \"subject\": \"...\", \"file_name\": \"...\", \"detected_at\": \"...\"}}. "
            "If the full match is not found, return triggered=false with the same schema."
        ),
        "context_handoff": "Tab 1 gathers the email context. Tab 2 verifies the SharePoint match and merges both contexts into the final alert.",
        "trigger_mode": "json",
        "trigger_text": "",
    },
    {
        "key": "sandbox-python-validate",
        "name": "Sandbox Python validate/test",
        "description": "Run code in C12b, validate syntax, run tests, and raise an alert if execution or tests fail.",
        "mode": "sandbox",
        "schedule_kind": "manual",
        "interval_minutes": 0,
        "tabs_required": 1,
        "executor_target": "c12b",
        "workspace_dir": "/workspace",
        "planner_prompt": "Write or update code in the sandbox workspace, then validate and test it before raising an alert.",
        "executor_prompt": "printf 'def add(a, b):\\n    return a + b\\n' > app.py && printf 'from app import add\\nprint(add(2, 3))\\n' > smoke.py && python3 smoke.py",
        "validation_command": "python3 -m py_compile app.py smoke.py",
        "test_command": "python3 smoke.py",
        "context_handoff": "Tasked stores the command plan. piplinetask logs each sandbox stage. Alerts show failures or requested summaries.",
        "trigger_mode": "always",
        "trigger_text": "sandbox execution",
    },
]

TASK_EXAMPLE_SPECS = [
    {
        "id": "task_example_jhb_nvidia",
        "template_key": "weather-dublin",
        "name": "Example 1: Johannesburg weather + Nvidia market cap",
        "trigger": "Johannesburg weather and Nvidia market cap rule",
        "title": "Example Johannesburg + Nvidia rule matched",
        "summary": "Every 12 minutes, Johannesburg weather and Nvidia market cap were checked. The condition matched, so a repeating alert cadence of every 5 minutes was armed.",
        "details": {
            "city": "Johannesburg",
            "temp_c": 18.4,
            "market_cap_usd": 2120000000000,
            "repeat_every_minutes": 5,
        },
        "acknowledged": False,
    },
    {
        "id": "task_example_gmail_sender",
        "template_key": "gmail-sender",
        "name": "Example 2: Email from sampelexample",
        "trigger": "incoming email from sampelexample",
        "title": "Example email detected from sampelexample",
        "summary": "A new email from sampelexample@example.com was detected, extracted, then copied into a second tab before the alert was created.",
        "details": {
            "sender": "sampelexample@example.com",
            "subject": "Project handoff update",
            "received_at": "2026-04-01T08:10:00Z",
        },
        "acknowledged": False,
    },
    {
        "id": "task_example_sharepoint_file",
        "template_key": "sharepoint-new-file",
        "name": "Example 3: New SharePoint file via C6",
        "trigger": "new SharePoint file event",
        "title": "Example SharePoint file detected",
        "summary": "A C6 agent-style run detected a new SharePoint file, returned structured feedback, and advanced the pipeline into alert creation.",
        "details": {
            "file_name": "Quarterly-Forecast.xlsx",
            "folder": "/Shared Documents/Finance/Forecasts",
            "detected_at": "2026-04-01T08:20:00Z",
            "agent_id": "c6-kilocode",
        },
        "acknowledged": False,
    },
    {
        "id": "task_example_outlook_sharepoint",
        "template_key": "outlook-sharepoint-linked",
        "name": "Example 4: Outlook plus SharePoint",
        "trigger": "email and linked SharePoint document",
        "title": "Example linked email and file match",
        "summary": "A matching Outlook email and SharePoint document were found, merged across two tabs, and the combined alert was acknowledged.",
        "details": {
            "sender": "sampelexample@example.com",
            "subject": "Updated project timeline",
            "file_name": "Project-Timeline.docx",
            "detected_at": "2026-04-01T08:40:00Z",
        },
        "acknowledged": True,
    },
]

TASKED_AUTHORING_EXAMPLES = [
    {
        "id": "existing-template-weather",
        "label": "Existing template: Dublin weather",
        "strategy": "existing-template",
        "prompt": (
            "Every 10 minutes, daily, check the weather in Dublin, Ireland. "
            "If the temperature is above 10C, create an alert visible on the Alerts page. "
            "Use 2 tabs and copy the weather result from one tab into the other."
        ),
    },
    {
        "id": "freehand-sandbox-jhb-nvda",
        "label": "Free-hand: Johannesburg + Nvidia via C12b",
        "strategy": "freehand",
        "prompt": (
            "Every 12 minutes from now, use C12b to run Python code that checks the weather in Johannesburg and Nvidia market cap. "
            "If Johannesburg is above 14 degrees C and Nvidia market cap is above 2 trillion USD, "
            "raise a warning alert every 5 minutes while true, then complete the run."
        ),
    },
]

TASKED_AUTHORING_PROMPT_PATH = BASE_DIR / "prompts" / "tasked_authoring.md"
TASKED_AUTHOR_ENABLE_LLM = str(os.environ.get("C9_TASKED_AUTHOR_ENABLE_LLM", "")).strip().lower() in {"1", "true", "yes", "on"}


# ── Shared async HTTP client ─────────────────────────────────────────────────
_http: httpx.AsyncClient | None = None
_runtime_cache: dict[str, object] = {"captured_monotonic": 0.0, "data": None}
_runtime_cache_lock: asyncio.Lock | None = None
_task_scheduler_task: asyncio.Task | None = None
_task_runner_ids: set[str] = set()
_task_runner_lock: asyncio.Lock | None = None
_task_scheduler_owner = "c9-" + uuid.uuid4().hex[:10]

RUNTIME_SLOW_MS = max(500, int(os.environ.get("C9_RUNTIME_SLOW_MS", "2500")))
RUNTIME_CACHE_TTL_S = max(1.0, float(os.environ.get("C9_RUNTIME_CACHE_TTL_S", "3")))
WAIT_HEARTBEAT_S = max(5.0, float(os.environ.get("C9_WAIT_HEARTBEAT_S", "15")))
POOL_TIGHT_THRESHOLD = max(1, int(os.environ.get("C9_POOL_TIGHT_THRESHOLD", "2")))
TASK_SCHEDULER_INTERVAL_S = max(15.0, float(os.environ.get("C9_TASK_SCHEDULER_INTERVAL_S", "30")))
_COPILOT_SERVICE_PHRASES = (
    "something went wrong",
    "please try again later",
    "please try again",
    "please retry",
    "try again later",
    "experiencing high demand",
    "we're experiencing",
    "high demand",
)


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(connect=5.0, read=360.0, write=10.0, pool=10.0),
        )
    return _http


def _get_runtime_lock() -> asyncio.Lock:
    global _runtime_cache_lock
    if _runtime_cache_lock is None:
        _runtime_cache_lock = asyncio.Lock()
    return _runtime_cache_lock


def _get_task_runner_lock() -> asyncio.Lock:
    global _task_runner_lock
    if _task_runner_lock is None:
        _task_runner_lock = asyncio.Lock()
    return _task_runner_lock


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
    # Migrate: task automation + alerts foundation
    try:
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_definitions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'chat',
                    schedule_kind TEXT NOT NULL DEFAULT 'manual',
                    interval_minutes INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    tabs_required INTEGER DEFAULT 1,
                    template_key TEXT DEFAULT '',
                    executor_target TEXT DEFAULT '',
                    workspace_dir TEXT DEFAULT '',
                    planner_prompt TEXT DEFAULT '',
                    executor_prompt TEXT DEFAULT '',
                    validation_command TEXT DEFAULT '',
                    test_command TEXT DEFAULT '',
                    sandbox_assist INTEGER DEFAULT 0,
                    sandbox_assist_target TEXT DEFAULT '',
                    sandbox_assist_workspace_dir TEXT DEFAULT '',
                    sandbox_assist_command TEXT DEFAULT '',
                    sandbox_assist_validation_command TEXT DEFAULT '',
                    sandbox_assist_test_command TEXT DEFAULT '',
                    context_handoff TEXT DEFAULT '',
                    trigger_mode TEXT DEFAULT 'json',
                    trigger_text TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    last_run_at TEXT,
                    next_run_at TEXT,
                    last_status TEXT DEFAULT 'idle',
                    last_result_excerpt TEXT DEFAULT '',
                    archived_at TEXT,
                    completion_policy_json TEXT DEFAULT '{}',
                    alert_policy_json TEXT DEFAULT '{}',
                    workflow_version INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    source TEXT DEFAULT 'manual',
                    status TEXT DEFAULT 'queued',
                    mode TEXT DEFAULT 'chat',
                    executor_target TEXT DEFAULT '',
                    sandbox_session_id TEXT DEFAULT '',
                    output_excerpt TEXT DEFAULT '',
                    validation_status TEXT DEFAULT '',
                    validation_excerpt TEXT DEFAULT '',
                    test_status TEXT DEFAULT '',
                    test_excerpt TEXT DEFAULT '',
                    error_text TEXT DEFAULT '',
                    alert_id INTEGER,
                    launch_url TEXT DEFAULT '',
                    current_step_id TEXT DEFAULT '',
                    terminal_reason TEXT DEFAULT '',
                    trigger_snapshot_json TEXT DEFAULT '{}',
                    completed_at TEXT,
                    parent_run_id TEXT DEFAULT '',
                    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    run_id TEXT DEFAULT '',
                    alert_id INTEGER,
                    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    title TEXT NOT NULL,
                    trigger_text TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '',
                    acknowledged_at TEXT,
                    updated_at TEXT,
                    resolved_at TEXT,
                    snoozed_until TEXT,
                    severity TEXT DEFAULT 'info',
                    repeat_key TEXT DEFAULT '',
                    closed_by_run_id TEXT DEFAULT '',
                    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_workflow_steps (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    config_json TEXT DEFAULT '{}',
                    on_success_step_id TEXT DEFAULT '',
                    on_failure_step_id TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_step_results (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_name TEXT DEFAULT '',
                    step_kind TEXT DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT DEFAULT 'queued',
                    output_json TEXT DEFAULT '{}',
                    duration_ms INTEGER DEFAULT 0,
                    error_text TEXT DEFAULT '',
                    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_feedback_events (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_id TEXT DEFAULT '',
                    agent_id TEXT NOT NULL,
                    feedback_type TEXT DEFAULT 'result',
                    status TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    summary TEXT DEFAULT '',
                    raw_excerpt TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_templates (
                    key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'chat',
                    schedule_kind TEXT NOT NULL DEFAULT 'manual',
                    interval_minutes INTEGER DEFAULT 0,
                    tabs_required INTEGER DEFAULT 1,
                    executor_target TEXT DEFAULT '',
                    workspace_dir TEXT DEFAULT '',
                    planner_prompt TEXT DEFAULT '',
                    executor_prompt TEXT DEFAULT '',
                    validation_command TEXT DEFAULT '',
                    test_command TEXT DEFAULT '',
                    sandbox_assist INTEGER DEFAULT 0,
                    sandbox_assist_target TEXT DEFAULT '',
                    sandbox_assist_workspace_dir TEXT DEFAULT '',
                    sandbox_assist_command TEXT DEFAULT '',
                    sandbox_assist_validation_command TEXT DEFAULT '',
                    sandbox_assist_test_command TEXT DEFAULT '',
                    context_handoff TEXT DEFAULT '',
                    trigger_mode TEXT DEFAULT 'json',
                    trigger_text TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    source TEXT DEFAULT 'user'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_run_claims (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    claimed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_def_next ON task_definitions(next_run_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_def_due ON task_definitions(active, schedule_kind, next_run_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_runs_status_created ON task_runs(status, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_alerts_task ON task_alerts(task_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_alerts_status ON task_alerts(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_alerts_repeat_key ON task_alerts(repeat_key, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_templates_active ON task_templates(active, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_claims_exp ON task_run_claims(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_steps_task_position ON task_workflow_steps(task_id, position, active)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_step_results_run_started ON task_step_results(run_id, started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_step_results_task_step ON task_step_results(task_id, step_id, started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_feedback_run_created ON task_feedback_events(run_id, created_at DESC)")
    except sqlite3.Error:
        pass
    for statement in (
        "ALTER TABLE task_definitions ADD COLUMN executor_target TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN workspace_dir TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN validation_command TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN test_command TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN sandbox_assist INTEGER DEFAULT 0",
        "ALTER TABLE task_definitions ADD COLUMN sandbox_assist_target TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN sandbox_assist_workspace_dir TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN sandbox_assist_command TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN sandbox_assist_validation_command TEXT DEFAULT ''",
        "ALTER TABLE task_definitions ADD COLUMN sandbox_assist_test_command TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN executor_target TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN sandbox_session_id TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN validation_status TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN validation_excerpt TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN test_status TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN test_excerpt TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN executor_target TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN workspace_dir TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN validation_command TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN test_command TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN sandbox_assist INTEGER DEFAULT 0",
        "ALTER TABLE task_templates ADD COLUMN sandbox_assist_target TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN sandbox_assist_workspace_dir TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN sandbox_assist_command TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN sandbox_assist_validation_command TEXT DEFAULT ''",
        "ALTER TABLE task_templates ADD COLUMN sandbox_assist_test_command TEXT DEFAULT ''",
        "ALTER TABLE task_alerts ADD COLUMN updated_at TEXT",
        "ALTER TABLE task_alerts ADD COLUMN resolved_at TEXT",
        "ALTER TABLE task_alerts ADD COLUMN snoozed_until TEXT",
        "ALTER TABLE task_definitions ADD COLUMN archived_at TEXT",
        "ALTER TABLE task_definitions ADD COLUMN completion_policy_json TEXT DEFAULT '{}'",
        "ALTER TABLE task_definitions ADD COLUMN alert_policy_json TEXT DEFAULT '{}'",
        "ALTER TABLE task_definitions ADD COLUMN workflow_version INTEGER DEFAULT 1",
        "ALTER TABLE task_runs ADD COLUMN current_step_id TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN terminal_reason TEXT DEFAULT ''",
        "ALTER TABLE task_runs ADD COLUMN trigger_snapshot_json TEXT DEFAULT '{}'",
        "ALTER TABLE task_runs ADD COLUMN completed_at TEXT",
        "ALTER TABLE task_runs ADD COLUMN parent_run_id TEXT DEFAULT ''",
        "ALTER TABLE task_alerts ADD COLUMN severity TEXT DEFAULT 'info'",
        "ALTER TABLE task_alerts ADD COLUMN repeat_key TEXT DEFAULT ''",
        "ALTER TABLE task_alerts ADD COLUMN closed_by_run_id TEXT DEFAULT ''",
    ):
        try:
            with sqlite3.connect(DEFAULT_DB) as conn:
                conn.execute(statement)
        except sqlite3.OperationalError:
            pass
        except sqlite3.Error:
            pass
    try:
        _ensure_task_templates_seeded()
    except Exception:
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


def _task_sandbox_target(value: str | None) -> str:
    target = (value or "c12b").strip().lower()
    return "c12b"


def _task_sandbox_workspace(value: str | None, target: str) -> str:
    raw = (value or "").strip()
    if raw:
        return raw
    return TASK_SANDBOX_DEFAULTS.get(target, "/workspace")


def _task_sandbox_assist_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _task_sandbox_assist_values(payload: dict, *, mode: str) -> dict:
    enabled = mode != "sandbox" and _task_sandbox_assist_enabled(payload.get("sandbox_assist"))
    target = _task_sandbox_target(payload.get("sandbox_assist_target") or "c12b") if enabled else ""
    return {
        "sandbox_assist": enabled,
        "sandbox_assist_target": target,
        "sandbox_assist_workspace_dir": _task_sandbox_workspace(payload.get("sandbox_assist_workspace_dir"), target) if enabled else "",
        "sandbox_assist_command": (payload.get("sandbox_assist_command") or "").strip() if enabled else "",
        "sandbox_assist_validation_command": (payload.get("sandbox_assist_validation_command") or "").strip() if enabled else "",
        "sandbox_assist_test_command": (payload.get("sandbox_assist_test_command") or "").strip() if enabled else "",
    }


def _task_c12b_cwd(workspace_dir: str) -> str:
    value = (workspace_dir or "/workspace").strip() or "/workspace"
    if value == "/workspace":
        return "."
    if value.startswith("/workspace/"):
        return value[len("/workspace/"):]
    if value.startswith("/"):
        return "."
    return value


# ── C12b Sandbox helpers (lean host-exposed sandbox) ─────────────────────────

async def _c12b_exec(command: str, timeout: int = 30, cwd: str = ".", session_id: str = "") -> dict:
    client = _get_http()
    try:
        r = await client.post(
            f"{C12B_URL}/exec",
            json={"command": command, "timeout": timeout, "cwd": cwd, "session_id": session_id},
            timeout=timeout + 10,
        )
        return r.json()
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False}


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


async def _probe_session_health(client: httpx.AsyncClient, c3_url: str, timeout: float = 5.0) -> dict:
    full = f"{c3_url}/session-health"
    t0 = time.monotonic()
    try:
        r = await client.get(full, timeout=timeout)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        try:
            parsed = r.json()
        except Exception:
            parsed = {
                "session": "unknown",
                "profile": "unknown",
                "reason": "C3 returned non-JSON body",
            }
        return {
            "name": "C3 /session-health",
            "url": full,
            "ok": r.status_code == 200,
            "http_status": r.status_code,
            "body": parsed,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "name": "C3 /session-health",
            "url": full,
            "ok": False,
            "http_status": None,
            "error": str(exc),
            "elapsed_ms": elapsed_ms,
            "body": {
                "session": "unknown",
                "profile": "unknown",
                "reason": str(exc),
            },
        }


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _short_detail(detail: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(detail or "")).strip()
    return text[:limit]


def _probe_state(probe: dict | None) -> str:
    if not probe or not probe.get("ok"):
        return "down"
    if _safe_int(probe.get("elapsed_ms")) >= RUNTIME_SLOW_MS:
        return "slow"
    return "ok"


def _component_from_probe(label: str, probe: dict | None) -> dict:
    probe = probe or {}
    state = _probe_state(probe)
    detail = probe.get("error") or ""
    http_status = probe.get("http_status")
    elapsed_ms = probe.get("elapsed_ms")
    if state == "ok":
        message = f"{label} reachable"
    elif state == "slow":
        message = f"{label} slow ({elapsed_ms}ms)"
    elif http_status:
        message = f"{label} unavailable (HTTP {http_status})"
    else:
        message = f"{label} unavailable"
        if detail:
            message += f" ({_short_detail(detail, 90)})"
    return {
        "state": state,
        "ok": probe.get("ok") is True,
        "http_status": http_status,
        "elapsed_ms": elapsed_ms,
        "message": message,
    }


def _classify_c3_pool(probe: dict | None) -> dict:
    probe = probe or {}
    body = probe.get("body") if isinstance(probe.get("body"), dict) else {}
    if not probe.get("ok") or not isinstance(body, dict) or body.get("status") == "error":
        return {
            "state": "down",
            "pool_size": 0,
            "pool_available": 0,
            "pool_initialized": False,
            "message": "C3 pool status unavailable",
        }
    pool_size = _safe_int(body.get("pool_size"))
    pool_available = _safe_int(body.get("pool_available"))
    pool_initialized = bool(body.get("pool_initialized"))
    if not pool_initialized:
        state = "warming"
        message = "C3 pool initializing"
    elif pool_size > 0 and pool_available <= 0:
        state = "saturated"
        message = f"C3 browser pool saturated ({pool_available}/{pool_size} tabs free)"
    elif pool_size > 0 and pool_available <= min(POOL_TIGHT_THRESHOLD, pool_size):
        state = "tight"
        message = f"C3 browser pool tight ({pool_available}/{pool_size} tabs free)"
    else:
        state = "ready"
        message = f"C3 browser pool ready ({pool_available}/{pool_size} tabs free)"
    return {
        "state": state,
        "pool_size": pool_size,
        "pool_available": pool_available,
        "pool_initialized": pool_initialized,
        "message": message,
    }


def _build_runtime_status_payload(probes: dict[str, dict], session_probe: dict) -> dict:
    session_body = session_probe.get("body") if isinstance(session_probe.get("body"), dict) else {}
    session_state = (session_body.get("session") or "unknown").strip().lower()
    components = {
        "c1": _component_from_probe("C1 API", probes.get("c1")),
        "c3": _component_from_probe("C3 browser-auth", probes.get("c3")),
        "c10": _component_from_probe("C10 sandbox", probes.get("c10")),
        "c11": _component_from_probe("C11 sandbox", probes.get("c11")),
        "c12b": _component_from_probe("C12b lean sandbox", probes.get("c12b")),
        "c3_pool": _classify_c3_pool(probes.get("c3-status")),
        "m365": {
            "state": session_state,
            "profile": session_body.get("profile") or "unknown",
            "reason": session_body.get("reason"),
            "chat_mode": session_body.get("chat_mode"),
            "http_status": session_probe.get("http_status"),
            "elapsed_ms": session_probe.get("elapsed_ms"),
            "message": (
                "M365 session active"
                if session_state == "active"
                else "M365 session expired"
                if session_state == "expired"
                else "M365 session state unavailable"
            ),
        },
    }
    issues: list[dict] = []

    def add_issue(component: str, code: str, severity: str, message: str) -> None:
        issues.append({
            "component": component,
            "code": code,
            "severity": severity,
            "message": message,
        })

    if components["m365"]["state"] == "expired":
        add_issue("m365", "session_expired", "error", "M365 session expired — sign in again via C3/noVNC")
    elif components["m365"]["state"] == "unknown":
        add_issue("m365", "session_unknown", "warn", "M365 session state unavailable")

    if components["c3"]["state"] == "down":
        add_issue("c3", "c3_down", "error", "C3 browser-auth unavailable")
    elif components["c3"]["state"] == "slow":
        add_issue("c3", "c3_slow", "warn", f"C3 browser-auth slow ({components['c3']['elapsed_ms']}ms)")

    if components["c1"]["state"] == "down":
        add_issue("c1", "c1_down", "error", "C1 API unavailable")
    elif components["c1"]["state"] == "slow":
        add_issue("c1", "c1_slow", "warn", f"C1 API slow ({components['c1']['elapsed_ms']}ms)")

    if components["c10"]["state"] == "down":
        add_issue("c10", "c10_down", "warn", "C10 sandbox unavailable")
    if components["c11"]["state"] == "down":
        add_issue("c11", "c11_down", "warn", "C11 sandbox unavailable")
    if components["c12b"]["state"] == "down":
        add_issue("c12b", "c12b_down", "warn", "C12b lean sandbox unavailable")

    c3_pool = components["c3_pool"]
    if c3_pool["state"] == "saturated":
        add_issue("c3_pool", "pool_saturated", "warn", c3_pool["message"])
    elif c3_pool["state"] == "tight":
        add_issue("c3_pool", "pool_tight", "warn", c3_pool["message"])
    elif c3_pool["state"] == "warming":
        add_issue("c3_pool", "pool_warming", "warn", c3_pool["message"])

    if any(issue["severity"] == "error" for issue in issues):
        level = "error"
    elif issues:
        level = "warn"
    else:
        level = "ok"

    badge_label = "Healthy"
    summary = "Runtime healthy — C1/C3 reachable and M365 session active."
    if issues:
        summary = issues[0]["message"]
        if issues[0]["code"] == "session_expired":
            badge_label = "M365 Expired"
        elif issues[0]["component"] == "c3_pool":
            badge_label = "C3 Pool Busy"
        elif issues[0]["component"] == "c3":
            badge_label = "C3 Degraded"
        elif issues[0]["component"] == "c1":
            badge_label = "C1 Degraded"
        elif issues[0]["component"] == "c10":
            badge_label = "C10 Down"
        elif issues[0]["component"] == "c11":
            badge_label = "C11 Down"
        elif issues[0]["component"] == "c12b":
            badge_label = "C12b Down"
        else:
            badge_label = "Runtime Degraded"
    elif components["m365"]["state"] != "active":
        summary = components["m365"]["message"]
        badge_label = "M365 Unknown"

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "badge_label": badge_label,
        "summary": summary,
        "issues": issues,
        "components": components,
    }


async def _collect_runtime_status(client: httpx.AsyncClient | None = None) -> dict:
    urls = _urls()
    client = client or _get_http()
    keys = ("c1", "c3", "c10", "c11", "c12b")
    health_tasks = [
        _probe_health(client, TARGETS[key]["label"], urls[key], TARGETS[key]["health"])
        for key in keys
    ]
    c3_status_task = _probe_health(client, "C3 /status", urls["c3"], "/status")
    session_task = _probe_session_health(client, urls["c3"])
    health_results = await asyncio.gather(*health_tasks, c3_status_task, session_task)
    probes = {key: result for key, result in zip(keys, health_results[: len(keys)])}
    probes["c3-status"] = health_results[len(keys)]
    session_probe = health_results[len(keys) + 1]
    return _build_runtime_status_payload(probes, session_probe)


async def _get_runtime_status_snapshot(force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    now_mono = time.monotonic()
    cached = _runtime_cache.get("data")
    cached_at = float(_runtime_cache.get("captured_monotonic") or 0.0)
    if not force and cached and (now_mono - cached_at) < RUNTIME_CACHE_TTL_S:
        return cached  # type: ignore[return-value]
    async with _get_runtime_lock():
        cached = _runtime_cache.get("data")
        cached_at = float(_runtime_cache.get("captured_monotonic") or 0.0)
        if not force and cached and (time.monotonic() - cached_at) < RUNTIME_CACHE_TTL_S:
            return cached  # type: ignore[return-value]
        data = await _collect_runtime_status(client=client)
        _runtime_cache["captured_monotonic"] = time.monotonic()
        _runtime_cache["data"] = data
        return data


def _runtime_wait_message(runtime: dict | None) -> str:
    if not runtime:
        return "checking C1/C3 status…"
    issues = runtime.get("issues") or []
    if issues:
        return issues[0].get("message") or runtime.get("summary") or "runtime degraded"
    components = runtime.get("components") or {}
    m365 = (components.get("m365") or {}).get("state")
    if m365 == "active":
        return "C1/C3 reachable; M365 session active"
    return runtime.get("summary") or "runtime steady"


def _is_timeoutish(detail: str) -> bool:
    text = (detail or "").lower()
    return any(token in text for token in ("timeout", "timed out", "readtimeout", "connecttimeout", "pool timeout"))


async def _diagnose_copilot_issue(
    detail: str,
    *,
    client: httpx.AsyncClient | None = None,
    runtime: dict | None = None,
) -> dict:
    runtime = runtime or await _get_runtime_status_snapshot(force=True, client=client)
    components = runtime.get("components") or {}
    c1_state = (components.get("c1") or {}).get("state")
    c3_state = (components.get("c3") or {}).get("state")
    c3_pool_state = (components.get("c3_pool") or {}).get("state")
    m365_state = (components.get("m365") or {}).get("state")
    short = _short_detail(detail)

    if m365_state == "expired":
        code = "m365_session_expired"
        summary = "M365 session expired — sign in again via C3/noVNC"
    elif c3_state == "down":
        code = "c3_unreachable"
        summary = "C3 browser-auth unavailable"
    elif c1_state == "down":
        code = "c1_unreachable"
        summary = "C1 API unavailable"
    elif c3_pool_state == "saturated":
        code = "c3_pool_saturated"
        summary = "C3 browser pool saturated"
    elif _is_timeoutish(short) and m365_state == "active" and c1_state in ("ok", "slow") and c3_state in ("ok", "slow"):
        code = "m365_upstream_timeout"
        summary = "M365 Copilot slow or not responding"
    elif any(p in short.lower() for p in _COPILOT_SERVICE_PHRASES):
        code = "m365_service_error"
        summary = "M365 Copilot returned a service-side error"
    elif runtime.get("summary"):
        code = "runtime_degraded"
        summary = str(runtime.get("summary"))
    else:
        code = "copilot_request_failed"
        summary = "Copilot request failed"

    message = summary
    if short and short.lower() not in summary.lower():
        message = f"{summary} ({short})"
    return {"code": code, "summary": summary, "message": message, "runtime": runtime}


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


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text"):
                text = str(part.get("text", "")).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _chat_prompt(prompt: str, messages: list | None = None) -> str:
    prompt = (prompt or "").strip()
    if prompt:
        return prompt
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _content_to_text(msg.get("content")).strip()
            if text:
                return text
    return ""


def _build_chat_messages(prompt: str, attachments: list | None = None, messages: list | None = None) -> list[dict]:
    attachments = attachments or []
    if messages:
        built: list[dict] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            built.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })
        if attachments and built and built[-1].get("role") == "user":
            content: list[dict] = [{"type": "text", "text": _content_to_text(built[-1].get("content"))}]
            for att in attachments:
                if att.get("file_id"):
                    content.append({
                        "type": "file_ref",
                        "file_id": att["file_id"],
                        "filename": att.get("filename", ""),
                    })
            built = built[:-1] + [{"role": "user", "content": content}]
        return built

    if attachments:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for att in attachments:
            if att.get("file_id"):
                content.append({
                    "type": "file_ref",
                    "file_id": att["file_id"],
                    "filename": att.get("filename", ""),
                })
        return [{"role": "user", "content": content}]

    return [{"role": "user", "content": prompt}]


def _build_chat_body(prompt: str, attachments: list | None = None, messages: list | None = None, stream: bool = False) -> dict:
    return {
        "model": "copilot",
        "messages": _build_chat_messages(prompt, attachments=attachments, messages=messages),
        "stream": stream,
    }


def _build_chat_headers(agent_id: str, chat_mode: str = "", work_mode: str = "") -> dict:
    headers = {"Content-Type": "application/json", "X-Agent-ID": agent_id}
    if chat_mode:
        headers["X-Chat-Mode"] = chat_mode
    if work_mode in ("work", "web"):
        headers["X-Work-Mode"] = work_mode
    return headers


def _error_text(payload) -> str:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
        except Exception:
            return text
    if isinstance(payload, dict):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if value:
                return _error_text(value)
        return json.dumps(payload)[:2000]
    return str(payload)


def _persist_chat_turn(
    session_id: str,
    agent_id: str,
    prompt: str,
    response_text: str,
    now: str,
    *,
    messages: list | None = None,
    http_status: int | None = None,
    elapsed_ms: int | None = None,
    source: str = "chat",
) -> int:
    token_est = _estimate_tokens(messages) if messages else (len(prompt) + len(response_text)) // 4
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id, created_at, updated_at, agent_id, title) VALUES (?,?,?,?,?)",
            (session_id, now, now, agent_id, (prompt or "Chat")[:80]),
        )
        turn_row = conn.execute(
            "SELECT MAX(turn) FROM chat_messages WHERE session_id=?", (session_id,)
        ).fetchone()
        next_turn = (turn_row[0] or 0) + 1
        conn.execute(
            "INSERT INTO chat_messages (session_id, turn, role, content, created_at) VALUES (?,?,?,?,?)",
            (session_id, next_turn, "user", prompt[:4000], now),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_id, turn, role, content, created_at) VALUES (?,?,?,?,?)",
            (session_id, next_turn, "assistant", response_text[:4000], now),
        )
        conn.execute(
            "UPDATE chat_sessions SET updated_at=?, message_count=message_count+2, token_estimate=? WHERE id=?",
            (now, token_est, session_id),
        )
        conn.execute(
            "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source, session_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (now, agent_id, prompt[:200], response_text[:500], http_status, elapsed_ms, source, session_id),
        )
    return token_est


def _log_chat_failure(
    session_id: str,
    agent_id: str,
    prompt: str,
    error_text: str,
    now: str,
    *,
    http_status: int | None = None,
    elapsed_ms: int | None = None,
    source: str = "chat",
) -> None:
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source, session_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (now, agent_id, prompt[:200], error_text[:500], http_status, elapsed_ms, source, session_id),
            )
    except sqlite3.Error:
        pass


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _post_with_heartbeats(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict,
    body: dict,
    request_timeout: float,
    heartbeat_every: float = WAIT_HEARTBEAT_S,
):
    task = asyncio.create_task(client.post(url, headers=headers, json=body, timeout=request_timeout))
    waited_s = 0
    while True:
        try:
            response = await asyncio.wait_for(asyncio.shield(task), timeout=heartbeat_every)
            yield {"kind": "response", "response": response, "waited_s": waited_s}
            return
        except asyncio.TimeoutError:
            waited_s += int(heartbeat_every)
            runtime = None
            try:
                runtime = await _get_runtime_status_snapshot(client=client)
            except Exception:
                runtime = None
            yield {"kind": "heartbeat", "waited_s": waited_s, "runtime": runtime}


async def _chat_one(agent_id: str, prompt: str, c1_url: str, chat_mode: str = "", attachments: list | None = None, work_mode: str = "", messages: list | None = None) -> dict:
    """Call C1 for a single agent. Returns {ok, http_status, text, elapsed_ms}.
    If `messages` is provided it is used as the full conversation history (multi-turn).
    Otherwise falls back to single-turn prompt.
    """
    body = _build_chat_body(prompt, attachments=attachments, messages=messages, stream=False)
    headers = _build_chat_headers(agent_id, chat_mode=chat_mode, work_mode=work_mode)
    client = _get_http()
    t0 = time.monotonic()
    try:
        r = await client.post(
            f"{c1_url}/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=360,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        text = ""
        error = None
        diagnosis = None
        if 200 <= r.status_code < 300:
            try:
                d = r.json()
                text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception:
                text = r.text[:2000]
            if not text.strip():
                diagnosis = await _diagnose_copilot_issue("empty response from Copilot", client=client)
                error = diagnosis["message"]
            elif any(p in text.lower() for p in _COPILOT_SERVICE_PHRASES):
                diagnosis = await _diagnose_copilot_issue(text, client=client)
                error = diagnosis["message"]
        else:
            raw_error = _error_text(r.text[:2000])
            diagnosis = await _diagnose_copilot_issue(raw_error or f"HTTP {r.status_code}", client=client)
            error = diagnosis["message"]
        return {
            "ok": 200 <= r.status_code < 300 and not error,
            "http_status": r.status_code,
            "text": text,
            "error": error,
            "elapsed_ms": elapsed_ms,
            "diagnosis": diagnosis,
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        diagnosis = await _diagnose_copilot_issue(str(e), client=client)
        return {
            "ok": False,
            "http_status": None,
            "text": "",
            "error": diagnosis["message"],
            "elapsed_ms": elapsed_ms,
            "diagnosis": diagnosis,
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _duration_ms(start: str | None, end: str | None = None) -> int | None:
    start_dt = _parse_iso_ts(start)
    if not start_dt:
        return None
    end_dt = _parse_iso_ts(end) or datetime.now(timezone.utc)
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def _duration_label(ms: int | None) -> str:
    if ms is None:
        return "—"
    total_seconds = max(0, int(ms / 1000))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _slugify(value: str, *, prefix: str = "item") -> str:
    raw = re.sub(r"[^a-z0-9_\-]", "-", (value or "").strip().lower())
    raw = re.sub(r"-{2,}", "-", raw).strip("-_")
    return raw or f"{prefix}-{uuid.uuid4().hex[:6]}"


def _task_next_run_at(schedule_kind: str, interval_minutes: int, *, base: datetime | None = None) -> str | None:
    if schedule_kind not in {"recurring", "continuous"} or interval_minutes <= 0:
        return None
    base = base or datetime.now(timezone.utc)
    return (base + timedelta(minutes=interval_minutes)).isoformat()


def _task_schedule_label(schedule_kind: str, interval_minutes: int, active: bool) -> str:
    if schedule_kind == "continuous":
        return f"Live / every {interval_minutes}m" + ("" if active else " / paused")
    if schedule_kind == "recurring":
        return f"Repeating / every {interval_minutes}m" + ("" if active else " / paused")
    return "Once-off / manual"


def _json_load_object(raw: object, default: dict | None = None) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return dict(default or {})


def _json_load_list(raw: object) -> list:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def _task_default_alert_policy() -> dict:
    return {
        "repeat_every_minutes": 0,
        "dedupe_key_template": "",
        "severity": "info",
        "while_condition_true": False,
    }


def _task_default_completion_policy() -> dict:
    return {
        "mark_completed_on": "step-complete",
        "archive_on_complete": False,
        "terminal_statuses": ["completed", "failed", "cancelled"],
    }


def _task_step_kinds() -> set[str]:
    return {item["id"] for item in TASK_WORKFLOW_STEP_KINDS}


def _task_steps_fetch(task_id: str) -> list[dict]:
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM task_workflow_steps WHERE task_id=? AND active=1 ORDER BY position ASC, created_at ASC",
                (task_id,),
            ).fetchall()
        return [_task_step_to_dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _task_step_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["position"] = int(raw.get("position") or 1)
    raw["active"] = bool(raw.get("active"))
    raw["config"] = _json_load_object(raw.get("config_json"))
    raw["config_json"] = json.dumps(raw["config"], ensure_ascii=False)
    raw["name"] = raw.get("name") or f"Step {raw['position']}"
    raw["kind_label"] = next((item["label"] for item in TASK_WORKFLOW_STEP_KINDS if item["id"] == raw.get("kind")), raw.get("kind") or "Step")
    return raw


def _task_step_result_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["output"] = _json_load_object(raw.get("output_json"))
    raw["duration_ms"] = int(raw.get("duration_ms") or 0)
    raw["duration_label"] = _duration_label(raw["duration_ms"])
    raw["finished"] = bool(raw.get("finished_at"))
    return raw


def _task_feedback_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["payload"] = _json_load_object(raw.get("payload_json"))
    return raw


def _task_build_default_steps(task: dict) -> list[dict]:
    task_id = str(task.get("id") or "")
    base_name = task.get("name") or "Task"
    mode = (task.get("mode") or "chat").strip().lower()
    steps: list[dict] = []
    if task.get("schedule_kind") in {"recurring", "continuous"}:
        steps.append({
            "id": f"{task_id}_trigger",
            "task_id": task_id,
            "position": len(steps) + 1,
            "name": "Trigger",
            "kind": "trigger",
            "config": {
                "schedule_kind": task.get("schedule_kind") or "manual",
                "interval_minutes": int(task.get("interval_minutes") or 0),
            },
            "active": True,
            "on_success_step_id": "",
            "on_failure_step_id": "",
        })
    if mode == "sandbox":
        steps.append({
            "id": f"{task_id}_sandbox",
            "task_id": task_id,
            "position": len(steps) + 1,
            "name": f"{base_name} sandbox",
            "kind": "sandbox",
            "config": {
                "executor_target": task.get("executor_target") or "c12b",
                "workspace_dir": task.get("workspace_dir") or "/workspace",
                "command": task.get("executor_prompt") or "",
                "validation_command": task.get("validation_command") or "",
                "test_command": task.get("test_command") or "",
            },
            "active": True,
            "on_success_step_id": "",
            "on_failure_step_id": "",
        })
    else:
        steps.append({
            "id": f"{task_id}_{mode or 'chat'}",
            "task_id": task_id,
            "position": len(steps) + 1,
            "name": f"{base_name} {mode or 'chat'}",
            "kind": mode or "chat",
            "config": {
                "prompt": task.get("executor_prompt") or task.get("planner_prompt") or "",
                "agent_id": "c6-kilocode",
                "sandbox_assist": bool(task.get("sandbox_assist")),
                "sandbox_assist_target": task.get("sandbox_assist_target") or "c12b",
                "sandbox_assist_workspace_dir": task.get("sandbox_assist_workspace_dir") or "/workspace",
                "sandbox_assist_command": task.get("sandbox_assist_command") or "",
                "sandbox_assist_validation_command": task.get("sandbox_assist_validation_command") or "",
                "sandbox_assist_test_command": task.get("sandbox_assist_test_command") or "",
            },
            "active": True,
            "on_success_step_id": "",
            "on_failure_step_id": "",
        })
    steps.append({
        "id": f"{task_id}_alert",
        "task_id": task_id,
        "position": len(steps) + 1,
        "name": "Alert",
        "kind": "alert",
        "config": {
            "trigger_mode": task.get("trigger_mode") or "json",
            "trigger_text": task.get("trigger_text") or "",
        },
        "active": True,
        "on_success_step_id": "",
        "on_failure_step_id": "",
    })
    steps.append({
        "id": f"{task_id}_complete",
        "task_id": task_id,
        "position": len(steps) + 1,
        "name": "Complete",
        "kind": "complete",
        "config": {},
        "active": True,
        "on_success_step_id": "",
        "on_failure_step_id": "",
    })
    return steps


def _task_normalize_step(task_id: str, step: dict, position: int) -> dict:
    kind = (step.get("kind") or "chat").strip().lower()
    if kind not in _task_step_kinds():
        raise ValueError(f"invalid step kind: {kind}")
    step_id = (step.get("id") or f"{task_id}_step_{position}").strip()
    cfg = _json_load_object(step.get("config"))
    return {
        "id": step_id,
        "task_id": task_id,
        "position": position,
        "name": (step.get("name") or f"Step {position}").strip(),
        "kind": kind,
        "config": cfg,
        "config_json": json.dumps(cfg, ensure_ascii=False),
        "on_success_step_id": (step.get("on_success_step_id") or "").strip(),
        "on_failure_step_id": (step.get("on_failure_step_id") or "").strip(),
        "active": 1 if step.get("active", True) else 0,
    }


def _task_save_steps(conn: sqlite3.Connection, task_id: str, steps: list[dict]) -> list[dict]:
    normalized = [_task_normalize_step(task_id, step, idx + 1) for idx, step in enumerate(steps) if step]
    conn.execute("DELETE FROM task_workflow_steps WHERE task_id=?", (task_id,))
    now = _iso_now()
    for item in normalized:
        conn.execute(
            "INSERT INTO task_workflow_steps (id, task_id, position, name, kind, config_json, on_success_step_id, on_failure_step_id, active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["id"], task_id, item["position"], item["name"], item["kind"], item["config_json"],
                item["on_success_step_id"], item["on_failure_step_id"], item["active"], now, now,
            ),
        )
    return normalized


def _task_rewrite_step_refs(value, mapping: dict[str, str]):
    if isinstance(value, dict):
        return {k: _task_rewrite_step_refs(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_task_rewrite_step_refs(item, mapping) for item in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def _task_clone_steps(task_id: str, steps: list[dict]) -> list[dict]:
    normalized = [_task_normalize_step(task_id, step, idx + 1) for idx, step in enumerate(steps) if step]
    mapping: dict[str, str] = {}
    cloned: list[dict] = []
    for idx, step in enumerate(normalized, start=1):
        old_id = step.get("id") or f"{task_id}_step_{idx}"
        new_id = f"{task_id}_step_{idx}"
        mapping[old_id] = new_id
        cloned.append({**step, "id": new_id, "task_id": task_id})
    for step in cloned:
        on_success = step.get("on_success_step_id") or ""
        on_failure = step.get("on_failure_step_id") or ""
        step["on_success_step_id"] = mapping.get(on_success, "")
        step["on_failure_step_id"] = mapping.get(on_failure, "")
        rewritten_cfg = _task_rewrite_step_refs(step.get("config") or {}, mapping)
        step["config"] = rewritten_cfg
        step["config_json"] = json.dumps(rewritten_cfg, ensure_ascii=False)
    return cloned


def _task_fetch_step_results(run_id: str) -> list[dict]:
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM task_step_results WHERE run_id=? ORDER BY started_at ASC, id ASC",
                (run_id,),
            ).fetchall()
        return [_task_step_result_to_dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _task_fetch_feedback(run_id: str) -> list[dict]:
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM task_feedback_events WHERE run_id=? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [_task_feedback_to_dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _task_insert_step_result(task_id: str, run_id: str, step: dict, *, status: str = "running", output: dict | None = None, error_text: str = "") -> str:
    result_id = "tsr_" + uuid.uuid4().hex[:10]
    now = _iso_now()
    with _db() as conn:
        conn.execute(
            "INSERT INTO task_step_results (id, run_id, task_id, step_id, step_name, step_kind, started_at, status, output_json, error_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                result_id, run_id, task_id, step.get("id") or "", step.get("name") or "", step.get("kind") or "",
                now, status, json.dumps(output or {}, ensure_ascii=False), error_text[:1500],
            ),
        )
    return result_id


def _task_finish_step_result(result_id: str, *, status: str, output: dict | None = None, error_text: str = "", finished_at: str | None = None) -> None:
    finished_at = finished_at or _iso_now()
    try:
        with _db() as conn:
            row = conn.execute("SELECT started_at FROM task_step_results WHERE id=?", (result_id,)).fetchone()
            started_at = row["started_at"] if row else finished_at
            conn.execute(
                "UPDATE task_step_results SET finished_at=?, status=?, output_json=?, duration_ms=?, error_text=? WHERE id=?",
                (
                    finished_at,
                    status,
                    json.dumps(output or {}, ensure_ascii=False),
                    _duration_ms(started_at, finished_at) or 0,
                    error_text[:1500],
                    result_id,
                ),
            )
    except sqlite3.Error:
        return


def _task_lifecycle_state(task: dict) -> str:
    if (task.get("archived_at") or "").strip():
        return "archived"
    schedule_kind = (task.get("schedule_kind") or "manual").strip().lower()
    active = bool(task.get("active"))
    last_status = (task.get("last_status") or "idle").strip().lower()
    if last_status == "running" or str(task.get("id") or "") in _task_runner_ids:
        return "running"
    if last_status in {"launch-required", "manual-only", "launch-pending"}:
        return "launch-pending"
    if last_status in {"waiting-user", "cancelled"}:
        return last_status
    if not active and schedule_kind in {"recurring", "continuous"}:
        return "paused"
    if schedule_kind == "continuous":
        return "live"
    if schedule_kind == "recurring":
        return "scheduled" if task.get("next_run_at") else "repeating"
    if last_status in {"completed", "failed", "launch-required", "manual-only"}:
        return last_status
    if not (task.get("last_run_at") or "").strip():
        return "draft"
    return "once-off"


def _task_template_row_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["active"] = bool(raw.get("active"))
    raw["interval_minutes"] = int(raw.get("interval_minutes") or 0)
    raw["tabs_required"] = int(raw.get("tabs_required") or 1)
    raw["executor_target"] = _task_sandbox_target(raw.get("executor_target")) if (raw.get("mode") == "sandbox" or raw.get("executor_target")) else ""
    raw["workspace_dir"] = _task_sandbox_workspace(raw.get("workspace_dir"), raw["executor_target"] or "c12b") if raw["executor_target"] else ""
    raw["validation_command"] = raw.get("validation_command") or ""
    raw["test_command"] = raw.get("test_command") or ""
    raw["sandbox_assist"] = _task_sandbox_assist_enabled(raw.get("sandbox_assist"))
    raw["sandbox_assist_target"] = _task_sandbox_target(raw.get("sandbox_assist_target")) if raw["sandbox_assist"] else ""
    raw["sandbox_assist_workspace_dir"] = _task_sandbox_workspace(raw.get("sandbox_assist_workspace_dir"), raw["sandbox_assist_target"] or "c12b") if raw["sandbox_assist"] else ""
    raw["sandbox_assist_command"] = raw.get("sandbox_assist_command") or ""
    raw["sandbox_assist_validation_command"] = raw.get("sandbox_assist_validation_command") or ""
    raw["sandbox_assist_test_command"] = raw.get("sandbox_assist_test_command") or ""
    raw["source"] = raw.get("source") or "user"
    return raw


def _ensure_task_templates_seeded() -> None:
    now = _iso_now()
    with _db() as conn:
        for tpl in TASK_TEMPLATES:
            assist = _task_sandbox_assist_values(tpl, mode=tpl.get("mode") or "chat")
            conn.execute(
                "INSERT OR IGNORE INTO task_templates (key, created_at, updated_at, name, description, mode, schedule_kind, interval_minutes, "
                "tabs_required, executor_target, workspace_dir, planner_prompt, executor_prompt, validation_command, test_command, "
                "sandbox_assist, sandbox_assist_target, sandbox_assist_workspace_dir, sandbox_assist_command, "
                "sandbox_assist_validation_command, sandbox_assist_test_command, context_handoff, trigger_mode, trigger_text, active, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tpl["key"],
                    now,
                    now,
                    tpl["name"],
                    tpl.get("description") or "",
                    tpl.get("mode") or "chat",
                    tpl.get("schedule_kind") or "manual",
                    int(tpl.get("interval_minutes") or 0),
                    int(tpl.get("tabs_required") or 1),
                    _task_sandbox_target(tpl.get("executor_target") or "c12b"),
                    _task_sandbox_workspace(tpl.get("workspace_dir"), _task_sandbox_target(tpl.get("executor_target") or "c12b")),
                    tpl.get("planner_prompt") or "",
                    tpl.get("executor_prompt") or "",
                    tpl.get("validation_command") or "",
                    tpl.get("test_command") or "",
                    1 if assist["sandbox_assist"] else 0,
                    assist["sandbox_assist_target"],
                    assist["sandbox_assist_workspace_dir"],
                    assist["sandbox_assist_command"],
                    assist["sandbox_assist_validation_command"],
                    assist["sandbox_assist_test_command"],
                    tpl.get("context_handoff") or "",
                    tpl.get("trigger_mode") or "json",
                    tpl.get("trigger_text") or "",
                    1,
                    "builtin",
                ),
            )


def _task_templates_payload(active_only: bool = True) -> list[dict]:
    try:
        with _db() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM task_templates WHERE active=1 ORDER BY CASE source WHEN 'builtin' THEN 0 ELSE 1 END, name ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM task_templates ORDER BY active DESC, CASE source WHEN 'builtin' THEN 0 ELSE 1 END, name ASC"
                ).fetchall()
        return [_task_template_row_to_dict(row) for row in rows]
    except sqlite3.Error:
        return [dict(item) | {"active": True, "source": "builtin"} for item in TASK_TEMPLATES]


def _task_run_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["executor_target"] = _task_sandbox_target(raw.get("executor_target")) if raw.get("executor_target") else ""
    raw["sandbox_session_id"] = raw.get("sandbox_session_id") or ""
    raw["validation_status"] = raw.get("validation_status") or ""
    raw["validation_excerpt"] = raw.get("validation_excerpt") or ""
    raw["test_status"] = raw.get("test_status") or ""
    raw["test_excerpt"] = raw.get("test_excerpt") or ""
    raw["trace_id"] = raw.get("task_id") or ""
    raw["task_url"] = f"/tasked?task_id={quote(str(raw.get('task_id') or ''))}" if raw.get("task_id") else "/tasked"
    raw["pipeline_url"] = f"/piplinetask?task_id={quote(str(raw.get('task_id') or ''))}" if raw.get("task_id") else "/piplinetask"
    raw["completed_url"] = f"/task-completed?task_id={quote(str(raw.get('task_id') or ''))}" if raw.get("task_id") else "/task-completed"
    raw["is_running"] = (raw.get("status") or "").lower() == "running" and not raw.get("finished_at")
    raw["duration_ms"] = _duration_ms(raw.get("started_at") or raw.get("created_at"), raw.get("finished_at"))
    raw["duration_label"] = _duration_label(raw.get("duration_ms"))
    raw["current_step_id"] = raw.get("current_step_id") or ""
    raw["terminal_reason"] = raw.get("terminal_reason") or ""
    raw["trigger_snapshot"] = _json_load_object(raw.get("trigger_snapshot_json"))
    raw["completed_at"] = raw.get("completed_at") or raw.get("finished_at") or ""
    raw["parent_run_id"] = raw.get("parent_run_id") or ""
    return raw


def _task_alert_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["interval_minutes"] = int(raw.get("interval_minutes") or 0)
    raw["tabs_required"] = int(raw.get("tabs_required") or 0)
    raw["active"] = bool(raw.get("active"))
    raw["executor_target"] = _task_sandbox_target(raw.get("executor_target")) if raw.get("executor_target") else ""
    raw["workspace_dir"] = raw.get("workspace_dir") or (TASK_SANDBOX_DEFAULTS.get(raw["executor_target"], "") if raw["executor_target"] else "")
    raw["sandbox_assist"] = _task_sandbox_assist_enabled(raw.get("sandbox_assist"))
    raw["sandbox_assist_target"] = _task_sandbox_target(raw.get("sandbox_assist_target")) if raw["sandbox_assist"] else ""
    raw["sandbox_assist_workspace_dir"] = _task_sandbox_workspace(raw.get("sandbox_assist_workspace_dir"), raw["sandbox_assist_target"] or "c12b") if raw["sandbox_assist"] else ""
    raw["updated_at"] = raw.get("updated_at") or raw.get("created_at")
    raw["resolved_at"] = raw.get("resolved_at") or ""
    raw["snoozed_until"] = raw.get("snoozed_until") or ""
    raw["trace_id"] = raw.get("task_id") or ""
    raw["task_url"] = f"/tasked?task_id={quote(str(raw.get('task_id') or ''))}" if raw.get("task_id") else "/tasked"
    raw["pipeline_url"] = f"/piplinetask?task_id={quote(str(raw.get('task_id') or ''))}" if raw.get("task_id") else "/piplinetask"
    raw["completed_url"] = f"/task-completed?task_id={quote(str(raw.get('task_id') or ''))}" if raw.get("task_id") else "/task-completed"
    raw["schedule_label"] = _task_schedule_label(raw.get("schedule_kind") or "manual", raw.get("interval_minutes") or 0, raw.get("active"))
    raw["severity"] = raw.get("severity") or "info"
    raw["repeat_key"] = raw.get("repeat_key") or ""
    raw["closed_by_run_id"] = raw.get("closed_by_run_id") or ""
    return raw


def _task_trace_payload(task: dict, latest_run: dict | None = None, latest_alert: dict | None = None) -> dict:
    return {
        "trace_id": task.get("id") or "",
        "task_id": task.get("id") or "",
        "run_id": (latest_run or {}).get("id") or "",
        "alert_id": (latest_alert or {}).get("id") or "",
        "orchestration": {
            "mode": task.get("mode") or "",
            "task_url": task.get("task_url") or "/tasked",
            "pipeline_url": task.get("pipeline_url") or "/piplinetask",
            "completed_url": task.get("completed_url") or "/task-completed",
            "executor_target": task.get("executor_target") or "",
            "sandbox_assist": bool(task.get("sandbox_assist")),
        },
        "planner": {
            "prompt": task.get("planner_prompt") or "",
            "context_handoff": task.get("context_handoff") or "",
        },
        "timer": {
            "schedule_kind": task.get("schedule_kind") or "manual",
            "schedule_label": task.get("schedule_label") or "",
            "interval_minutes": task.get("interval_minutes") or 0,
            "active": bool(task.get("active")),
            "next_run_at": task.get("next_run_at") or "",
        },
        "executor": {
            "prompt": task.get("executor_prompt") or "",
            "target": task.get("executor_target") or "",
            "workspace_dir": task.get("workspace_dir") or "",
            "validation_command": task.get("validation_command") or "",
            "test_command": task.get("test_command") or "",
            "last_status": (latest_run or {}).get("status") or task.get("last_status") or "idle",
            "duration_label": (latest_run or {}).get("duration_label") or "—",
            "validation_status": (latest_run or {}).get("validation_status") or "",
            "test_status": (latest_run or {}).get("test_status") or "",
            "sandbox_session_id": (latest_run or {}).get("sandbox_session_id") or "",
        },
        "sandbox_assist": {
            "enabled": bool(task.get("sandbox_assist")),
            "target": task.get("sandbox_assist_target") or "",
            "workspace_dir": task.get("sandbox_assist_workspace_dir") or "",
            "command": task.get("sandbox_assist_command") or "",
            "validation_command": task.get("sandbox_assist_validation_command") or "",
            "test_command": task.get("sandbox_assist_test_command") or "",
            "last_status": (latest_run or {}).get("status") or "",
            "sandbox_session_id": (latest_run or {}).get("sandbox_session_id") or "",
            "validation_status": (latest_run or {}).get("validation_status") or "",
            "test_status": (latest_run or {}).get("test_status") or "",
        },
        "alert_generator": {
            "trigger_mode": task.get("trigger_mode") or "json",
            "trigger_text": task.get("trigger_text") or "",
            "latest_alert_status": (latest_alert or {}).get("status") or "",
            "latest_alert_title": (latest_alert or {}).get("title") or "",
            "severity": (latest_alert or {}).get("severity") or task.get("alert_policy", {}).get("severity") or "info",
        },
        "completion": {
            "last_status": (latest_run or {}).get("status") or task.get("last_status") or "idle",
            "terminal_reason": (latest_run or {}).get("terminal_reason") or "",
            "completed_at": (latest_run or {}).get("completed_at") or "",
            "current_step_id": (latest_run or {}).get("current_step_id") or "",
        },
    }


def _task_launch_url(mode: str, prompt: str, *, task_id: str = "", run_id: str = "", extra_params: dict | None = None) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return "/tasked"
    path = {
        "chat": "/chat",
        "agent": "/agent",
        "multi-agent": "/multi-agent",
        "multi-agento": "/multi-Agento",
    }.get(mode, "/tasked")
    params = {"task": prompt}
    if task_id:
        params["task_id"] = task_id
    if run_id:
        params["task_run_id"] = run_id
    params["source"] = "tasked"
    for key, value in (extra_params or {}).items():
        if value not in {None, ""}:
            params[str(key)] = str(value)
    return f"{path}?{urlencode(params)}"


def _task_row_to_dict(row: sqlite3.Row | dict) -> dict:
    raw = dict(row)
    raw["active"] = bool(raw.get("active"))
    raw["interval_minutes"] = int(raw.get("interval_minutes") or 0)
    raw["tabs_required"] = int(raw.get("tabs_required") or 1)
    raw["executor_target"] = _task_sandbox_target(raw.get("executor_target")) if (raw.get("mode") == "sandbox" or raw.get("executor_target")) else ""
    raw["workspace_dir"] = _task_sandbox_workspace(raw.get("workspace_dir"), raw["executor_target"] or "c12b") if raw["executor_target"] else ""
    raw["validation_command"] = raw.get("validation_command") or ""
    raw["test_command"] = raw.get("test_command") or ""
    raw["sandbox_assist"] = _task_sandbox_assist_enabled(raw.get("sandbox_assist"))
    raw["sandbox_assist_target"] = _task_sandbox_target(raw.get("sandbox_assist_target")) if raw["sandbox_assist"] else ""
    raw["sandbox_assist_workspace_dir"] = _task_sandbox_workspace(raw.get("sandbox_assist_workspace_dir"), raw["sandbox_assist_target"] or "c12b") if raw["sandbox_assist"] else ""
    raw["sandbox_assist_command"] = raw.get("sandbox_assist_command") or ""
    raw["sandbox_assist_validation_command"] = raw.get("sandbox_assist_validation_command") or ""
    raw["sandbox_assist_test_command"] = raw.get("sandbox_assist_test_command") or ""
    raw["archived_at"] = raw.get("archived_at") or ""
    raw["completion_policy"] = _json_load_object(raw.get("completion_policy_json"), _task_default_completion_policy())
    raw["alert_policy"] = _json_load_object(raw.get("alert_policy_json"), _task_default_alert_policy())
    raw["workflow_version"] = int(raw.get("workflow_version") or 1)
    raw["background_supported"] = raw.get("mode") in {"chat", "sandbox"}
    raw["launch_url"] = "" if raw.get("mode") == "sandbox" else _task_launch_url(
        raw.get("mode") or "chat",
        raw.get("executor_prompt") or raw.get("planner_prompt") or "",
        task_id=str(raw.get("id") or ""),
    )
    raw["task_url"] = f"/tasked?task_id={quote(str(raw.get('id') or ''))}" if raw.get("id") else "/tasked"
    raw["pipeline_url"] = f"/piplinetask?task_id={quote(str(raw.get('id') or ''))}" if raw.get("id") else "/piplinetask"
    raw["completed_url"] = f"/task-completed?task_id={quote(str(raw.get('id') or ''))}" if raw.get("id") else "/task-completed"
    raw["alerts_url"] = "/alerts"
    raw["trace_id"] = raw.get("id") or ""
    raw["schedule_label"] = _task_schedule_label(raw.get("schedule_kind") or "manual", raw.get("interval_minutes") or 0, raw.get("active"))
    raw["lifecycle_state"] = _task_lifecycle_state(raw)
    raw["lifecycle_label"] = raw["lifecycle_state"].replace("-", " ").title()
    raw["mode_label"] = _task_mode_label(raw.get("mode") or "")
    raw["template_label"] = _task_template_label(raw.get("template_key") or "")
    return raw


def _task_mode_label(mode: str) -> str:
    return next((item["label"] for item in TASK_MODE_OPTIONS if item["id"] == mode), mode or "Unknown")


def _task_executor_target_label(target: str) -> str:
    return next((item["label"] for item in TASK_EXECUTOR_TARGET_OPTIONS if item["id"] == target), target or "Unknown")


def _task_template_label(template_key: str) -> str:
    return next((item["name"] for item in _task_templates_payload(active_only=False) if item["key"] == template_key), template_key or "custom")


def _task_pipeline_build(
    task_row: dict,
    runs: list[sqlite3.Row | dict],
    alerts: list[sqlite3.Row | dict],
    task_events: list[sqlite3.Row | dict] | None = None,
    step_results: list[sqlite3.Row | dict] | None = None,
    feedback_events: list[sqlite3.Row | dict] | None = None,
    selected_run_id: str = "",
) -> dict:
    task = _task_row_to_dict(task_row)
    task["mode_label"] = _task_mode_label(task.get("mode") or "")
    task["template_label"] = _task_template_label(task.get("template_key") or "")
    task["task_url"] = f"/tasked?task_id={quote(str(task.get('id') or ''))}" if task.get("id") else "/tasked"
    task["alerts_url"] = "/alerts"
    task["pipeline_url"] = f"/piplinetask?task_id={quote(str(task.get('id') or ''))}" if task.get("id") else "/piplinetask"
    task["completed_url"] = f"/task-completed?task_id={quote(str(task.get('id') or ''))}" if task.get("id") else "/task-completed"

    run_items = [_task_run_to_dict(r) for r in runs]
    selected_run = next((item for item in run_items if item.get("id") == selected_run_id), None) if selected_run_id else None
    selected_run = selected_run or (run_items[-1] if run_items else None)
    run_id = str((selected_run or {}).get("id") or "")
    alert_items = [
        _task_alert_to_dict(a) for a in alerts
        if not run_id or str(dict(a).get("run_id") or "") == run_id
    ]
    if not alert_items:
        alert_items = [_task_alert_to_dict(a) for a in alerts]
    latest_alert = alert_items[-1] if alert_items else None
    step_items = [_task_step_result_to_dict(r) for r in (step_results or [])]
    feedback_items = [_task_feedback_to_dict(r) for r in (feedback_events or [])]
    task["current_step_id"] = (selected_run or {}).get("current_step_id") or ""

    events: list[dict] = []

    def add_event(ts: str | None, kind: str, title: str, detail: str, status: str = "", level: str = "info", **extra):
        if not ts:
            return
        item = {
            "ts": ts,
            "kind": kind,
            "title": title,
            "detail": detail[:1500] if detail else "",
            "status": status,
            "level": level,
        }
        item.update(extra)
        events.append(item)

    add_event(
        task.get("created_at"),
        "task-created",
        "Tasked created",
        f"{task.get('name') or 'Tasked'} created in {task.get('mode_label') or task.get('mode')}.",
        status=task.get("lifecycle_state") or task.get("last_status") or "idle",
    )
    if task.get("updated_at") and task.get("updated_at") != task.get("created_at"):
        add_event(
            task.get("updated_at"),
            "task-updated",
            "Tasked updated",
            f"Definition updated. Schedule: {task.get('schedule_label')}. Tabs: {task.get('tabs_required')}.",
            status=task.get("lifecycle_state") or task.get("last_status") or "idle",
        )
    if task.get("schedule_kind") in {"recurring", "continuous"} and task.get("next_run_at"):
        add_event(
            task.get("next_run_at"),
            "task-scheduled",
            "Next run scheduled",
            f"{task.get('schedule_label')} task is scheduled for the next run. Active={task.get('active')}.",
            status="scheduled",
        )
    if task.get("archived_at"):
        add_event(
            task.get("archived_at"),
            "task-archived",
            "Task archived",
            f"{task.get('name') or 'Tasked'} was archived and removed from active schedules.",
            status="archived",
            level="warn",
        )

    filtered_task_events = []
    for raw_event in (task_events or []):
        event = dict(raw_event)
        event_run_id = str(event.get("run_id") or "")
        if run_id and event_run_id and event_run_id != run_id:
            continue
        filtered_task_events.append(event)
    for event in sorted(filtered_task_events, key=lambda item: item.get("created_at") or ""):
        add_event(
            event.get("created_at"),
            event.get("event_type") or "task-event",
            (event.get("event_type") or "task-event").replace("-", " ").title(),
            event.get("detail") or "Task event recorded.",
            status=event.get("status") or "",
            level="warn" if (event.get("status") or "") in {"paused", "launch-required", "manual-only", "launch-pending", "waiting-feedback"} else "info",
            run_id=event.get("run_id") or "",
            alert_id=event.get("alert_id"),
        )

    if selected_run:
        run_status = selected_run.get("status") or "queued"
        add_event(
            selected_run.get("started_at") or selected_run.get("created_at"),
            "run-started",
            "Task run started",
            f"Source={selected_run.get('source') or 'manual'} · Mode={_task_mode_label(selected_run.get('mode') or '')} · Duration={selected_run.get('duration_label') or '—'}",
            status=run_status,
            level="warn" if run_status in {"launch-required", "manual-only", "launch-pending", "waiting-feedback"} else "info",
            run_id=selected_run.get("id"),
        )
        detail = selected_run.get("launch_url") or selected_run.get("output_excerpt") or selected_run.get("error_text") or "Run completed."
        add_event(
            selected_run.get("finished_at") or selected_run.get("created_at"),
            "run-finished",
            f"Task run {run_status}",
            detail,
            status=run_status,
            level="error" if run_status == "failed" else ("warn" if run_status in {"launch-required", "manual-only", "launch-pending", "waiting-feedback"} else "ok"),
            run_id=selected_run.get("id"),
            alert_id=selected_run.get("alert_id"),
        )

    for step in step_items:
        add_event(
            step.get("started_at"),
            "step-started",
            step.get("step_name") or step.get("step_id") or "Step started",
            f"{(step.get('step_kind') or 'step').replace('-', ' ')} started.",
            status="running",
            level="info",
            run_id=run_id,
            step_id=step.get("step_id") or "",
        )
        add_event(
            step.get("finished_at") or step.get("started_at"),
            "step-finished",
            f"{step.get('step_name') or step.get('step_id') or 'Step'} {step.get('status') or 'finished'}",
            json.dumps(step.get("output") or {}, ensure_ascii=False)[:1500] or step.get("error_text") or "Step finished.",
            status=step.get("status") or "",
            level="error" if (step.get("status") or "") in {"failed", "cancelled"} else ("warn" if (step.get("status") or "") in {"waiting-feedback", "launch-pending", "skipped"} else "ok"),
            run_id=run_id,
            step_id=step.get("step_id") or "",
        )

    for feedback in feedback_items:
        add_event(
            feedback.get("created_at"),
            "agent-feedback",
            f"{(feedback.get('agent_id') or 'Agent').upper()} feedback",
            feedback.get("summary") or json.dumps(feedback.get("payload") or {}, ensure_ascii=False)[:1500] or "Feedback received.",
            status=feedback.get("status") or "",
            level="error" if (feedback.get("status") or "") in {"failed", "cancelled", "error"} else "ok",
            run_id=feedback.get("run_id") or run_id,
            step_id=feedback.get("step_id") or "",
        )

    for alert in sorted(alert_items, key=lambda item: item.get("created_at") or ""):
        add_event(
            alert.get("created_at"),
            "alert-created",
            "Alert created",
            f"{alert.get('title') or 'Alert'} · Trigger={alert.get('trigger_text') or '—'} · {alert.get('summary') or ''}",
            status=alert.get("status") or "open",
            level="ok",
            alert_id=alert.get("id"),
            run_id=alert.get("run_id"),
        )
        if alert.get("snoozed_until"):
            add_event(
                alert.get("updated_at") or alert.get("created_at"),
                "alert-snoozed",
                "Alert snoozed",
                f"Alert #{alert.get('id')} snoozed until {alert.get('snoozed_until')}.",
                status=alert.get("status") or "snoozed",
                level="warn",
                alert_id=alert.get("id"),
                run_id=alert.get("run_id"),
            )
        if alert.get("resolved_at"):
            add_event(
                alert.get("resolved_at"),
                "alert-resolved",
                "Alert resolved",
                f"Alert #{alert.get('id')} was resolved.",
                status=alert.get("status") or "resolved",
                level="ok",
                alert_id=alert.get("id"),
                run_id=alert.get("run_id"),
            )
        if alert.get("acknowledged_at"):
            add_event(
                alert.get("acknowledged_at"),
                "alert-acknowledged",
                "Alert acknowledged",
                f"Alert #{alert.get('id')} was acknowledged.",
                status="acknowledged",
                level="info",
                alert_id=alert.get("id"),
                run_id=alert.get("run_id"),
            )

    deduped = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in sorted(events, key=lambda event: (event.get("ts") or "", event.get("kind") or "")):
        key = (
            item.get("ts") or "",
            item.get("kind") or "",
            item.get("run_id") or "",
            str(item.get("alert_id") or ""),
            (item.get("detail") or "")[:120],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    events = deduped
    summary = {
        "runs_total": len(run_items),
        "alerts_total": len(alert_items),
        "open_alerts": sum(1 for a in alert_items if (a.get("status") or "open") == "open"),
        "last_status": task.get("lifecycle_state") or task.get("last_status") or "idle",
        "latest_run_duration_label": (selected_run or {}).get("duration_label") or "—",
        "trace_id": task.get("id") or "",
        "run_status": (selected_run or {}).get("status") or "",
        "current_step_id": (selected_run or {}).get("current_step_id") or "",
        "terminal_reason": (selected_run or {}).get("terminal_reason") or "",
        "feedback_total": len(feedback_items),
        "steps_total": len(step_items),
    }
    return {
        "task": task,
        "run": selected_run,
        "alerts": alert_items,
        "feedback": feedback_items,
        "steps": step_items,
        "summary": summary,
        "trace": _task_trace_payload(task, selected_run, latest_alert),
        "events": events,
    }


def _seed_tasked_examples() -> dict:
    created_ids: list[str] = []
    base = datetime.now(timezone.utc) - timedelta(minutes=40)

    with _db() as conn:
        for idx, spec in enumerate(TASK_EXAMPLE_SPECS):
            task_id = spec["id"]
            run_id = f"trun_example_{idx + 1}"
            created_at = (base + timedelta(minutes=idx * 3)).isoformat()
            started_at = (base + timedelta(minutes=idx * 3 + 1)).isoformat()
            finished_at = (base + timedelta(minutes=idx * 3 + 2)).isoformat()
            acknowledged_at = (base + timedelta(minutes=idx * 3 + 3)).isoformat() if spec.get("acknowledged") else None
            payload = {
                "triggered": True,
                "trigger": spec["trigger"],
                "title": spec["title"],
                "summary": spec["summary"],
                "details": spec["details"],
            }
            excerpt = json.dumps(payload, ensure_ascii=False)

            if task_id == "task_example_jhb_nvidia":
                mode = "chat"
                schedule_kind = "recurring"
                interval_minutes = 12
                active = 1
                tabs_required = 2
                planner_prompt = "Check Johannesburg weather, then check Nvidia market cap, evaluate the combined condition, and repeat the alert every 5 minutes while the condition remains true."
                executor_prompt = "Use structured steps instead of a single prompt."
                context_handoff = "Tab 1 checks Johannesburg weather. Tab 2 checks Nvidia market cap. The combined condition is evaluated before the alert cadence is armed."
                trigger_mode = "always"
                trigger_text = "Johannesburg weather and Nvidia market cap rule"
                alert_policy = {"repeat_every_minutes": 5, "dedupe_key_template": "jhb-nvidia-{task_id}", "severity": "warning", "while_condition_true": True}
                completion_policy = _task_default_completion_policy()
                steps = [
                    {"id": f"{task_id}_trigger", "name": "Trigger", "kind": "trigger", "config": {"schedule_kind": "recurring", "interval_minutes": 12}},
                    {"id": f"{task_id}_weather", "name": "Fetch Johannesburg weather", "kind": "chat", "config": {"prompt": "Fetch Johannesburg weather and return JSON."}},
                    {"id": f"{task_id}_market", "name": "Fetch Nvidia market cap", "kind": "chat", "config": {"prompt": "Fetch Nvidia market cap and return JSON."}},
                    {"id": f"{task_id}_condition", "name": "Evaluate combined condition", "kind": "condition", "config": {"operator": "AND", "rules": [
                        {"source": f"{task_id}_weather", "field": "parsed.temp_c", "comparator": "gt", "value": 14},
                        {"source": f"{task_id}_market", "field": "parsed.market_cap_usd", "comparator": "gt", "value": 2000000000000},
                    ]}},
                    {"id": f"{task_id}_alert", "name": "Raise repeating alert", "kind": "alert", "config": {"title": spec["title"], "trigger_text": spec["trigger"], "severity": "warning", "repeat_every_minutes": 5, "dedupe_key": "jhb-nvidia-{task_id}", "summary": spec["summary"]}},
                    {"id": f"{task_id}_complete", "name": "Complete", "kind": "complete", "config": {}},
                ]
                step_outputs = [
                    ("task_example_jhb_nvidia_trigger", "Trigger", "trigger", {"schedule_kind": "recurring", "interval_minutes": 12}),
                    ("task_example_jhb_nvidia_weather", "Fetch Johannesburg weather", "chat", {"text": '{"triggered": true, "city": "Johannesburg", "temp_c": 18.4}', "parsed": {"triggered": True, "city": "Johannesburg", "temp_c": 18.4}, "ok": True}),
                    ("task_example_jhb_nvidia_market", "Fetch Nvidia market cap", "chat", {"text": '{"triggered": true, "company": "Nvidia", "market_cap_usd": 2120000000000}', "parsed": {"triggered": True, "company": "Nvidia", "market_cap_usd": 2120000000000}, "ok": True}),
                    ("task_example_jhb_nvidia_condition", "Evaluate combined condition", "condition", {"matched": True, "operator": "AND", "details": [
                        {"source": f"{task_id}_weather", "field": "parsed.temp_c", "comparator": "gt", "expected": 14, "actual": 18.4, "passed": True},
                        {"source": f"{task_id}_market", "field": "parsed.market_cap_usd", "comparator": "gt", "expected": 2000000000000, "actual": 2120000000000, "passed": True},
                    ]}),
                    ("task_example_jhb_nvidia_alert", "Raise repeating alert", "alert", {"title": spec["title"], "severity": "warning"}),
                    ("task_example_jhb_nvidia_complete", "Complete", "complete", {"completed": True, "summary": spec["summary"]}),
                ]
                feedback_rows: list[tuple[str, str, str, str, str, str, str, str, str]] = []
            elif task_id == "task_example_gmail_sender":
                mode = "chat"
                schedule_kind = "recurring"
                interval_minutes = 10
                active = 1
                tabs_required = 2
                planner_prompt = "Check Gmail or Outlook for a new email from sampelexample@example.com, extract sender and subject, then create an alert."
                executor_prompt = "Detect sender, extract subject, and copy the context into a second tab."
                context_handoff = "Tab 1 detects the sender and subject. Tab 2 receives the extracted metadata before the alert is created."
                trigger_mode = "json"
                trigger_text = "incoming email from sampelexample"
                alert_policy = {"repeat_every_minutes": 0, "dedupe_key_template": "gmail-{task_id}", "severity": "info", "while_condition_true": False}
                completion_policy = _task_default_completion_policy()
                steps = [
                    {"id": f"{task_id}_trigger", "name": "Trigger", "kind": "trigger", "config": {"schedule_kind": "recurring", "interval_minutes": 10}},
                    {"id": f"{task_id}_email", "name": "Detect email", "kind": "chat", "config": {"prompt": "Detect email from sampelexample@example.com and return JSON."}},
                    {"id": f"{task_id}_alert", "name": "Create alert", "kind": "alert", "config": {"title": spec["title"], "trigger_text": spec["trigger"], "summary": spec["summary"]}},
                    {"id": f"{task_id}_complete", "name": "Complete", "kind": "complete", "config": {}},
                ]
                step_outputs = [
                    (f"{task_id}_trigger", "Trigger", "trigger", {"schedule_kind": "recurring", "interval_minutes": 10}),
                    (f"{task_id}_email", "Detect email", "chat", {"text": '{"triggered": true, "sender": "sampelexample@example.com", "subject": "Project handoff update"}', "parsed": {"triggered": True, "sender": "sampelexample@example.com", "subject": "Project handoff update"}, "ok": True}),
                    (f"{task_id}_alert", "Create alert", "alert", {"title": spec["title"], "severity": "info"}),
                    (f"{task_id}_complete", "Complete", "complete", {"completed": True, "summary": spec["summary"]}),
                ]
                feedback_rows = []
            elif task_id == "task_example_sharepoint_file":
                mode = "agent"
                schedule_kind = "recurring"
                interval_minutes = 10
                active = 1
                tabs_required = 2
                planner_prompt = "Launch C6 to inspect SharePoint, then wait for structured feedback before creating the alert."
                executor_prompt = "Find a newly added SharePoint file and return folder path plus file name."
                context_handoff = "C6 returns structured file metadata, then the alert stage uses that feedback to complete the workflow."
                trigger_mode = "always"
                trigger_text = "new SharePoint file event"
                alert_policy = {"repeat_every_minutes": 0, "dedupe_key_template": "sharepoint-{task_id}", "severity": "info", "while_condition_true": False}
                completion_policy = _task_default_completion_policy()
                steps = [
                    {"id": f"{task_id}_trigger", "name": "Trigger", "kind": "trigger", "config": {"schedule_kind": "recurring", "interval_minutes": 10}},
                    {"id": f"{task_id}_agent", "name": "C6 SharePoint check", "kind": "agent", "config": {"prompt": "Check SharePoint for a newly added file and return structured feedback.", "agent_id": "c6-kilocode"}},
                    {"id": f"{task_id}_alert", "name": "Create alert", "kind": "alert", "config": {"title": spec["title"], "trigger_text": spec["trigger"], "summary": spec["summary"]}},
                    {"id": f"{task_id}_complete", "name": "Complete", "kind": "complete", "config": {}},
                ]
                step_outputs = [
                    (f"{task_id}_trigger", "Trigger", "trigger", {"schedule_kind": "recurring", "interval_minutes": 10}),
                    (f"{task_id}_agent", "C6 SharePoint check", "agent", {"launch_url": f"/agent?task={quote('Check SharePoint for a new file')}", "agent_id": "c6-kilocode", "prompt": "Check SharePoint for a new file"}),
                    (f"{task_id}_alert", "Create alert", "alert", {"title": spec["title"], "severity": "info"}),
                    (f"{task_id}_complete", "Complete", "complete", {"completed": True, "summary": spec["summary"]}),
                ]
                feedback_rows = [
                    (
                        f"tfb_example_{idx + 1}",
                        task_id,
                        run_id,
                        f"{task_id}_agent",
                        "c6-kilocode",
                        "result",
                        "completed",
                        json.dumps({"sub_task": True, "file_name": spec["details"]["file_name"], "folder": spec["details"]["folder"]}, ensure_ascii=False),
                        "C6 found the new SharePoint file and returned structured metadata.",
                        "SharePoint file matched and passed back to Tasked.",
                    ),
                ]
            else:
                mode = "chat"
                schedule_kind = "recurring"
                interval_minutes = 10
                active = 1
                tabs_required = 2
                planner_prompt = "Check Outlook, extract the linked SharePoint file, verify the document, then create a combined alert."
                executor_prompt = "Use two tabs to merge Outlook and SharePoint context."
                context_handoff = "Outlook email context is copied into the second tab, where SharePoint verification completes the task."
                trigger_mode = "always"
                trigger_text = "email and linked SharePoint document"
                alert_policy = {"repeat_every_minutes": 0, "dedupe_key_template": "outlook-sp-{task_id}", "severity": "info", "while_condition_true": False}
                completion_policy = _task_default_completion_policy()
                steps = [
                    {"id": f"{task_id}_trigger", "name": "Trigger", "kind": "trigger", "config": {"schedule_kind": "recurring", "interval_minutes": 10}},
                    {"id": f"{task_id}_email", "name": "Detect Outlook email", "kind": "chat", "config": {"prompt": "Detect the Outlook email and extract its metadata."}},
                    {"id": f"{task_id}_file", "name": "Match SharePoint file", "kind": "chat", "config": {"prompt": "Check SharePoint for the related file and return JSON."}},
                    {"id": f"{task_id}_alert", "name": "Create combined alert", "kind": "alert", "config": {"title": spec["title"], "trigger_text": spec["trigger"], "summary": spec["summary"]}},
                    {"id": f"{task_id}_complete", "name": "Complete", "kind": "complete", "config": {}},
                ]
                step_outputs = [
                    (f"{task_id}_trigger", "Trigger", "trigger", {"schedule_kind": "recurring", "interval_minutes": 10}),
                    (f"{task_id}_email", "Detect Outlook email", "chat", {"text": '{"triggered": true, "sender": "sampelexample@example.com", "subject": "Updated project timeline"}', "parsed": {"triggered": True, "sender": "sampelexample@example.com", "subject": "Updated project timeline"}, "ok": True}),
                    (f"{task_id}_file", "Match SharePoint file", "chat", {"text": '{"triggered": true, "file_name": "Project-Timeline.docx"}', "parsed": {"triggered": True, "file_name": "Project-Timeline.docx"}, "ok": True}),
                    (f"{task_id}_alert", "Create combined alert", "alert", {"title": spec["title"], "severity": "info"}),
                    (f"{task_id}_complete", "Complete", "complete", {"completed": True, "summary": spec["summary"]}),
                ]
                feedback_rows = []

            # Seeded examples should show a full completed history without immediately
            # rescheduling themselves in the background on a low-memory branch stack.
            active = 0
            next_run_at = _task_next_run_at(schedule_kind, interval_minutes, base=base + timedelta(minutes=idx * 3 + 2)) if active else None

            conn.execute("DELETE FROM task_alerts WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM task_feedback_events WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM task_step_results WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM task_events WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM task_runs WHERE id=?", (run_id,))
            conn.execute("DELETE FROM task_alerts WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM task_feedback_events WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM task_step_results WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM task_workflow_steps WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM task_events WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM task_runs WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM task_definitions WHERE id=?", (task_id,))

            conn.execute(
                "INSERT INTO task_definitions (id, created_at, updated_at, name, mode, schedule_kind, interval_minutes, active, tabs_required, "
                "template_key, planner_prompt, executor_prompt, context_handoff, trigger_mode, trigger_text, notes, last_run_at, next_run_at, "
                "last_status, last_result_excerpt, completion_policy_json, alert_policy_json, workflow_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    created_at,
                    finished_at,
                    spec["name"],
                    mode,
                    schedule_kind,
                    interval_minutes,
                    active,
                    tabs_required,
                    spec["template_key"],
                    planner_prompt,
                    executor_prompt,
                    context_handoff,
                    trigger_mode,
                    trigger_text,
                    "Seeded Tasked example row for Tasked → piplinetask → Alerts validation.",
                    finished_at,
                    next_run_at,
                    "completed",
                    excerpt[:500],
                    json.dumps(completion_policy, ensure_ascii=False),
                    json.dumps(alert_policy, ensure_ascii=False),
                    1,
                ),
            )
            _task_save_steps(conn, task_id, steps)

            conn.execute(
                "INSERT INTO task_runs (id, task_id, created_at, started_at, finished_at, completed_at, source, status, mode, output_excerpt, error_text, alert_id, launch_url, current_step_id, terminal_reason, trigger_snapshot_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    task_id,
                    created_at,
                    started_at,
                    finished_at,
                    finished_at,
                    "seeded-example",
                    "completed",
                    mode,
                    excerpt[:2000],
                    "",
                    None,
                    "",
                    f"{task_id}_complete",
                    "workflow-complete",
                    json.dumps({"seeded": True, "task_id": task_id}, ensure_ascii=False),
                ),
            )

            for offset, (step_id, step_name, step_kind, output) in enumerate(step_outputs):
                step_started = (base + timedelta(minutes=idx * 3 + 1, seconds=offset * 4)).isoformat()
                step_finished = (base + timedelta(minutes=idx * 3 + 1, seconds=offset * 4 + 2)).isoformat()
                conn.execute(
                    "INSERT INTO task_step_results (id, run_id, task_id, step_id, step_name, step_kind, started_at, finished_at, status, output_json, duration_ms, error_text) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"tsr_example_{idx + 1}_{offset + 1}",
                        run_id,
                        task_id,
                        step_id,
                        step_name,
                        step_kind,
                        step_started,
                        step_finished,
                        "completed",
                        json.dumps(output, ensure_ascii=False),
                        _duration_ms(step_started, step_finished) or 0,
                        "",
                    ),
                )

            for feedback_id, task_fk, run_fk, step_id, agent_id, feedback_type, feedback_status, payload_json, summary, raw_excerpt in feedback_rows:
                conn.execute(
                    "INSERT INTO task_feedback_events (id, task_id, run_id, step_id, agent_id, feedback_type, status, payload_json, summary, raw_excerpt, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        feedback_id,
                        task_fk,
                        run_fk,
                        step_id,
                        agent_id,
                        feedback_type,
                        feedback_status,
                        payload_json,
                        summary,
                        raw_excerpt,
                        (base + timedelta(minutes=idx * 3 + 1, seconds=12)).isoformat(),
                    ),
                )

            event_rows = [
                (task_id, created_at, "task-created", "completed", f"Seeded workflow created for {spec['name']}.", "", None),
                (task_id, started_at, "task-run-started", "running", f"Seeded example run {run_id} started.", run_id, None),
                (task_id, finished_at, "task-run-finished", "completed", spec["summary"], run_id, None),
            ]
            if task_id == "task_example_sharepoint_file":
                event_rows.insert(2, (task_id, (base + timedelta(minutes=idx * 3 + 1, seconds=8)).isoformat(), "agent-launch", "launch-pending", "C6 task launched from Tasked and awaited feedback.", run_id, None))
                event_rows.insert(3, (task_id, (base + timedelta(minutes=idx * 3 + 1, seconds=12)).isoformat(), "agent-feedback", "completed", "C6 returned the SharePoint file metadata.", run_id, None))
            for task_fk, ts, event_type, event_status, detail, event_run_id, alert_fk in event_rows:
                conn.execute(
                    "INSERT INTO task_events (task_id, created_at, event_type, status, detail, run_id, alert_id) VALUES (?,?,?,?,?,?,?)",
                    (task_fk, ts, event_type, event_status, detail[:1500], event_run_id, alert_fk),
                )

            cur = conn.execute(
                "INSERT INTO task_alerts (task_id, run_id, created_at, updated_at, status, title, trigger_text, summary, payload_json, acknowledged_at, severity, repeat_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    run_id,
                    finished_at,
                    acknowledged_at or finished_at,
                    "acknowledged" if acknowledged_at else "open",
                    spec["title"][:160],
                    spec["trigger"][:240],
                    spec["summary"][:1500],
                    json.dumps(payload, ensure_ascii=False),
                    acknowledged_at,
                    (alert_policy.get("severity") or "info"),
                    (alert_policy.get("dedupe_key_template") or "").format(task_id=task_id),
                ),
            )
            conn.execute("UPDATE task_runs SET alert_id=? WHERE id=?", (cur.lastrowid, run_id))
            created_ids.append(task_id)

    return {
        "ok": True,
        "seeded_count": len(created_ids),
        "task_ids": created_ids,
    }


def _task_parse_json_payload(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _tasked_author_examples_payload() -> list[dict]:
    return [dict(item) for item in TASKED_AUTHORING_EXAMPLES]


def _tasked_authoring_prompt_markdown() -> str:
    try:
        return TASKED_AUTHORING_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "Translate a plain-English task request into a Tasked JSON draft. "
            "Prefer an existing template when the request closely matches one. "
            "Otherwise return a free-hand draft with a linear steps array. "
            "Use C12b as the only sandbox target. Return JSON only."
        )


def _tasked_author_template_catalog() -> list[dict]:
    catalog: list[dict] = []
    for item in _task_templates_payload():
        catalog.append({
            "key": item.get("key") or "",
            "name": item.get("name") or "",
            "description": item.get("description") or "",
            "mode": item.get("mode") or "chat",
            "schedule_kind": item.get("schedule_kind") or "manual",
            "interval_minutes": int(item.get("interval_minutes") or 0),
            "tabs_required": int(item.get("tabs_required") or 1),
            "trigger_mode": item.get("trigger_mode") or "json",
            "trigger_text": item.get("trigger_text") or "",
        })
    return catalog


def _tasked_author_find_template_by_key(template_key: str, templates: list[dict] | None = None) -> dict | None:
    key = (template_key or "").strip()
    if not key:
        return None
    templates = templates or _task_templates_payload()
    return next((item for item in templates if item.get("key") == key), None)


def _tasked_author_parse_word_number(token: str) -> int | None:
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    cleaned = (token or "").strip().lower()
    if cleaned.isdigit():
        return int(cleaned)
    return words.get(cleaned)


def _tasked_author_guess_interval_minutes(prompt: str) -> int:
    text = (prompt or "").strip().lower()
    match = re.search(r"\bevery\s+(\d+)\s+minutes?\b", text)
    if match:
        return max(0, int(match.group(1)))
    match = re.search(r"\bevery\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+minutes?\b", text)
    if match:
        return max(0, _tasked_author_parse_word_number(match.group(1)) or 0)
    return 0


def _tasked_author_guess_tabs_required(prompt: str) -> int:
    text = (prompt or "").strip().lower()
    match = re.search(r"\b(\d+)\s+tabs?\b", text)
    if match:
        return max(1, min(12, int(match.group(1))))
    match = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+tabs?\b", text)
    if match:
        return max(1, min(12, _tasked_author_parse_word_number(match.group(1)) or 1))
    if "tab to tab" in text or "two tabs" in text or "2 tabs" in text:
        return 2
    return 1


def _tasked_author_guess_schedule_kind(prompt: str, interval_minutes: int) -> str:
    text = (prompt or "").strip().lower()
    if any(phrase in text for phrase in ("continuous", "live monitor", "live / continuous", "watch continuously")):
        return "continuous"
    if interval_minutes > 0 or "every " in text:
        return "recurring"
    return "manual"


def _tasked_author_guess_mode(prompt: str, mode_hint: str = "") -> str:
    hint = (mode_hint or "").strip().lower()
    if hint in {item["id"] for item in TASK_MODE_OPTIONS}:
        return hint
    text = (prompt or "").strip().lower()
    if "multi-agento" in text:
        return "multi-agento"
    if "multi-agent" in text or "multi agent" in text:
        return "multi-agent"
    if re.search(r"\bagent\b", text):
        return "agent"
    if any(term in text for term in ("c12b", "sandbox", "python", "pytest", "node", "npm", "javascript", "write code", "run code", "shell command")):
        return "sandbox"
    return "chat"


def _tasked_author_guess_name(prompt: str) -> str:
    cleaned = re.sub(r"\s+", " ", (prompt or "").strip())
    if not cleaned:
        return "Generated Tasked"
    cleaned = re.sub(r"^\s*every\s+\d+\s+minutes?,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned[:88].strip(" .,:;")
    if not cleaned:
        return "Generated Tasked"
    return cleaned[0].upper() + cleaned[1:]


def _tasked_author_guess_temperature_threshold(prompt: str) -> float | None:
    text = (prompt or "").lower()
    if "weather" not in text and "temperature" not in text:
        return None
    match = re.search(r"(?:temperature|weather).*?\babove\s+(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"\babove\s+(\d+(?:\.\d+)?)\s*(?:degrees?|c|celsius)\b", text)
    if match:
        return float(match.group(1))
    return None


def _tasked_author_guess_market_cap_threshold(prompt: str) -> float | None:
    text = (prompt or "").lower()
    if "market cap" not in text:
        return None
    match = re.search(r"\babove\s+(\d+(?:\.\d+)?)\s*(trillion|billion|million)\b", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    multiplier = {
        "million": 1_000_000,
        "billion": 1_000_000_000,
        "trillion": 1_000_000_000_000,
    }.get(unit, 1)
    return value * multiplier


def _tasked_author_match_template(prompt: str, templates: list[dict] | None = None, preferred_key: str = "") -> dict | None:
    templates = templates or _task_templates_payload()
    preferred = _tasked_author_find_template_by_key(preferred_key, templates)
    if preferred:
        return preferred
    text = (prompt or "").strip().lower()
    if not text:
        return None
    if "johannesburg" in text or "nvidia" in text or "market cap" in text:
        return None
    if "dublin" in text and ("weather" in text or "temperature" in text):
        return _tasked_author_find_template_by_key("weather-dublin", templates)
    if "sharepoint" in text and any(term in text for term in ("email", "outlook", "attachment", "document link", "linked file")):
        return _tasked_author_find_template_by_key("outlook-sharepoint-linked", templates)
    if "alerts@company.com" in text or ("m365 outlook" in text and "email" in text):
        return _tasked_author_find_template_by_key("m365-outlook-alert", templates)
    if "sampelexample@example.com" in text and any(term in text for term in ("gmail", "outlook", "email")):
        return _tasked_author_find_template_by_key("gmail-sender", templates)
    if "sharepoint" in text and "file" in text:
        return _tasked_author_find_template_by_key("sharepoint-new-file", templates)
    if any(term in text for term in ("sandbox", "pytest", "py_compile", "python code")):
        return _tasked_author_find_template_by_key("sandbox-python-validate", templates)
    return None


def _tasked_author_template_seed_draft(template: dict) -> dict:
    mode = (template.get("mode") or "chat").strip().lower()
    draft = {
        "id": "",
        "template_key": template.get("key") or "",
        "name": template.get("name") or "Generated Tasked",
        "mode": mode,
        "schedule_kind": template.get("schedule_kind") or "manual",
        "interval_minutes": int(template.get("interval_minutes") or 0),
        "tabs_required": int(template.get("tabs_required") or 1),
        "active": True,
        "planner_prompt": template.get("planner_prompt") or "",
        "executor_prompt": template.get("executor_prompt") or "",
        "executor_target": "c12b" if mode == "sandbox" else "",
        "workspace_dir": _task_sandbox_workspace(template.get("workspace_dir"), "c12b") if mode == "sandbox" else "",
        "validation_command": template.get("validation_command") or "",
        "test_command": template.get("test_command") or "",
        "sandbox_assist": bool(template.get("sandbox_assist")) if mode != "sandbox" else False,
        "sandbox_assist_target": "c12b" if template.get("sandbox_assist") and mode != "sandbox" else "",
        "sandbox_assist_workspace_dir": _task_sandbox_workspace(template.get("sandbox_assist_workspace_dir"), "c12b") if template.get("sandbox_assist") and mode != "sandbox" else "",
        "sandbox_assist_command": template.get("sandbox_assist_command") or "",
        "sandbox_assist_validation_command": template.get("sandbox_assist_validation_command") or "",
        "sandbox_assist_test_command": template.get("sandbox_assist_test_command") or "",
        "context_handoff": template.get("context_handoff") or "",
        "trigger_mode": template.get("trigger_mode") or "json",
        "trigger_text": template.get("trigger_text") or "",
        "notes": template.get("description") or "",
        "alert_policy": _task_default_alert_policy(),
        "completion_policy": _task_default_completion_policy(),
    }
    draft["steps"] = _task_build_default_steps({**draft, "id": "task_draft"})
    return draft


def _tasked_author_condition_rules(prompt: str, execute_step_id: str) -> list[dict]:
    text = (prompt or "").strip().lower()
    rules: list[dict] = []
    temp_threshold = _tasked_author_guess_temperature_threshold(prompt)
    if temp_threshold is not None:
        rules.append({
            "source": execute_step_id,
            "field": "parsed.temp_c",
            "comparator": "gt",
            "value": temp_threshold,
        })
    market_cap_threshold = _tasked_author_guess_market_cap_threshold(prompt)
    if market_cap_threshold is not None:
        rules.append({
            "source": execute_step_id,
            "field": "parsed.market_cap_usd",
            "comparator": "gt",
            "value": market_cap_threshold,
        })
    if "{sub task}" in text or "sub task" in text or "sub_task" in text:
        rules.append({
            "source": execute_step_id,
            "field": "sub_task",
            "comparator": "eq",
            "value": True,
        })
    if "{x}" in text or re.search(r"\breturn another\s+x\b", text) or re.search(r"\bx exists\b", text):
        rules.append({
            "source": execute_step_id,
            "field": "x",
            "comparator": "exists",
            "value": True,
        })
    return rules


def _tasked_author_freehand_scaffold(prompt: str, mode_hint: str = "") -> dict:
    interval_minutes = _tasked_author_guess_interval_minutes(prompt)
    schedule_kind = _tasked_author_guess_schedule_kind(prompt, interval_minutes)
    mode = _tasked_author_guess_mode(prompt, mode_hint=mode_hint)
    tabs_required = _tasked_author_guess_tabs_required(prompt)
    trigger_text = "task trigger"
    text = (prompt or "").lower()
    if "weather" in text and "johannesburg" in text and "nvidia" in text:
        trigger_text = "Johannesburg weather and Nvidia market cap rule"
    elif "sharepoint" in text:
        trigger_text = "SharePoint task trigger"
    elif "email" in text or "outlook" in text or "gmail" in text:
        trigger_text = "Email task trigger"
    alert_repeat = 0
    alert_repeat_match = re.search(r"\balert(?:\s+\w+){0,4}\s+every\s+(\d+)\s+minutes?\b", text)
    if alert_repeat_match:
        alert_repeat = int(alert_repeat_match.group(1))
    elif re.search(r"\bevery\s+5\s+minutes?\b.*?\bwhile true\b", text):
        alert_repeat = 5
    else:
        alert_repeat_match = re.search(r"\brepeat(?:ing)?\s+alert(?:s)?(?:\s+\w+){0,4}\s+every\s+(\d+)\s+minutes?\b", text)
    if alert_repeat_match and not alert_repeat:
        alert_repeat = int(alert_repeat_match.group(1))
    if not alert_repeat:
        alert_repeat_match = re.search(r"\bevery\s+(\d+)\s+minutes?\b.*?\bwhile true\b", text)
    if alert_repeat_match:
        alert_repeat = int(alert_repeat_match.group(1))
    elif "alert every 5 minutes" in text or "every 5 minutes while true" in text:
        alert_repeat = 5
    notes = "Generated from the Tasked chat planner. Review and edit this free-hand draft before saving."
    executor_prompt = (prompt or "").strip()
    validation_command = ""
    test_command = ""
    workspace_dir = ""
    executor_target = ""
    if mode == "sandbox":
        executor_target = "c12b"
        workspace_dir = "/workspace"
        if "weather" in text and "johannesburg" in text and "nvidia" in text:
            executor_prompt = (
                "cat > task_payload.py <<'PY'\n"
                "import json\n"
                "# Replace these mock values with real fetch logic for Johannesburg weather and Nvidia market cap.\n"
                "payload = {\n"
                "    'triggered': True,\n"
                "    'temp_c': 18.4,\n"
                "    'market_cap_usd': 2100000000000,\n"
                "    'trigger': 'Johannesburg weather and Nvidia market cap rule',\n"
                "    'summary': 'Mock values matched; replace this scaffold with live API calls.'\n"
                "}\n"
                "print(json.dumps(payload))\n"
                "PY\n"
                "python3 task_payload.py"
            )
            validation_command = "python3 -m py_compile task_payload.py"
            test_command = (
                "python3 - <<'PY'\n"
                "import json, subprocess\n"
                "raw = subprocess.check_output(['python3', 'task_payload.py'], text=True)\n"
                "payload = json.loads(raw)\n"
                "assert 'temp_c' in payload\n"
                "assert 'market_cap_usd' in payload\n"
                "print('sandbox payload ok')\n"
                "PY"
            )
        else:
            executor_prompt = (
                "cat > task_payload.py <<'PY'\n"
                "import json\n"
                "payload = {\n"
                "    'triggered': False,\n"
                "    'summary': 'Replace this scaffold with your custom task logic.',\n"
                "    'task_prompt': " + json.dumps((prompt or "").strip()) + "\n"
                "}\n"
                "print(json.dumps(payload))\n"
                "PY\n"
                "python3 task_payload.py"
            )
            validation_command = "python3 -m py_compile task_payload.py"
            test_command = "python3 task_payload.py"
    return {
        "id": "",
        "template_key": "",
        "name": _tasked_author_guess_name(prompt),
        "mode": mode,
        "schedule_kind": schedule_kind,
        "interval_minutes": interval_minutes,
        "tabs_required": tabs_required,
        "active": True,
        "planner_prompt": "Translate the plain-English task into a linear workflow with explicit alerts, traceability, and completion states.",
        "executor_prompt": executor_prompt,
        "executor_target": executor_target,
        "workspace_dir": workspace_dir,
        "validation_command": validation_command,
        "test_command": test_command,
        "sandbox_assist": False,
        "sandbox_assist_target": "",
        "sandbox_assist_workspace_dir": "",
        "sandbox_assist_command": "",
        "sandbox_assist_validation_command": "",
        "sandbox_assist_test_command": "",
        "context_handoff": "Keep the execution context explicit. If multiple tabs or lanes are required, copy the extracted result into the next lane before continuing.",
        "trigger_mode": "always" if mode == "sandbox" else "json",
        "trigger_text": trigger_text,
        "notes": notes,
        "alert_policy": {
            "repeat_every_minutes": alert_repeat,
            "dedupe_key_template": "",
            "severity": "warning" if alert_repeat else "info",
            "while_condition_true": bool(alert_repeat),
        },
        "completion_policy": _task_default_completion_policy(),
    }


def _tasked_author_build_linear_steps(draft: dict, prompt: str, *, raw: dict | None = None) -> list[dict]:
    task_id = "task_draft"
    schedule_kind = draft.get("schedule_kind") or "manual"
    mode = draft.get("mode") or "chat"
    steps: list[dict] = []
    if schedule_kind in {"recurring", "continuous"}:
        steps.append({
            "id": f"{task_id}_trigger",
            "position": len(steps) + 1,
            "name": "Trigger",
            "kind": "trigger",
            "config": {
                "schedule_kind": schedule_kind,
                "interval_minutes": int(draft.get("interval_minutes") or 0),
            },
            "on_success_step_id": "",
            "on_failure_step_id": "",
            "active": True,
        })
    execute_step_id = f"{task_id}_{'execute' if mode != 'sandbox' else 'sandbox'}"
    execute_step = {
        "id": execute_step_id,
        "position": len(steps) + 1,
        "name": "Execute",
        "kind": mode,
        "config": {},
        "on_success_step_id": "",
        "on_failure_step_id": "",
        "active": True,
    }
    if mode == "sandbox":
        execute_step["name"] = "Sandbox execution"
        execute_step["config"] = {
            "executor_target": "c12b",
            "workspace_dir": draft.get("workspace_dir") or "/workspace",
            "command": draft.get("executor_prompt") or "",
            "validation_command": draft.get("validation_command") or "",
            "test_command": draft.get("test_command") or "",
        }
    elif mode in {"agent", "multi-agent", "multi-agento"}:
        execute_step["name"] = "Launch " + mode
        execute_step["config"] = {
            "prompt": draft.get("executor_prompt") or draft.get("planner_prompt") or "",
            "agent_id": str(_json_load_object(raw or {}).get("agent_id") or "c6-kilocode"),
            "sandbox_assist": bool(draft.get("sandbox_assist")),
            "sandbox_assist_target": draft.get("sandbox_assist_target") or "",
            "sandbox_assist_workspace_dir": draft.get("sandbox_assist_workspace_dir") or "",
            "sandbox_assist_command": draft.get("sandbox_assist_command") or "",
            "sandbox_assist_validation_command": draft.get("sandbox_assist_validation_command") or "",
            "sandbox_assist_test_command": draft.get("sandbox_assist_test_command") or "",
        }
    else:
        execute_step["name"] = "Execute prompt"
        execute_step["config"] = {
            "prompt": draft.get("executor_prompt") or draft.get("planner_prompt") or "",
        }
    steps.append(execute_step)

    rules = _tasked_author_condition_rules(prompt, execute_step_id)
    if isinstance(_json_load_object(raw or {}).get("condition"), dict):
        cfg = _json_load_object(raw.get("condition"))
        if isinstance(cfg.get("rules"), list) and cfg.get("rules"):
            rules = cfg.get("rules")
    condition_step_id = ""
    if rules:
        condition_step_id = f"{task_id}_condition"
        steps.append({
            "id": condition_step_id,
            "position": len(steps) + 1,
            "name": "Condition gate",
            "kind": "condition",
            "config": {
                "operator": str(_json_load_object(raw or {}).get("condition", {}).get("operator") or "AND").upper(),
                "rules": rules,
            },
            "on_success_step_id": "",
            "on_failure_step_id": "",
            "active": True,
        })

    alert_step_id = f"{task_id}_alert"
    complete_step_id = f"{task_id}_complete"
    steps.append({
        "id": alert_step_id,
        "position": len(steps) + 1,
        "name": "Create alert",
        "kind": "alert",
        "config": {
            "title": draft.get("name") or "Task alert",
            "trigger_text": draft.get("trigger_text") or "task trigger",
            "repeat_every_minutes": int((draft.get("alert_policy") or {}).get("repeat_every_minutes") or 0),
            "dedupe_key": str((draft.get("alert_policy") or {}).get("dedupe_key_template") or "").strip(),
            "severity": str((draft.get("alert_policy") or {}).get("severity") or "info"),
        },
        "on_success_step_id": "",
        "on_failure_step_id": "",
        "active": True,
    })
    steps.append({
        "id": complete_step_id,
        "position": len(steps) + 1,
        "name": "Complete",
        "kind": "complete",
        "config": {},
        "on_success_step_id": "",
        "on_failure_step_id": "",
        "active": True,
    })
    if condition_step_id:
        steps[-3]["on_success_step_id"] = alert_step_id
        steps[-3]["on_failure_step_id"] = complete_step_id
        execute_step["on_success_step_id"] = condition_step_id
    else:
        execute_step["on_success_step_id"] = alert_step_id
    steps[-2]["on_success_step_id"] = complete_step_id
    return [_task_normalize_step(task_id, step, idx + 1) for idx, step in enumerate(steps)]


def _tasked_author_normalize_draft(raw: dict | None, *, prompt: str, requested_strategy: str, mode_hint: str = "", matched_template: dict | None = None) -> dict:
    raw = dict(raw or {})
    if matched_template:
        draft = _tasked_author_template_seed_draft(matched_template)
    else:
        draft = _tasked_author_freehand_scaffold(prompt, mode_hint=mode_hint)

    strategy = (raw.get("strategy") or requested_strategy or "auto").strip().lower()
    if strategy not in {"auto", "existing-template", "freehand"}:
        strategy = requested_strategy if requested_strategy in {"auto", "existing-template", "freehand"} else "auto"

    raw_template_key = (raw.get("template_key") or "").strip()
    if raw_template_key:
        maybe_template = _tasked_author_find_template_by_key(raw_template_key)
        if maybe_template:
            matched_template = maybe_template
            draft = _tasked_author_template_seed_draft(maybe_template)
    if strategy == "existing-template" and matched_template:
        draft["template_key"] = matched_template.get("key") or ""
    elif strategy == "freehand":
        draft["template_key"] = ""

    if (raw.get("name") or "").strip():
        draft["name"] = str(raw.get("name") or "").strip()[:160]
    if (raw.get("planner_prompt") or "").strip():
        draft["planner_prompt"] = str(raw.get("planner_prompt") or "").strip()
    if (raw.get("executor_prompt") or "").strip():
        draft["executor_prompt"] = str(raw.get("executor_prompt") or "").strip()
    if (raw.get("context_handoff") or "").strip():
        draft["context_handoff"] = str(raw.get("context_handoff") or "").strip()
    if (raw.get("trigger_text") or "").strip():
        draft["trigger_text"] = str(raw.get("trigger_text") or "").strip()
    if (raw.get("notes") or "").strip():
        draft["notes"] = str(raw.get("notes") or "").strip()

    mode = (raw.get("mode") or draft.get("mode") or _tasked_author_guess_mode(prompt, mode_hint=mode_hint)).strip().lower()
    if mode not in {item["id"] for item in TASK_MODE_OPTIONS}:
        mode = _tasked_author_guess_mode(prompt, mode_hint=mode_hint)
    draft["mode"] = mode

    interval_minutes = int(raw.get("interval_minutes") or draft.get("interval_minutes") or _tasked_author_guess_interval_minutes(prompt) or 0)
    schedule_kind = (raw.get("schedule_kind") or draft.get("schedule_kind") or _tasked_author_guess_schedule_kind(prompt, interval_minutes)).strip().lower()
    if schedule_kind not in {"manual", "recurring", "continuous"}:
        schedule_kind = _tasked_author_guess_schedule_kind(prompt, interval_minutes)
    if schedule_kind in {"recurring", "continuous"} and interval_minutes <= 0:
        interval_minutes = _tasked_author_guess_interval_minutes(prompt) or 10
    draft["schedule_kind"] = schedule_kind
    draft["interval_minutes"] = interval_minutes
    draft["tabs_required"] = max(1, min(12, int(raw.get("tabs_required") or draft.get("tabs_required") or _tasked_author_guess_tabs_required(prompt) or 1)))
    draft["active"] = bool(raw.get("active", draft.get("active", True)))
    draft["trigger_mode"] = str(raw.get("trigger_mode") or draft.get("trigger_mode") or ("always" if mode == "sandbox" else "json")).strip().lower()
    if draft["trigger_mode"] not in {"json", "contains", "always"}:
        draft["trigger_mode"] = "always" if mode == "sandbox" else "json"

    alert_policy = {**_task_default_alert_policy(), **_json_load_object(draft.get("alert_policy")), **_json_load_object(raw.get("alert_policy"))}
    completion_policy = {**_task_default_completion_policy(), **_json_load_object(draft.get("completion_policy")), **_json_load_object(raw.get("completion_policy"))}
    if not alert_policy.get("dedupe_key_template") and int(alert_policy.get("repeat_every_minutes") or 0) > 0:
        alert_policy["dedupe_key_template"] = _slugify(draft.get("name") or "tasked", prefix="tasked") + "-{task_id}"
    draft["alert_policy"] = alert_policy
    draft["completion_policy"] = completion_policy

    if mode == "sandbox":
        draft["executor_target"] = "c12b"
        draft["workspace_dir"] = _task_sandbox_workspace(raw.get("workspace_dir") or draft.get("workspace_dir"), "c12b")
        draft["validation_command"] = str(raw.get("validation_command") or draft.get("validation_command") or "").strip()
        draft["test_command"] = str(raw.get("test_command") or draft.get("test_command") or "").strip()
        draft["sandbox_assist"] = False
        draft["sandbox_assist_target"] = ""
        draft["sandbox_assist_workspace_dir"] = ""
        draft["sandbox_assist_command"] = ""
        draft["sandbox_assist_validation_command"] = ""
        draft["sandbox_assist_test_command"] = ""
    else:
        assist = _task_sandbox_assist_values({**draft, **raw}, mode=mode)
        draft["executor_target"] = ""
        draft["workspace_dir"] = ""
        draft["validation_command"] = ""
        draft["test_command"] = ""
        draft["sandbox_assist"] = assist["sandbox_assist"]
        draft["sandbox_assist_target"] = assist["sandbox_assist_target"]
        draft["sandbox_assist_workspace_dir"] = assist["sandbox_assist_workspace_dir"]
        draft["sandbox_assist_command"] = assist["sandbox_assist_command"]
        draft["sandbox_assist_validation_command"] = assist["sandbox_assist_validation_command"]
        draft["sandbox_assist_test_command"] = assist["sandbox_assist_test_command"]

    raw_steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    if raw_steps:
        draft["steps"] = [_task_normalize_step("task_draft", item, idx + 1) for idx, item in enumerate(raw_steps)]
    else:
        draft["steps"] = _tasked_author_build_linear_steps(draft, prompt, raw=raw)

    if requested_strategy == "existing-template" and matched_template:
        draft["template_key"] = matched_template.get("key") or ""
        strategy = "existing-template"
    elif requested_strategy == "freehand":
        draft["template_key"] = ""
        strategy = "freehand"
    elif draft.get("template_key"):
        strategy = "existing-template"
    else:
        strategy = "freehand"
    draft["strategy"] = strategy
    draft["explanation"] = str(raw.get("explanation") or "").strip()
    return draft


def _tasked_author_fallback_draft(prompt: str, *, requested_strategy: str, mode_hint: str = "", matched_template: dict | None = None) -> tuple[dict, str]:
    if requested_strategy != "freehand" and matched_template:
        draft = _tasked_author_normalize_draft({}, prompt=prompt, requested_strategy="existing-template", mode_hint=mode_hint, matched_template=matched_template)
        explanation = f'Matched the existing template "{matched_template.get("name") or matched_template.get("key")}".'
        return draft, explanation
    draft = _tasked_author_normalize_draft({}, prompt=prompt, requested_strategy="freehand", mode_hint=mode_hint, matched_template=None)
    return draft, "Built a free-hand Tasked draft because no existing template fit the request cleanly."


async def _tasked_author_draft_from_text(prompt: str, *, strategy: str = "auto", mode_hint: str = "", template_key: str = "") -> dict:
    cleaned_prompt = re.sub(r"\s+", " ", (prompt or "").strip())
    if not cleaned_prompt:
        return {"ok": False, "error": "prompt required"}
    requested_strategy = (strategy or "auto").strip().lower()
    if requested_strategy not in {"auto", "existing-template", "freehand"}:
        requested_strategy = "auto"
    templates = _task_templates_payload()
    matched_template = _tasked_author_match_template(cleaned_prompt, templates, preferred_key=template_key)
    if matched_template and requested_strategy in {"auto", "existing-template"}:
        draft, explanation = _tasked_author_fallback_draft(
            cleaned_prompt,
            requested_strategy="existing-template",
            mode_hint=mode_hint,
            matched_template=matched_template,
        )
        return {
            "ok": True,
            "requested_strategy": requested_strategy,
            "strategy_used": "existing-template",
            "matched_template": {
                "key": matched_template.get("key") or "",
                "name": matched_template.get("name") or "",
                "description": matched_template.get("description") or "",
            },
            "explanation": explanation,
            "source": "heuristic-template",
            "authoring_engine": "local" if not TASKED_AUTHOR_ENABLE_LLM else "llm",
            "draft": {key: value for key, value in draft.items() if key != "explanation"},
        }
    llm_payload = None
    llm_error = ""
    llm_response = ""
    source = "heuristic"
    if TASKED_AUTHOR_ENABLE_LLM:
        preferred_template_key = matched_template.get("key") if matched_template else ""
        prompt_text = (
            _tasked_authoring_prompt_markdown()
            + "\n\nActive Tasked templates:\n"
            + json.dumps(_tasked_author_template_catalog(), ensure_ascii=False, indent=2)
            + "\n\nRequested strategy: "
            + requested_strategy
            + "\nPreferred template key: "
            + (preferred_template_key or "(none)")
            + "\nMode hint: "
            + ((mode_hint or "").strip() or "(none)")
            + "\n\nUser task request:\n"
            + cleaned_prompt
        )
        try:
            chat_result = await asyncio.wait_for(
                _chat_one("c9-jokes-task-author", prompt_text, _urls()["c1"], chat_mode="deep", work_mode="work"),
                timeout=20,
            )
            llm_response = (chat_result or {}).get("text") or ""
            llm_payload = _task_parse_json_payload(llm_response)
        except Exception as exc:
            llm_error = str(exc)

    if llm_payload:
        draft = _tasked_author_normalize_draft(
            llm_payload,
            prompt=cleaned_prompt,
            requested_strategy=requested_strategy,
            mode_hint=mode_hint,
            matched_template=matched_template,
        )
        explanation = str(llm_payload.get("explanation") or "").strip()
        if not explanation:
            explanation = (
                f'Used the existing template "{draft.get("template_key")}".'
                if draft.get("strategy") == "existing-template" and draft.get("template_key")
                else "Built a free-hand Tasked draft from the English task prompt."
            )
        source = "llm"
    else:
        draft, explanation = _tasked_author_fallback_draft(
            cleaned_prompt,
            requested_strategy=requested_strategy,
            mode_hint=mode_hint,
            matched_template=matched_template,
        )
        if requested_strategy == "existing-template" and matched_template:
            source = "heuristic-template"
        else:
            source = "heuristic-freehand"

    resolved_template = _tasked_author_find_template_by_key(draft.get("template_key") or "", templates)
    response: dict = {
        "ok": True,
        "requested_strategy": requested_strategy,
        "strategy_used": draft.get("strategy") or ("existing-template" if resolved_template else "freehand"),
        "matched_template": {
            "key": resolved_template.get("key") or "",
            "name": resolved_template.get("name") or "",
            "description": resolved_template.get("description") or "",
        } if resolved_template else None,
        "explanation": explanation,
        "source": source,
        "draft": {key: value for key, value in draft.items() if key != "explanation"},
    }
    if llm_error:
        response["llm_error"] = llm_error
    if llm_response and source.startswith("heuristic"):
        response["llm_response_excerpt"] = llm_response[:500]
    if not TASKED_AUTHOR_ENABLE_LLM:
        response["authoring_engine"] = "local"
    return response


def _task_alert_from_result(task_row: dict, response_text: str) -> dict | None:
    parsed = _task_parse_json_payload(response_text)
    if parsed is not None:
        triggered = parsed.get("triggered")
        if isinstance(triggered, bool) and not triggered:
            return None
        if triggered is True or "title" in parsed or "summary" in parsed:
            return {
                "title": str(parsed.get("title") or task_row.get("name") or "Task alert")[:160],
                "trigger_text": str(parsed.get("trigger") or task_row.get("trigger_text") or task_row.get("name") or "")[:240],
                "summary": str(parsed.get("summary") or response_text[:500])[:1500],
                "payload_json": json.dumps(parsed, ensure_ascii=False),
            }

    trigger_mode = (task_row.get("trigger_mode") or "json").strip().lower()
    trigger_text = (task_row.get("trigger_text") or "").strip()
    lower_text = (response_text or "").lower()
    matched = False
    if trigger_mode == "always":
        matched = True
    elif trigger_mode == "contains" and trigger_text:
        matched = trigger_text.lower() in lower_text
    if not matched:
        return None
    return {
        "title": str(task_row.get("name") or "Task alert")[:160],
        "trigger_text": trigger_text[:240] or "manual trigger",
        "summary": response_text[:1500],
        "payload_json": json.dumps({"response": response_text[:4000]}, ensure_ascii=False),
    }


def _insert_task_alert(task_id: str, run_id: str, alert: dict) -> int | None:
    try:
        now = _iso_now()
        severity = (alert.get("severity") or "info").strip().lower() or "info"
        repeat_key = (alert.get("repeat_key") or "").strip()
        repeat_minutes = max(0, int(alert.get("repeat_every_minutes") or 0))
        with _db() as conn:
            if repeat_key and repeat_minutes > 0:
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=repeat_minutes)).isoformat()
                existing = conn.execute(
                    "SELECT id FROM task_alerts WHERE task_id=? AND repeat_key=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                    (task_id, repeat_key, cutoff),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO task_alerts (task_id, run_id, created_at, updated_at, status, title, trigger_text, summary, payload_json, severity, repeat_key, closed_by_run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    run_id,
                    now,
                    now,
                    "open",
                    (alert.get("title") or "Task alert")[:160],
                    (alert.get("trigger_text") or "")[:240],
                    (alert.get("summary") or "")[:1500],
                    alert.get("payload_json") or "{}",
                    severity[:24],
                    repeat_key[:200],
                    (alert.get("closed_by_run_id") or "")[:80],
                ),
            )
            return cur.lastrowid
    except sqlite3.Error:
        return None


def _record_task_event(task_id: str, event_type: str, detail: str, *, status: str = "", run_id: str = "", alert_id: int | None = None) -> None:
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO task_events (task_id, created_at, event_type, status, detail, run_id, alert_id) VALUES (?,?,?,?,?,?,?)",
                (task_id, _iso_now(), event_type, status, detail[:1500], run_id, alert_id),
            )
    except sqlite3.Error:
        return


def _task_sandbox_stage_status(result: dict | None) -> str:
    if not result:
        return ""
    if result.get("timed_out"):
        return "timed-out"
    return "completed" if int(result.get("exit_code") or 0) == 0 else "failed"


def _task_sandbox_excerpt(result: dict | None, limit: int = 500) -> str:
    if not result:
        return ""
    text = (result.get("stdout") or "").strip()
    if not text:
        text = (result.get("stderr") or "").strip()
    return text[:limit]


def _task_append_alert_metadata(alert: dict | None, extra_details: dict, *, extra_summary: str = "") -> dict | None:
    if not alert:
        return None
    merged = dict(alert)
    try:
        payload = json.loads(merged.get("payload_json") or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    details = payload.get("details")
    if not isinstance(details, dict):
        details = {}
    details.update(extra_details)
    payload["details"] = details
    merged["payload_json"] = json.dumps(payload, ensure_ascii=False)
    if extra_summary:
        summary = (merged.get("summary") or "").strip()
        merged["summary"] = ((summary + "\n\n" + extra_summary).strip())[:1500]
    return merged


async def _task_execute_sandbox_plan(
    *,
    task_id: str,
    run_id: str,
    source: str,
    target: str,
    workspace_dir: str,
    command: str,
    validation_command: str = "",
    test_command: str = "",
    task_name: str = "Sandbox task",
    label: str = "Sandbox",
    event_prefix: str = "sandbox",
    trigger_mode: str = "json",
    trigger_text: str = "",
    alert_on_success: bool = True,
) -> dict:
    timeout = 120
    if not command:
        return {
            "ok": False,
            "status": "failed",
            "text": "",
            "error": f"{label} requires an execution command",
            "alert": None,
            "executor_target": target,
            "workspace_dir": workspace_dir,
            "sandbox_session_id": "",
            "validation_status": "",
            "validation_excerpt": "",
            "test_status": "",
            "test_excerpt": "",
            "summary_text": "",
        }

    async def run_stage(stage_command: str, stage: str, prior_session_id: str = "") -> dict:
        result = await _c12b_exec(stage_command, timeout=timeout, cwd=_task_c12b_cwd(workspace_dir), session_id=prior_session_id)
        status = _task_sandbox_stage_status(result)
        excerpt = _task_sandbox_excerpt(result)
        _record_task_event(
            task_id,
            f"{event_prefix}-{stage}",
            f"{label} {stage} on {_task_executor_target_label(target)}\nWorkspace: {workspace_dir}\nCommand: {stage_command}\nStatus: {status or 'unknown'}\n{excerpt}",
            status=status or "unknown",
            run_id=run_id,
        )
        return result | {"stage_status": status, "excerpt": excerpt}

    exec_result = await run_stage(command, "exec")
    session_id = exec_result.get("session_id") or ""
    validation_result = None
    test_result = None
    if exec_result.get("stage_status") == "completed" and validation_command:
        validation_result = await run_stage(validation_command, "validate", session_id)
        session_id = validation_result.get("session_id") or session_id
    if exec_result.get("stage_status") == "completed" and (validation_result is None or validation_result.get("stage_status") == "completed") and test_command:
        test_result = await run_stage(test_command, "test", session_id)
        session_id = test_result.get("session_id") or session_id

    failures = [item for item in (exec_result, validation_result, test_result) if item and item.get("stage_status") != "completed"]
    overall_ok = not failures
    parsed_output = _task_parse_json_payload(exec_result.get("stdout") or "") or _task_parse_json_payload(exec_result.get("stderr") or "") or {}

    sandbox_label = "Sandbox assist" if event_prefix == "sandbox-assist" else "Sandbox"
    summary_lines = [
        f"{sandbox_label} target: {_task_executor_target_label(target)}",
        f"Workspace: {workspace_dir}",
        f"Execution: {exec_result.get('stage_status') or 'unknown'}",
    ]
    if validation_command:
        summary_lines.append(f"Validation: {(validation_result or {}).get('stage_status') or 'skipped'}")
    if test_command:
        summary_lines.append(f"Test: {(test_result or {}).get('stage_status') or 'skipped'}")

    combined_text = "\n\n".join(
        part for part in [
            "\n".join(summary_lines),
            _task_sandbox_excerpt(exec_result, 1200),
            _task_sandbox_excerpt(validation_result, 800),
            _task_sandbox_excerpt(test_result, 800),
        ] if part
    )

    alert_triggered = bool(failures)
    if not alert_triggered and alert_on_success:
        if trigger_mode == "always":
            alert_triggered = True
        elif trigger_mode == "contains" and trigger_text:
            alert_triggered = trigger_text.lower() in combined_text.lower()
        elif trigger_mode == "json":
            parsed = _task_parse_json_payload(exec_result.get("stdout") or combined_text)
            if parsed is not None and parsed.get("triggered") is True:
                alert_triggered = True

    payload = {
        "triggered": alert_triggered,
        "trigger": trigger_text or (f"{_task_executor_target_label(target)} {label.lower()} failure" if failures else f"{_task_executor_target_label(target)} {label.lower()} summary"),
        "title": f"{task_name} {'failed' if failures else 'completed'}",
        "summary": combined_text[:1500],
        "details": {
            "executor_target": target,
            "workspace_dir": workspace_dir,
            "session_id": session_id,
            "label": label,
            "executor": {
                "command": command,
                "status": exec_result.get("stage_status"),
                "exit_code": exec_result.get("exit_code"),
                "output": (exec_result.get("stdout") or "")[:4000],
                "error": (exec_result.get("stderr") or "")[:2000],
            },
            "validation": {
                "command": validation_command,
                "status": (validation_result or {}).get("stage_status") or "",
                "exit_code": (validation_result or {}).get("exit_code"),
                "output": ((validation_result or {}).get("stdout") or "")[:4000],
                "error": ((validation_result or {}).get("stderr") or "")[:2000],
            },
            "test": {
                "command": test_command,
                "status": (test_result or {}).get("stage_status") or "",
                "exit_code": (test_result or {}).get("exit_code"),
                "output": ((test_result or {}).get("stdout") or "")[:4000],
                "error": ((test_result or {}).get("stderr") or "")[:2000],
            },
            "source": source,
        },
    }

    return {
        "ok": overall_ok,
        "status": "completed" if overall_ok else "failed",
        "text": combined_text,
        "error": "" if overall_ok else combined_text[:1500],
        "alert": _task_alert_from_result({"name": task_name, "trigger_mode": trigger_mode, "trigger_text": trigger_text}, json.dumps(payload, ensure_ascii=False)) if alert_triggered else None,
        "executor_target": target,
        "workspace_dir": workspace_dir,
        "sandbox_session_id": session_id,
        "validation_status": (validation_result or {}).get("stage_status") or "",
        "validation_excerpt": _task_sandbox_excerpt(validation_result),
        "test_status": (test_result or {}).get("stage_status") or "",
        "test_excerpt": _task_sandbox_excerpt(test_result),
        "summary_text": combined_text,
        "parsed": parsed_output,
    }


async def _task_execute_sandbox(task_row: dict, *, task_id: str, run_id: str, source: str) -> dict:
    target = _task_sandbox_target(task_row.get("executor_target") or "c12b")
    workspace_dir = _task_sandbox_workspace(task_row.get("workspace_dir"), target)
    prompt = (task_row.get("executor_prompt") or task_row.get("planner_prompt") or "").strip()
    return await _task_execute_sandbox_plan(
        task_id=task_id,
        run_id=run_id,
        source=source,
        target=target,
        workspace_dir=workspace_dir,
        command=prompt,
        validation_command=(task_row.get("validation_command") or "").strip(),
        test_command=(task_row.get("test_command") or "").strip(),
        task_name=task_row.get("name") or "Sandbox task",
        label="Sandbox",
        event_prefix="sandbox",
        trigger_mode=(task_row.get("trigger_mode") or "json").strip().lower(),
        trigger_text=(task_row.get("trigger_text") or "").strip(),
        alert_on_success=True,
    )


async def _task_execute_sandbox_assist(task_row: dict, *, task_id: str, run_id: str, source: str) -> dict | None:
    if not task_row.get("sandbox_assist") or not (task_row.get("sandbox_assist_command") or "").strip():
        return None
    target = _task_sandbox_target(task_row.get("sandbox_assist_target") or "c12b")
    workspace_dir = _task_sandbox_workspace(task_row.get("sandbox_assist_workspace_dir"), target)
    return await _task_execute_sandbox_plan(
        task_id=task_id,
        run_id=run_id,
        source=source,
        target=target,
        workspace_dir=workspace_dir,
        command=(task_row.get("sandbox_assist_command") or "").strip(),
        validation_command=(task_row.get("sandbox_assist_validation_command") or "").strip(),
        test_command=(task_row.get("sandbox_assist_test_command") or "").strip(),
        task_name=f"{task_row.get('name') or 'Task'} sandbox assist",
        label="Sandbox assist",
        event_prefix="sandbox-assist",
        trigger_mode="always",
        trigger_text="sandbox assist failure",
        alert_on_success=False,
    )


def _task_steps_for_task(task_row: dict) -> list[dict]:
    steps = _task_steps_fetch(str(task_row.get("id") or ""))
    if not steps:
        steps = _task_build_default_steps(task_row)
    normalized = []
    for idx, step in enumerate(steps, start=1):
        item = _task_normalize_step(str(task_row.get("id") or ""), step, idx)
        normalized.append(item)
    return normalized


def _task_context_from_history(task_row: dict, run_id: str) -> dict:
    context = {
        "task": {
            "id": task_row.get("id") or "",
            "name": task_row.get("name") or "",
            "mode": task_row.get("mode") or "",
            "schedule_kind": task_row.get("schedule_kind") or "",
            "interval_minutes": int(task_row.get("interval_minutes") or 0),
            "trigger_mode": task_row.get("trigger_mode") or "",
            "trigger_text": task_row.get("trigger_text") or "",
        },
        "steps": {},
        "feedback": {},
        "last_text": "",
        "alert_candidate": None,
        "condition_passed": None,
    }
    for result in _task_fetch_step_results(run_id):
        output = result.get("output") or {}
        if isinstance(output, dict) and isinstance(output.get("signals"), dict):
            output = {**output, **output.get("signals", {})}
        context["steps"][result.get("step_id") or ""] = output
        excerpt = str((result.get("output") or {}).get("text") or "")
        if excerpt:
            context["last_text"] = excerpt
    for feedback in _task_fetch_feedback(run_id):
        context["feedback"][feedback.get("step_id") or feedback.get("id") or ""] = feedback.get("payload") or {}
        summary = (feedback.get("summary") or "").strip()
        if summary:
            context["last_text"] = summary
    return context


def _task_context_value(context: dict, source: str, field: str) -> object:
    bucket: object
    if source in {"task", "feedback"}:
        bucket = context.get(source) or {}
    else:
        bucket = (context.get("steps") or {}).get(source) or (context.get("feedback") or {}).get(source) or {}
    current = bucket
    for part in [p for p in str(field or "").split(".") if p]:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _task_compare_rule(actual: object, comparator: str, expected: object) -> bool:
    comparator = (comparator or "eq").strip().lower()
    if comparator == "exists":
        return actual is not None and actual != "" and actual != [] and actual != {}
    if comparator == "contains":
        return str(expected or "").lower() in str(actual or "").lower()
    if comparator in {"gt", "gte", "lt", "lte"}:
        try:
            actual_num = float(actual)
            expected_num = float(expected)
        except Exception:
            return False
        if comparator == "gt":
            return actual_num > expected_num
        if comparator == "gte":
            return actual_num >= expected_num
        if comparator == "lt":
            return actual_num < expected_num
        return actual_num <= expected_num
    if comparator == "neq":
        return actual != expected
    return actual == expected


def _task_evaluate_condition_step(context: dict, step: dict) -> dict:
    config = _json_load_object(step.get("config"))
    operator = (config.get("operator") or "AND").strip().upper()
    rules = config.get("rules") if isinstance(config.get("rules"), list) else []
    if not rules:
        return {"matched": False, "details": [], "operator": operator}
    details = []
    matches = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        source = (rule.get("source") or "task").strip()
        field = (rule.get("field") or "").strip()
        comparator = (rule.get("comparator") or "eq").strip().lower()
        expected = rule.get("value")
        actual = _task_context_value(context, source, field)
        passed = _task_compare_rule(actual, comparator, expected)
        matches.append(passed)
        details.append({
            "source": source,
            "field": field,
            "comparator": comparator,
            "expected": expected,
            "actual": actual,
            "passed": passed,
        })
    matched = all(matches) if operator != "OR" else any(matches)
    return {"matched": matched, "details": details, "operator": operator}


def _task_resolve_next_step_id(steps: list[dict], current_index: int, step: dict, *, success: bool) -> str:
    explicit = (step.get("on_success_step_id") if success else step.get("on_failure_step_id")) or ""
    if explicit:
        return explicit
    next_index = current_index + 1
    return steps[next_index]["id"] if next_index < len(steps) else ""


def _task_step_index_map(steps: list[dict]) -> dict[str, int]:
    return {step["id"]: idx for idx, step in enumerate(steps)}


def _task_step_alert_payload(task_row: dict, step: dict, context: dict) -> dict:
    cfg = _json_load_object(step.get("config"))
    candidate = context.get("alert_candidate")
    alert = dict(candidate or {})
    text = (cfg.get("summary") or context.get("last_text") or task_row.get("last_result_excerpt") or "").strip()
    alert.setdefault("title", str(cfg.get("title") or task_row.get("name") or step.get("name") or "Task alert")[:160])
    alert.setdefault("trigger_text", str(cfg.get("trigger_text") or task_row.get("trigger_text") or step.get("name") or "task trigger")[:240])
    alert.setdefault("summary", text[:1500] or alert["title"])
    payload = _json_load_object(alert.get("payload_json"), {})
    payload.setdefault("step_id", step.get("id") or "")
    payload.setdefault("task_id", task_row.get("id") or "")
    alert["payload_json"] = json.dumps(payload, ensure_ascii=False)
    policy = _json_load_object(task_row.get("alert_policy"), _task_default_alert_policy())
    alert["severity"] = str(cfg.get("severity") or policy.get("severity") or "info")
    alert["repeat_every_minutes"] = int(cfg.get("repeat_every_minutes") or policy.get("repeat_every_minutes") or 0)
    repeat_key_template = str(cfg.get("dedupe_key") or policy.get("dedupe_key_template") or "").strip()
    if repeat_key_template:
        alert["repeat_key"] = repeat_key_template.format(task_id=task_row.get("id") or "", step_id=step.get("id") or "")
    return alert


def _task_update_run_tracking(run_id: str, **fields: object) -> None:
    if not fields:
        return
    cols = []
    values = []
    for key, value in fields.items():
        cols.append(f"{key}=?")
        values.append(value)
    values.append(run_id)
    try:
        with _db() as conn:
            conn.execute(f"UPDATE task_runs SET {', '.join(cols)} WHERE id=?", values)
    except sqlite3.Error:
        return


def _task_mark_terminal(task_row: dict, run_id: str, *, status: str, text: str = "", error_text: str = "", alert_id: int | None = None, current_step_id: str = "", terminal_reason: str = "", next_run_at: str | None = None) -> dict:
    finished_at = _iso_now()
    excerpt = (text or error_text or "No output")[:500]
    completion_policy = _json_load_object(task_row.get("completion_policy"), _task_default_completion_policy())
    archive_on_complete = bool(completion_policy.get("archive_on_complete")) and status == "completed"
    _task_update_run_tracking(
        run_id,
        finished_at=finished_at,
        completed_at=finished_at,
        status=status,
        output_excerpt=text[:2000],
        error_text=error_text[:1500],
        alert_id=alert_id,
        current_step_id=current_step_id,
        terminal_reason=terminal_reason[:240],
    )
    try:
        with _db() as conn:
            conn.execute(
                "UPDATE task_definitions SET updated_at=?, last_run_at=?, next_run_at=?, last_status=?, last_result_excerpt=?, archived_at=CASE WHEN ? THEN ? ELSE archived_at END, active=CASE WHEN ? THEN 0 ELSE active END WHERE id=?",
                (finished_at, finished_at, next_run_at, status, excerpt, 1 if archive_on_complete else 0, finished_at, 1 if archive_on_complete else 0, task_row.get("id")),
            )
    except sqlite3.Error:
        pass
    _record_task_event(
        str(task_row.get("id") or ""),
        "task-run-finished",
        text[:1500] if status == "completed" else (error_text or text or "Task run finished"),
        status=status,
        run_id=run_id,
        alert_id=alert_id,
    )
    return {
        "ok": status == "completed",
        "task_id": task_row.get("id"),
        "run_id": run_id,
        "status": status,
        "text": text,
        "error": error_text,
        "alert_id": alert_id,
        "current_step_id": current_step_id,
        "terminal_reason": terminal_reason,
    }


async def _task_execute_chat_step(task_row: dict, step: dict, context: dict) -> dict:
    prompt = str(_json_load_object(step.get("config")).get("prompt") or task_row.get("executor_prompt") or task_row.get("planner_prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "chat prompt required", "text": ""}
    return await _chat_one("c9-jokes-task", prompt, _urls()["c1"], chat_mode="deep", work_mode="work")


def _task_launch_url_for_step(task_row: dict, step: dict, run_id: str) -> str:
    config = _json_load_object(step.get("config"))
    prompt = str(config.get("prompt") or task_row.get("executor_prompt") or task_row.get("planner_prompt") or "").strip()
    mode = step.get("kind") or task_row.get("mode") or "agent"
    params = {"step_id": str(step.get("id") or "")}
    if mode == "agent":
        params["agent_id"] = str(config.get("agent_id") or "c6-kilocode")
    return _task_launch_url(mode, prompt, task_id=str(task_row.get("id") or ""), run_id=run_id, extra_params=params)


async def _task_resume_workflow(task_row: dict, run_id: str, *, source: str, start_step_id: str = "", parent_run_id: str = "", context: dict | None = None) -> dict:
    steps = _task_steps_for_task(task_row)
    step_index = _task_step_index_map(steps)
    next_run_at = _task_next_run_at(task_row.get("schedule_kind") or "manual", task_row.get("interval_minutes") or 0)
    if not steps:
        return _task_mark_terminal(task_row, run_id, status="failed", error_text="No workflow steps configured", terminal_reason="no-steps", next_run_at=next_run_at)
    context = context or _task_context_from_history(task_row, run_id)
    idx = 0
    if start_step_id and start_step_id in step_index:
        idx = step_index[start_step_id]
    while idx < len(steps):
        step = steps[idx]
        current_step_id = step.get("id") or ""
        _task_update_run_tracking(run_id, status="running", current_step_id=current_step_id)
        _record_task_event(str(task_row.get("id") or ""), "step-started", f"{step.get('name') or current_step_id} ({step.get('kind')}) started.", status="running", run_id=run_id)
        result_id = _task_insert_step_result(str(task_row.get("id") or ""), run_id, step, status="running")
        cfg = _json_load_object(step.get("config"))
        kind = step.get("kind")
        if kind == "trigger":
            out = {"schedule_kind": cfg.get("schedule_kind") or task_row.get("schedule_kind") or "manual", "interval_minutes": int(cfg.get("interval_minutes") or task_row.get("interval_minutes") or 0), "ts": _iso_now()}
            _task_finish_step_result(result_id, status="completed", output=out)
            context["steps"][current_step_id] = out
            idx += 1
            continue
        if kind == "condition":
            evaluated = _task_evaluate_condition_step(context, step)
            context["condition_passed"] = bool(evaluated.get("matched"))
            context["steps"][current_step_id] = evaluated
            _task_finish_step_result(result_id, status="completed" if evaluated.get("matched") else "skipped", output=evaluated)
            _record_task_event(str(task_row.get("id") or ""), "condition-evaluated", json.dumps(evaluated, ensure_ascii=False)[:1500], status="completed" if evaluated.get("matched") else "skipped", run_id=run_id)
            matched = bool(evaluated.get("matched"))
            if matched:
                next_step_id = (step.get("on_success_step_id") or "").strip() or _task_resolve_next_step_id(steps, idx, step, success=True)
            else:
                next_step_id = (step.get("on_failure_step_id") or "").strip()
            if not matched and not next_step_id:
                return _task_mark_terminal(task_row, run_id, status="completed", text="Condition was false; workflow finished without alert.", current_step_id=current_step_id, terminal_reason="condition-false", next_run_at=next_run_at)
            idx = step_index.get(next_step_id, idx + 1) if next_step_id else idx + 1
            continue
        if kind == "sandbox":
            sandbox_result = await _task_execute_sandbox_plan(
                task_id=str(task_row.get("id") or ""),
                run_id=run_id,
                source=source,
                target=_task_sandbox_target(cfg.get("executor_target") or task_row.get("executor_target") or "c12b"),
                workspace_dir=_task_sandbox_workspace(cfg.get("workspace_dir"), "c12b"),
                command=str(cfg.get("command") or task_row.get("executor_prompt") or "").strip(),
                validation_command=str(cfg.get("validation_command") or task_row.get("validation_command") or "").strip(),
                test_command=str(cfg.get("test_command") or task_row.get("test_command") or "").strip(),
                task_name=task_row.get("name") or "Sandbox task",
                label=step.get("name") or "Sandbox",
                event_prefix="sandbox",
                trigger_mode=(task_row.get("trigger_mode") or "json").strip().lower(),
                trigger_text=(task_row.get("trigger_text") or "").strip(),
                alert_on_success=False,
            )
            out = {
                "text": sandbox_result.get("text") or "",
                "ok": bool(sandbox_result.get("ok")),
                "executor_target": sandbox_result.get("executor_target") or "c12b",
                "workspace_dir": sandbox_result.get("workspace_dir") or "/workspace",
                "validation_status": sandbox_result.get("validation_status") or "",
                "test_status": sandbox_result.get("test_status") or "",
                "parsed": sandbox_result.get("parsed") or {},
            }
            context["steps"][current_step_id] = out
            context["last_text"] = out["text"]
            context["alert_candidate"] = sandbox_result.get("alert")
            _task_finish_step_result(result_id, status="completed" if sandbox_result.get("ok") else "failed", output=out, error_text=(sandbox_result.get("error") or ""))
            _task_update_run_tracking(
                run_id,
                sandbox_session_id=sandbox_result.get("sandbox_session_id") or "",
                validation_status=sandbox_result.get("validation_status") or "",
                validation_excerpt=(sandbox_result.get("validation_excerpt") or "")[:1500],
                test_status=sandbox_result.get("test_status") or "",
                test_excerpt=(sandbox_result.get("test_excerpt") or "")[:1500],
                output_excerpt=(sandbox_result.get("text") or "")[:2000],
                trigger_snapshot_json=json.dumps(context, ensure_ascii=False),
            )
            if not sandbox_result.get("ok"):
                alert_id = _insert_task_alert(str(task_row.get("id") or ""), run_id, sandbox_result.get("alert") or _task_step_alert_payload(task_row, step, context))
                return _task_mark_terminal(task_row, run_id, status="failed", text=sandbox_result.get("text") or "", error_text=sandbox_result.get("error") or "Sandbox step failed", alert_id=alert_id, current_step_id=current_step_id, terminal_reason="sandbox-failed", next_run_at=next_run_at)
            idx += 1
            continue
        if kind == "chat":
            chat_result = await _task_execute_chat_step(task_row, step, context)
            text = (chat_result.get("text") or "").strip()
            parsed = _task_parse_json_payload(text)
            out = {"text": text, "parsed": parsed or {}, "ok": bool(chat_result.get("ok") and text)}
            context["steps"][current_step_id] = out
            context["last_text"] = text
            context["alert_candidate"] = _task_alert_from_result(task_row, text)
            _task_finish_step_result(result_id, status="completed" if out["ok"] else "failed", output=out, error_text=(chat_result.get("error") or ""))
            _task_update_run_tracking(run_id, output_excerpt=text[:2000], trigger_snapshot_json=json.dumps(context, ensure_ascii=False))
            if not out["ok"]:
                return _task_mark_terminal(task_row, run_id, status="failed", text=text, error_text=chat_result.get("error") or "Chat step failed", current_step_id=current_step_id, terminal_reason="chat-failed", next_run_at=next_run_at)
            idx += 1
            continue
        if kind in {"agent", "multi-agent", "multi-agento"}:
            launch_url = _task_launch_url_for_step(task_row, step, run_id)
            out = {"launch_url": launch_url, "agent_id": str(cfg.get("agent_id") or "c6-kilocode"), "prompt": str(cfg.get("prompt") or "")}
            context["steps"][current_step_id] = out
            launch_text = launch_url
            if context.get("sandbox_assist"):
                launch_text = f"Sandbox assist completed. Launch required:\n{launch_url}"
            _task_finish_step_result(result_id, status="launch-pending", output=out)
            _task_update_run_tracking(
                run_id,
                status="launch-pending",
                launch_url=launch_url,
                current_step_id=current_step_id,
                trigger_snapshot_json=json.dumps(context, ensure_ascii=False),
            )
            try:
                with _db() as conn:
                    conn.execute(
                        "UPDATE task_definitions SET updated_at=?, last_run_at=?, next_run_at=?, last_status=?, last_result_excerpt=? WHERE id=?",
                        (_iso_now(), _iso_now(), next_run_at, "launch-pending", launch_url[:500], task_row.get("id")),
                    )
            except sqlite3.Error:
                pass
            _record_task_event(str(task_row.get("id") or ""), "agent-launch", f"{step.get('kind')} launched. {launch_url}", status="launch-pending", run_id=run_id)
            return {
                "ok": True,
                "task_id": task_row.get("id"),
                "run_id": run_id,
                "status": "launch-pending",
                "text": launch_text,
                "launch_url": launch_url,
                "background_supported": False,
                "current_step_id": current_step_id,
            }
        if kind == "alert":
            alert = _task_step_alert_payload(task_row, step, context)
            if context.get("condition_passed") is False and not alert.get("summary"):
                _task_finish_step_result(result_id, status="skipped", output={"skipped": True})
                idx += 1
                continue
            alert_id = _insert_task_alert(str(task_row.get("id") or ""), run_id, alert)
            out = {"alert_id": alert_id, "title": alert.get("title") or "", "severity": alert.get("severity") or "info"}
            context["steps"][current_step_id] = out
            _task_finish_step_result(result_id, status="completed", output=out)
            _record_task_event(str(task_row.get("id") or ""), "alert-created", (alert.get("summary") or alert.get("title") or "Alert created")[:1500], status="alert-open", run_id=run_id, alert_id=alert_id)
            idx += 1
            continue
        if kind == "complete":
            out = {"completed": True, "summary": context.get("last_text") or "Workflow completed"}
            context["steps"][current_step_id] = out
            _task_finish_step_result(result_id, status="completed", output=out)
            latest_alerts = []
            try:
                with _db() as conn:
                    rows = conn.execute("SELECT * FROM task_alerts WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchall()
                latest_alerts = [_task_alert_to_dict(r) for r in rows]
            except sqlite3.Error:
                latest_alerts = []
            return _task_mark_terminal(task_row, run_id, status="completed", text=context.get("last_text") or "Workflow completed", alert_id=(latest_alerts[0]["id"] if latest_alerts else None), current_step_id=current_step_id, terminal_reason="workflow-complete", next_run_at=next_run_at)
        _task_finish_step_result(result_id, status="failed", output={}, error_text=f"Unsupported step kind: {kind}")
        return _task_mark_terminal(task_row, run_id, status="failed", error_text=f"Unsupported step kind: {kind}", current_step_id=current_step_id, terminal_reason="unsupported-step", next_run_at=next_run_at)
    return _task_mark_terminal(task_row, run_id, status="completed", text=context.get("last_text") or "Workflow completed", current_step_id=steps[-1]["id"], terminal_reason="workflow-complete", next_run_at=next_run_at)


def _task_claim(task_id: str, run_id: str, *, source: str, ttl_seconds: int = 900) -> bool:
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=max(60, ttl_seconds))).isoformat()
    try:
        with _db() as conn:
            conn.execute("DELETE FROM task_run_claims WHERE expires_at<=?", (now.isoformat(),))
            conn.execute(
                "INSERT INTO task_run_claims (task_id, run_id, owner_id, source, claimed_at, expires_at) VALUES (?,?,?,?,?,?)",
                (task_id, run_id, _task_scheduler_owner, source, now.isoformat(), expires_at),
            )
        return True
    except sqlite3.IntegrityError:
        return False
    except sqlite3.Error:
        return False


def _task_release_claim(task_id: str, run_id: str = "") -> None:
    try:
        with _db() as conn:
            if run_id:
                conn.execute("DELETE FROM task_run_claims WHERE task_id=? AND run_id=?", (task_id, run_id))
            else:
                conn.execute("DELETE FROM task_run_claims WHERE task_id=?", (task_id,))
    except sqlite3.Error:
        return


def _task_template_upsert(payload: dict, *, template_key: str = "") -> dict:
    now = _iso_now()
    key = (template_key or payload.get("key") or "").strip() or _slugify(payload.get("name") or "template", prefix="template")
    mode = (payload.get("mode") or "chat").strip().lower()
    executor_target = _task_sandbox_target(payload.get("executor_target") or ("c12b" if mode == "sandbox" else ""))
    sandbox_assist = _task_sandbox_assist_values(payload, mode=mode)
    row_payload = {
        "key": key,
        "name": (payload.get("name") or "").strip(),
        "description": (payload.get("description") or payload.get("notes") or "").strip(),
        "mode": mode,
        "schedule_kind": (payload.get("schedule_kind") or "manual").strip().lower(),
        "interval_minutes": max(0, int(payload.get("interval_minutes") or 0)),
        "tabs_required": max(1, min(12, int(payload.get("tabs_required") or 1))),
        "executor_target": executor_target if mode == "sandbox" else "",
        "workspace_dir": _task_sandbox_workspace(payload.get("workspace_dir"), executor_target) if mode == "sandbox" else "",
        "planner_prompt": (payload.get("planner_prompt") or "").strip(),
        "executor_prompt": (payload.get("executor_prompt") or "").strip(),
        "validation_command": (payload.get("validation_command") or "").strip() if mode == "sandbox" else "",
        "test_command": (payload.get("test_command") or "").strip() if mode == "sandbox" else "",
        **sandbox_assist,
        "context_handoff": (payload.get("context_handoff") or "").strip(),
        "trigger_mode": (payload.get("trigger_mode") or "json").strip().lower(),
        "trigger_text": (payload.get("trigger_text") or "").strip(),
        "active": 1 if payload.get("active", True) else 0,
        "source": (payload.get("source") or "user").strip().lower() or "user",
    }
    if not row_payload["name"]:
        return {"ok": False, "error": "template name required"}
    if row_payload["sandbox_assist"] and not row_payload["sandbox_assist_command"]:
        return {"ok": False, "error": "sandbox_assist_command required when AIO sandbox assist is enabled"}
    with _db() as conn:
        existing = conn.execute("SELECT key, created_at FROM task_templates WHERE key=?", (key,)).fetchone()
        created_at = existing["created_at"] if existing else now
        if existing:
            conn.execute(
                "UPDATE task_templates SET updated_at=?, name=?, description=?, mode=?, schedule_kind=?, interval_minutes=?, tabs_required=?, "
                "executor_target=?, workspace_dir=?, planner_prompt=?, executor_prompt=?, validation_command=?, test_command=?, "
                "sandbox_assist=?, sandbox_assist_target=?, sandbox_assist_workspace_dir=?, sandbox_assist_command=?, "
                "sandbox_assist_validation_command=?, sandbox_assist_test_command=?, context_handoff=?, trigger_mode=?, trigger_text=?, active=?, source=? WHERE key=?",
                (
                    now, row_payload["name"], row_payload["description"], row_payload["mode"], row_payload["schedule_kind"],
                    row_payload["interval_minutes"], row_payload["tabs_required"], row_payload["executor_target"], row_payload["workspace_dir"],
                    row_payload["planner_prompt"], row_payload["executor_prompt"], row_payload["validation_command"], row_payload["test_command"],
                    1 if row_payload["sandbox_assist"] else 0, row_payload["sandbox_assist_target"], row_payload["sandbox_assist_workspace_dir"],
                    row_payload["sandbox_assist_command"], row_payload["sandbox_assist_validation_command"], row_payload["sandbox_assist_test_command"],
                    row_payload["context_handoff"], row_payload["trigger_mode"], row_payload["trigger_text"], row_payload["active"],
                    row_payload["source"], key,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO task_templates (key, created_at, updated_at, name, description, mode, schedule_kind, interval_minutes, tabs_required, "
                "executor_target, workspace_dir, planner_prompt, executor_prompt, validation_command, test_command, sandbox_assist, "
                "sandbox_assist_target, sandbox_assist_workspace_dir, sandbox_assist_command, sandbox_assist_validation_command, "
                "sandbox_assist_test_command, context_handoff, trigger_mode, trigger_text, active, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key, created_at, now, row_payload["name"], row_payload["description"], row_payload["mode"],
                    row_payload["schedule_kind"], row_payload["interval_minutes"], row_payload["tabs_required"], row_payload["executor_target"],
                    row_payload["workspace_dir"], row_payload["planner_prompt"], row_payload["executor_prompt"],
                    row_payload["validation_command"], row_payload["test_command"], 1 if row_payload["sandbox_assist"] else 0,
                    row_payload["sandbox_assist_target"], row_payload["sandbox_assist_workspace_dir"], row_payload["sandbox_assist_command"],
                    row_payload["sandbox_assist_validation_command"], row_payload["sandbox_assist_test_command"], row_payload["context_handoff"],
                    row_payload["trigger_mode"], row_payload["trigger_text"], row_payload["active"], row_payload["source"],
                ),
            )
        row = conn.execute("SELECT * FROM task_templates WHERE key=?", (key,)).fetchone()
    return {"ok": True, "template": _task_template_row_to_dict(row)}


async def _execute_task_record(task_id: str, *, source: str = "manual") -> dict:
    lock = _get_task_runner_lock()
    async with lock:
        if task_id in _task_runner_ids:
            return {"ok": False, "error": "Task is already running", "task_id": task_id}
        _task_runner_ids.add(task_id)

    try:
        with _db() as conn:
            row = conn.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Task not found", "task_id": task_id}

        task_row = _task_row_to_dict(row)
        run_id = "trun_" + uuid.uuid4().hex[:8]
        if not _task_claim(task_id, run_id, source=source):
            return {"ok": False, "error": "Task is already running", "task_id": task_id}
        created_at = _iso_now()
        run_executor_target = (
            task_row.get("executor_target")
            or (task_row.get("sandbox_assist_target") if task_row.get("sandbox_assist") else "")
            or ""
        )

        with _db() as conn:
            conn.execute(
                "INSERT INTO task_runs (id, task_id, created_at, started_at, source, status, mode, executor_target, trigger_snapshot_json, parent_run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    task_id,
                    created_at,
                    created_at,
                    source,
                    "running",
                    task_row.get("mode") or "chat",
                    run_executor_target,
                    json.dumps({"source": source}, ensure_ascii=False),
                    "",
                ),
            )
            conn.execute(
                "UPDATE task_definitions SET updated_at=?, last_status=? WHERE id=?",
                (created_at, "running", task_id),
            )
        _record_task_event(
            task_id,
            "task-run-started",
            f"Task orchestration started a {task_row.get('mode') or 'chat'} run via {source}.",
            status="running",
            run_id=run_id,
        )
        context = _task_context_from_history(task_row, run_id)
        if task_row.get("mode") != "sandbox" and task_row.get("sandbox_assist"):
            assist_result = await _task_execute_sandbox_assist(task_row, task_id=task_id, run_id=run_id, source=source)
            if assist_result:
                context["sandbox_assist"] = {
                    "target": assist_result.get("executor_target") or "c12b",
                    "workspace_dir": assist_result.get("workspace_dir") or "/workspace",
                    "text": assist_result.get("text") or "",
                    "ok": bool(assist_result.get("ok")),
                }
                context["last_text"] = assist_result.get("text") or context.get("last_text") or ""
                _task_update_run_tracking(
                    run_id,
                    sandbox_session_id=assist_result.get("sandbox_session_id") or "",
                    validation_status=assist_result.get("validation_status") or "",
                    validation_excerpt=(assist_result.get("validation_excerpt") or "")[:1500],
                    test_status=assist_result.get("test_status") or "",
                    test_excerpt=(assist_result.get("test_excerpt") or "")[:1500],
                    output_excerpt=(assist_result.get("text") or "")[:2000],
                    trigger_snapshot_json=json.dumps(context, ensure_ascii=False),
                )
                if not assist_result.get("ok"):
                    alert = assist_result.get("alert") or {
                        "title": f"{task_row.get('name') or 'Task'} sandbox assist failed",
                        "trigger_text": "sandbox assist failure",
                        "summary": (assist_result.get("error") or assist_result.get("text") or "Sandbox assist failed")[:1500],
                        "payload_json": json.dumps({"assistant": "sandbox", "task_id": task_id}, ensure_ascii=False),
                        "severity": task_row.get("alert_policy", {}).get("severity") or "error",
                    }
                    alert_id = _insert_task_alert(task_id, run_id, alert)
                    return _task_mark_terminal(
                        task_row,
                        run_id,
                        status="failed",
                        text=assist_result.get("text") or "",
                        error_text=assist_result.get("error") or "Sandbox assist failed",
                        alert_id=alert_id,
                        terminal_reason="sandbox-assist-failed",
                        next_run_at=_task_next_run_at(task_row.get("schedule_kind") or "manual", task_row.get("interval_minutes") or 0),
                    )
        return await _task_resume_workflow(task_row, run_id, source=source, context=context)
    finally:
        _task_release_claim(task_id, run_id if "run_id" in locals() else "")
        async with _get_task_runner_lock():
            _task_runner_ids.discard(task_id)


async def _run_due_tasks_once() -> None:
    now = _iso_now()
    try:
        with _db() as conn:
            conn.execute("DELETE FROM task_run_claims WHERE expires_at<=?", (now,))
            rows = conn.execute(
                "SELECT id FROM task_definitions WHERE active=1 AND mode IN ('chat','sandbox') AND schedule_kind IN ('recurring','continuous') "
                "AND next_run_at IS NOT NULL AND next_run_at<>'' AND next_run_at<=? ORDER BY next_run_at ASC LIMIT 4",
                (now,),
            ).fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        await _execute_task_record(row["id"], source="scheduler")


async def _task_scheduler_loop() -> None:
    while True:
        try:
            await _run_due_tasks_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(TASK_SCHEDULER_INTERVAL_S)


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _task_scheduler_task
    _ensure_db()
    _task_scheduler_task = asyncio.create_task(_task_scheduler_loop())
    yield
    if _task_scheduler_task:
        _task_scheduler_task.cancel()
        try:
            await _task_scheduler_task
        except asyncio.CancelledError:
            pass
        _task_scheduler_task = None
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


@app.get("/c3-auth", response_class=HTMLResponse, name="page_c3_auth")
async def page_c3_auth(request: Request):
    return templates.TemplateResponse(request, "c3_auth.html", {})


@app.get("/task", response_class=HTMLResponse, include_in_schema=False)
async def page_task_legacy(request: Request):
    return RedirectResponse(url="/tasked", status_code=307)


@app.get("/tasked", response_class=HTMLResponse, name="page_tasked")
async def page_tasked(request: Request):
    return templates.TemplateResponse(request, "tasked.html", {
        "task_modes": TASK_MODE_OPTIONS,
        "task_executor_targets": json.dumps(TASK_EXECUTOR_TARGET_OPTIONS, ensure_ascii=False),
        "task_step_kinds": json.dumps(TASK_WORKFLOW_STEP_KINDS, ensure_ascii=False),
        "task_agent_targets": json.dumps(TASK_AGENT_TARGET_OPTIONS, ensure_ascii=False),
        "task_templates": json.dumps(_task_templates_payload(), ensure_ascii=False),
        "tasked_author_examples": json.dumps(_tasked_author_examples_payload(), ensure_ascii=False),
    })


@app.get("/alerts", response_class=HTMLResponse, name="page_alerts")
async def page_alerts(request: Request):
    return templates.TemplateResponse(request, "alerts.html", {
        "task_modes": json.dumps(TASK_MODE_OPTIONS, ensure_ascii=False),
        "task_executor_targets": json.dumps(TASK_EXECUTOR_TARGET_OPTIONS, ensure_ascii=False),
        "task_agent_targets": json.dumps(TASK_AGENT_TARGET_OPTIONS, ensure_ascii=False),
        "task_templates": json.dumps(_task_templates_payload(), ensure_ascii=False),
    })


@app.get("/task-completed", response_class=HTMLResponse, name="page_task_completed")
async def page_task_completed(request: Request):
    return templates.TemplateResponse(request, "task_completed.html", {
        "task_modes": json.dumps(TASK_MODE_OPTIONS, ensure_ascii=False),
        "task_executor_targets": json.dumps(TASK_EXECUTOR_TARGET_OPTIONS, ensure_ascii=False),
        "task_agent_targets": json.dumps(TASK_AGENT_TARGET_OPTIONS, ensure_ascii=False),
    })


@app.get("/piplinetask", response_class=HTMLResponse, name="page_piplinetask")
async def page_piplinetask(request: Request):
    return templates.TemplateResponse(request, "piplinetask.html", {
        "task_modes": json.dumps(TASK_MODE_OPTIONS, ensure_ascii=False),
        "task_executor_targets": json.dumps(TASK_EXECUTOR_TARGET_OPTIONS, ensure_ascii=False),
        "task_agent_targets": json.dumps(TASK_AGENT_TARGET_OPTIONS, ensure_ascii=False),
        "task_step_kinds": json.dumps(TASK_WORKFLOW_STEP_KINDS, ensure_ascii=False),
        "task_templates": json.dumps(_task_templates_payload(), ensure_ascii=False),
    })


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
async def page_agent(
    request: Request,
    task: str = "",
    task_id: str = "",
    task_run_id: str = "",
    source: str = "",
    agent_id: str = "",
    step_id: str = "",
):
    """AI Agent Workspace — IDE-like agentic task execution via C10 sandbox."""
    return templates.TemplateResponse(request, "agent.html", {
        "agents": AGENTS,
        "c10_url": C10_URL,
        "task_launch": {
            "task": task,
            "task_id": task_id,
            "task_run_id": task_run_id,
            "source": source,
            "agent_id": agent_id,
            "step_id": step_id,
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# JSON API ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/session-health", name="api_session_health")
async def api_session_health():
    """Proxy C3's /session-health endpoint; used by the LED indicator on all pages."""
    c3_url = _urls().get("c3", "http://browser-auth:8001")
    client = _get_http()
    probe = await _probe_session_health(client, c3_url, timeout=5)
    body = probe.get("body") if isinstance(probe.get("body"), dict) else {}
    if "checked_at" not in body:
        body["checked_at"] = datetime.now(timezone.utc).isoformat()
    return JSONResponse(body, status_code=probe.get("http_status") or 503)


@app.get("/api/c3-auth-progress", name="api_c3_auth_progress")
async def api_c3_auth_progress():
    """Proxy C3's Tab 1 auth-progress snapshot plus C3/session/runtime context."""
    urls = _urls()
    c3_url = urls.get("c3", "http://browser-auth:8001")
    client = _get_http()
    progress_body: dict = {
        "active": False,
        "result": None,
        "error": "C3 auth progress unavailable",
        "current_step_id": None,
        "steps": [],
    }
    progress_ok = False
    progress_status = 503
    try:
        progress_resp = await client.get(f"{c3_url}/auth-progress", timeout=5)
        progress_status = progress_resp.status_code
        ct = progress_resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            payload = progress_resp.json()
            if isinstance(payload, dict):
                progress_body = payload
        progress_ok = progress_resp.status_code < 400
    except Exception as exc:
        progress_body["error"] = str(exc)

    session_probe = await _probe_session_health(client, c3_url, timeout=5)
    c3_status_probe = await _probe_health(client, "C3 /status", c3_url, "/status")
    runtime = await _get_runtime_status_snapshot(client=client)
    return JSONResponse({
        "ok": progress_ok,
        "http_status": progress_status,
        "progress": progress_body,
        "session": session_probe.get("body") if isinstance(session_probe.get("body"), dict) else {},
        "c3_status": c3_status_probe.get("body") if isinstance(c3_status_probe.get("body"), dict) else {},
        "runtime": runtime,
    })


@app.post("/api/c3-auth-progress/run", name="api_c3_auth_progress_run")
async def api_c3_auth_progress_run():
    """Start a live Tab 1 validate-auth run on C3; the UI polls /api/c3-auth-progress while this runs."""
    urls = _urls()
    c3_url = urls.get("c3", "http://browser-auth:8001")
    client = _get_http()
    try:
        resp = await client.post(f"{c3_url}/validate-auth", timeout=130)
        ct = resp.headers.get("content-type", "")
        body = resp.json() if ct.startswith("application/json") else {"validated": False, "error": resp.text}
        if not isinstance(body, dict):
            body = {"validated": False, "error": "Unexpected C3 validate-auth response"}
        return JSONResponse(body, status_code=resp.status_code)
    except Exception as exc:
        return JSONResponse({"validated": False, "error": str(exc)}, status_code=503)


@app.get("/api/tasks", name="api_tasks")
async def api_tasks(include_archived: bool = False):
    try:
        with _db() as conn:
            if include_archived:
                rows = conn.execute("SELECT * FROM task_definitions ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM task_definitions WHERE archived_at IS NULL OR archived_at='' ORDER BY created_at DESC").fetchall()
            tasks = []
            for row in rows:
                task = _task_row_to_dict(row)
                latest_run_row = conn.execute(
                    "SELECT * FROM task_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                    (task["id"],),
                ).fetchone()
                latest_alert_row = conn.execute(
                    "SELECT a.*, t.mode AS task_mode, t.template_key AS template_key, t.schedule_kind AS schedule_kind, "
                    "t.interval_minutes AS interval_minutes, t.tabs_required AS tabs_required, t.active AS active, "
                    "t.executor_target AS executor_target, t.workspace_dir AS workspace_dir, "
                    "t.sandbox_assist AS sandbox_assist, t.sandbox_assist_target AS sandbox_assist_target, "
                    "t.sandbox_assist_workspace_dir AS sandbox_assist_workspace_dir "
                    "FROM task_alerts a LEFT JOIN task_definitions t ON t.id=a.task_id "
                    "WHERE a.task_id=? ORDER BY a.created_at DESC LIMIT 1",
                    (task["id"],),
                ).fetchone()
                step_rows = conn.execute(
                    "SELECT * FROM task_workflow_steps WHERE task_id=? AND active=1 ORDER BY position ASC, created_at ASC",
                    (task["id"],),
                ).fetchall()
                latest_run = _task_run_to_dict(latest_run_row) if latest_run_row else None
                latest_alert = _task_alert_to_dict(latest_alert_row) if latest_alert_row else None
                task["steps"] = [_task_step_to_dict(step_row) for step_row in step_rows] or _task_build_default_steps(task)
                task["current_step_id"] = (latest_run or {}).get("current_step_id") or ""
                task["latest_run"] = latest_run
                task["latest_alert"] = latest_alert
                task["trace"] = _task_trace_payload(task, latest_run, latest_alert)
                tasks.append(task)
        return JSONResponse({
            "ok": True,
            "tasks": tasks,
            "templates": _task_templates_payload(),
        })
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc), "tasks": [], "templates": _task_templates_payload()}, status_code=500)


@app.get("/api/task-templates", name="api_task_templates")
async def api_task_templates(include_archived: bool = False):
    templates = _task_templates_payload(active_only=not include_archived)
    return JSONResponse({"ok": True, "templates": templates})


@app.post("/api/task-templates", name="api_task_templates_upsert")
async def api_task_templates_upsert(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = _task_template_upsert(body, template_key=(body.get("key") or "").strip())
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    except (sqlite3.Error, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/task-templates/{template_key}/clone", name="api_task_template_clone")
async def api_task_template_clone(template_key: str):
    try:
        with _db() as conn:
            row = conn.execute("SELECT * FROM task_templates WHERE key=?", (template_key,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "Template not found"}, status_code=404)
        source = _task_template_row_to_dict(row)
        clone_key = _slugify(f"{source['key']}-clone", prefix="template")
        result = _task_template_upsert({
            **source,
            "key": clone_key,
            "name": f"{source['name']} (Clone)",
            "source": "user",
            "active": True,
        }, template_key=clone_key)
        if result.get("ok"):
            result["source_template_key"] = template_key
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/task-templates/{template_key}/archive", name="api_task_template_archive")
async def api_task_template_archive(template_key: str):
    try:
        with _db() as conn:
            cur = conn.execute(
                "UPDATE task_templates SET active=0, updated_at=? WHERE key=?",
                (_iso_now(), template_key),
            )
            if cur.rowcount <= 0:
                return JSONResponse({"ok": False, "error": "Template not found"}, status_code=404)
            row = conn.execute("SELECT * FROM task_templates WHERE key=?", (template_key,)).fetchone()
        return JSONResponse({"ok": True, "template": _task_template_row_to_dict(row)})
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/tasks/seed-examples", name="api_tasks_seed_examples")
async def api_tasks_seed_examples():
    try:
        result = _seed_tasked_examples()
        return JSONResponse(result)
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/tasks/draft-from-text", name="api_tasks_draft_from_text")
async def api_tasks_draft_from_text(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    prompt = re.sub(r"\s+", " ", str(body.get("prompt") or "").strip())
    strategy = (body.get("strategy") or "auto").strip().lower()
    mode_hint = (body.get("mode_hint") or "").strip().lower()
    template_key = (body.get("template_key") or "").strip()
    if not prompt:
        return JSONResponse({"ok": False, "error": "prompt required"}, status_code=400)
    result = await _tasked_author_draft_from_text(
        prompt,
        strategy=strategy,
        mode_hint=mode_hint,
        template_key=template_key,
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/tasks", name="api_tasks_upsert")
async def api_tasks_upsert(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    now = _iso_now()
    task_id = (body.get("id") or "").strip() or ("task_" + uuid.uuid4().hex[:8])
    name = (body.get("name") or "").strip()
    mode = (body.get("mode") or "chat").strip().lower()
    schedule_kind = (body.get("schedule_kind") or "manual").strip().lower()
    template_key = (body.get("template_key") or "").strip()
    executor_target = _task_sandbox_target(body.get("executor_target") or ("c12b" if mode == "sandbox" else ""))
    workspace_dir = _task_sandbox_workspace(body.get("workspace_dir"), executor_target) if mode == "sandbox" else ""
    sandbox_assist = _task_sandbox_assist_values(body, mode=mode)
    planner_prompt = (body.get("planner_prompt") or "").strip()
    executor_prompt = (body.get("executor_prompt") or "").strip()
    validation_command = (body.get("validation_command") or "").strip() if mode == "sandbox" else ""
    test_command = (body.get("test_command") or "").strip() if mode == "sandbox" else ""
    context_handoff = (body.get("context_handoff") or "").strip()
    trigger_mode = (body.get("trigger_mode") or "json").strip().lower()
    trigger_text = (body.get("trigger_text") or "").strip()
    notes = (body.get("notes") or "").strip()
    steps_payload = body.get("steps") if isinstance(body.get("steps"), list) else []
    alert_policy = {**_task_default_alert_policy(), **_json_load_object(body.get("alert_policy"))}
    completion_policy = {**_task_default_completion_policy(), **_json_load_object(body.get("completion_policy"))}
    try:
        interval_minutes = max(0, int(body.get("interval_minutes") or 0))
    except Exception:
        interval_minutes = 0
    try:
        tabs_required = max(1, min(12, int(body.get("tabs_required") or 1)))
    except Exception:
        tabs_required = 1
    active = 1 if body.get("active", True) else 0

    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    if mode not in {m["id"] for m in TASK_MODE_OPTIONS}:
        return JSONResponse({"ok": False, "error": "invalid mode"}, status_code=400)
    if schedule_kind not in {"manual", "recurring", "continuous"}:
        return JSONResponse({"ok": False, "error": "invalid schedule_kind"}, status_code=400)
    if schedule_kind in {"recurring", "continuous"} and interval_minutes <= 0:
        return JSONResponse({"ok": False, "error": "interval_minutes must be > 0 for repeating or live tasks"}, status_code=400)
    if sandbox_assist["sandbox_assist"] and not sandbox_assist["sandbox_assist_command"]:
        return JSONResponse({"ok": False, "error": "sandbox_assist_command required when AIO sandbox assist is enabled"}, status_code=400)
    try:
        steps_to_save = steps_payload or _task_build_default_steps({
            "id": task_id,
            "name": name,
            "mode": mode,
            "schedule_kind": schedule_kind,
            "interval_minutes": interval_minutes,
            "executor_target": executor_target,
            "workspace_dir": workspace_dir,
            "executor_prompt": executor_prompt,
            "planner_prompt": planner_prompt,
            "validation_command": validation_command,
            "test_command": test_command,
            "trigger_mode": trigger_mode,
            "trigger_text": trigger_text,
            **sandbox_assist,
        })
        needs_rebase = any(
            isinstance(item, dict) and (
                str(item.get("id") or "").startswith("task_draft")
                or (item.get("task_id") and str(item.get("task_id") or "") not in {"", task_id})
            )
            for item in steps_to_save
        )
        normalized_steps = _task_clone_steps(task_id, steps_to_save) if needs_rebase else [_task_normalize_step(task_id, item, idx + 1) for idx, item in enumerate(steps_to_save)]
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    next_run_at = _task_next_run_at(schedule_kind, interval_minutes) if active else None
    try:
        with _db() as conn:
            existing = conn.execute("SELECT id FROM task_definitions WHERE id=?", (task_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE task_definitions SET updated_at=?, name=?, mode=?, schedule_kind=?, interval_minutes=?, active=?, tabs_required=?, "
                    "template_key=?, executor_target=?, workspace_dir=?, planner_prompt=?, executor_prompt=?, validation_command=?, test_command=?, "
                    "sandbox_assist=?, sandbox_assist_target=?, sandbox_assist_workspace_dir=?, sandbox_assist_command=?, "
                    "sandbox_assist_validation_command=?, sandbox_assist_test_command=?, context_handoff=?, trigger_mode=?, trigger_text=?, notes=?, "
                    "next_run_at=CASE WHEN ?=1 THEN ? ELSE NULL END, completion_policy_json=?, alert_policy_json=?, workflow_version=?, archived_at=NULL WHERE id=?",
                    (
                        now, name, mode, schedule_kind, interval_minutes, active, tabs_required,
                        template_key, executor_target if mode == "sandbox" else "", workspace_dir, planner_prompt, executor_prompt,
                        validation_command, test_command, 1 if sandbox_assist["sandbox_assist"] else 0, sandbox_assist["sandbox_assist_target"],
                        sandbox_assist["sandbox_assist_workspace_dir"], sandbox_assist["sandbox_assist_command"],
                        sandbox_assist["sandbox_assist_validation_command"], sandbox_assist["sandbox_assist_test_command"],
                        context_handoff, trigger_mode, trigger_text, notes,
                        active, next_run_at, json.dumps(completion_policy, ensure_ascii=False), json.dumps(alert_policy, ensure_ascii=False), 1, task_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO task_definitions (id, created_at, updated_at, name, mode, schedule_kind, interval_minutes, active, tabs_required, "
                    "template_key, executor_target, workspace_dir, planner_prompt, executor_prompt, validation_command, test_command, "
                    "sandbox_assist, sandbox_assist_target, sandbox_assist_workspace_dir, sandbox_assist_command, sandbox_assist_validation_command, "
                    "sandbox_assist_test_command, context_handoff, trigger_mode, trigger_text, notes, next_run_at, completion_policy_json, alert_policy_json, workflow_version) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id, now, now, name, mode, schedule_kind, interval_minutes, active, tabs_required,
                        template_key, executor_target if mode == "sandbox" else "", workspace_dir, planner_prompt, executor_prompt,
                        validation_command, test_command, 1 if sandbox_assist["sandbox_assist"] else 0, sandbox_assist["sandbox_assist_target"],
                        sandbox_assist["sandbox_assist_workspace_dir"], sandbox_assist["sandbox_assist_command"],
                        sandbox_assist["sandbox_assist_validation_command"], sandbox_assist["sandbox_assist_test_command"],
                        context_handoff, trigger_mode, trigger_text, notes, next_run_at,
                        json.dumps(completion_policy, ensure_ascii=False), json.dumps(alert_policy, ensure_ascii=False), 1,
                    ),
                )
            _task_save_steps(conn, task_id, normalized_steps)
            row = conn.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone()
        task = _task_row_to_dict(row)
        task["steps"] = normalized_steps
        _record_task_event(
            task_id,
            "task-edited" if existing else "task-created",
            f"Task orchestration saved the definition. Mode={mode} · Schedule={task.get('schedule_label')} · Tabs={tabs_required}.",
            status=task.get("lifecycle_state") or task.get("last_status") or "idle",
        )
        return JSONResponse({"ok": True, "task": task})
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/task-runs", name="api_task_runs")
async def api_task_runs(task_id: str = "", limit: int = 30):
    limit = max(1, min(100, limit))
    try:
        with _db() as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM task_runs WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM task_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return JSONResponse({"ok": True, "runs": [_task_run_to_dict(r) for r in rows]})
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc), "runs": []}, status_code=500)


def _task_fetch_row(task_id: str) -> sqlite3.Row | None:
    with _db() as conn:
        return conn.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone()


def _task_state_response(task_id: str) -> dict | None:
    try:
        with _db() as conn:
            task_row = conn.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone()
            if not task_row:
                return None
            latest_run_row = conn.execute(
                "SELECT * FROM task_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            latest_alert_row = conn.execute(
                "SELECT a.*, t.mode AS task_mode, t.template_key AS template_key, t.schedule_kind AS schedule_kind, "
                "t.interval_minutes AS interval_minutes, t.tabs_required AS tabs_required, t.active AS active, "
                "t.executor_target AS executor_target, t.workspace_dir AS workspace_dir, "
                "t.sandbox_assist AS sandbox_assist, t.sandbox_assist_target AS sandbox_assist_target, "
                "t.sandbox_assist_workspace_dir AS sandbox_assist_workspace_dir "
                "FROM task_alerts a LEFT JOIN task_definitions t ON t.id=a.task_id "
                "WHERE a.task_id=? ORDER BY a.created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            step_rows = conn.execute(
                "SELECT * FROM task_workflow_steps WHERE task_id=? AND active=1 ORDER BY position ASC, created_at ASC",
                (task_id,),
            ).fetchall()
        task = _task_row_to_dict(task_row)
        latest_run = _task_run_to_dict(latest_run_row) if latest_run_row else None
        latest_alert = _task_alert_to_dict(latest_alert_row) if latest_alert_row else None
        task["steps"] = [_task_step_to_dict(step_row) for step_row in step_rows] or _task_build_default_steps(task)
        task["current_step_id"] = (latest_run or {}).get("current_step_id") or ""
        task["latest_run"] = latest_run
        task["latest_alert"] = latest_alert
        task["trace"] = _task_trace_payload(task, latest_run, latest_alert)
        return task
    except sqlite3.Error:
        return None


def _task_update_activation(task_id: str, *, active: bool, last_status: str, event_type: str, detail: str) -> dict:
    now = _iso_now()
    row = _task_fetch_row(task_id)
    if not row:
        return {"ok": False, "error": "Task not found", "task_id": task_id}
    task = _task_row_to_dict(row)
    next_run_at = _task_next_run_at(task.get("schedule_kind") or "manual", task.get("interval_minutes") or 0) if active else None
    with _db() as conn:
        conn.execute(
            "UPDATE task_definitions SET updated_at=?, active=?, next_run_at=?, last_status=? WHERE id=?",
            (now, 1 if active else 0, next_run_at, last_status, task_id),
        )
    _record_task_event(task_id, event_type, detail, status=last_status)
    updated = _task_state_response(task_id)
    return {"ok": True, "task": updated}


def _task_clone_definition(task_id: str) -> dict:
    row = _task_fetch_row(task_id)
    if not row:
        return {"ok": False, "error": "Task not found", "task_id": task_id}
    source = _task_row_to_dict(row)
    cloned_id = "task_" + uuid.uuid4().hex[:8]
    now = _iso_now()
    clone_name = f"{source.get('name') or 'Tasked'} (Clone)"
    next_run_at = _task_next_run_at(source.get("schedule_kind") or "manual", source.get("interval_minutes") or 0) if source.get("active") else None
    with _db() as conn:
        conn.execute(
            "INSERT INTO task_definitions (id, created_at, updated_at, name, mode, schedule_kind, interval_minutes, active, tabs_required, "
            "template_key, executor_target, workspace_dir, planner_prompt, executor_prompt, validation_command, test_command, "
            "sandbox_assist, sandbox_assist_target, sandbox_assist_workspace_dir, sandbox_assist_command, sandbox_assist_validation_command, "
            "sandbox_assist_test_command, context_handoff, trigger_mode, trigger_text, notes, next_run_at, last_status, last_result_excerpt, "
            "completion_policy_json, alert_policy_json, workflow_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cloned_id, now, now, clone_name, source.get("mode") or "chat", source.get("schedule_kind") or "manual",
                source.get("interval_minutes") or 0, 1 if source.get("active") else 0, source.get("tabs_required") or 1,
                source.get("template_key") or "", source.get("executor_target") or "", source.get("workspace_dir") or "",
                source.get("planner_prompt") or "", source.get("executor_prompt") or "", source.get("validation_command") or "",
                source.get("test_command") or "", 1 if source.get("sandbox_assist") else 0, source.get("sandbox_assist_target") or "",
                source.get("sandbox_assist_workspace_dir") or "", source.get("sandbox_assist_command") or "",
                source.get("sandbox_assist_validation_command") or "", source.get("sandbox_assist_test_command") or "",
                source.get("context_handoff") or "", source.get("trigger_mode") or "json",
                source.get("trigger_text") or "", (source.get("notes") or "")[:1200] + f"\n\nCloned from {task_id}.",
                next_run_at, "idle", "", json.dumps(source.get("completion_policy") or _task_default_completion_policy(), ensure_ascii=False),
                json.dumps(source.get("alert_policy") or _task_default_alert_policy(), ensure_ascii=False), int(source.get("workflow_version") or 1),
            ),
        )
        _task_save_steps(conn, cloned_id, _task_clone_steps(cloned_id, source.get("steps") or _task_steps_fetch(task_id) or _task_build_default_steps(source)))
    _record_task_event(cloned_id, "task-cloned", f"Task cloned from {task_id}.", status="idle")
    cloned = _task_state_response(cloned_id)
    return {"ok": True, "task": cloned, "source_task_id": task_id}


def _task_archive_definition(task_id: str) -> dict:
    row = _task_fetch_row(task_id)
    if not row:
        return {"ok": False, "error": "Task not found", "task_id": task_id}
    now = _iso_now()
    try:
        with _db() as conn:
            conn.execute(
                "UPDATE task_definitions SET archived_at=?, active=0, next_run_at=NULL, updated_at=?, last_status=? WHERE id=?",
                (now, now, "archived", task_id),
            )
        _record_task_event(task_id, "task-archived", f"Task {task_id} archived.", status="archived")
        return {"ok": True, "task": _task_state_response(task_id)}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "task_id": task_id}


def _update_alert_status_record(alert_id: int, *, status: str, snooze_minutes: int = 0) -> tuple[dict, int]:
    now = _iso_now()
    snoozed_until = (datetime.now(timezone.utc) + timedelta(minutes=snooze_minutes)).isoformat() if status == "snoozed" and snooze_minutes > 0 else None
    resolved_at = now if status == "resolved" else None
    acknowledged_at = now if status == "acknowledged" else None
    try:
        with _db() as conn:
            row = conn.execute("SELECT * FROM task_alerts WHERE id=?", (alert_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "Alert not found"}, 404
            conn.execute(
                "UPDATE task_alerts SET status=?, updated_at=?, acknowledged_at=?, resolved_at=?, snoozed_until=? WHERE id=?",
                (status, now, acknowledged_at, resolved_at, snoozed_until, alert_id),
            )
            updated = conn.execute("SELECT * FROM task_alerts WHERE id=?", (alert_id,)).fetchone()
        updated_alert = _task_alert_to_dict(updated)
        task_id = updated_alert.get("task_id") or ""
        if task_id:
            event_type = {
                "acknowledged": "alert-acknowledged",
                "resolved": "alert-resolved",
                "snoozed": "alert-snoozed",
                "open": "alert-reopened",
            }[status]
            detail = {
                "acknowledged": f"Alert #{alert_id} was acknowledged.",
                "resolved": f"Alert #{alert_id} was resolved.",
                "snoozed": f"Alert #{alert_id} was snoozed until {snoozed_until or 'later'}.",
                "open": f"Alert #{alert_id} was reopened.",
            }[status]
            _record_task_event(task_id, event_type, detail, status=status, run_id=str(updated_alert.get("run_id") or ""), alert_id=alert_id)
        return {"ok": True, "alert": updated_alert}, 200
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}, 500


async def _task_apply_feedback(task_id: str, run_id: str, step_id: str, *, agent_id: str, status: str, signals: dict, summary: str, raw_excerpt: str) -> dict:
    row = _task_fetch_row(task_id)
    if not row:
        return {"ok": False, "error": "Task not found", "task_id": task_id}
    task_row = _task_row_to_dict(row)
    steps = _task_steps_for_task(task_row)
    step_index = _task_step_index_map(steps)
    if step_id and step_id not in step_index:
        return {"ok": False, "error": "Step not found", "task_id": task_id, "run_id": run_id}
    try:
        with _db() as conn:
            run_row = conn.execute("SELECT * FROM task_runs WHERE id=? AND task_id=?", (run_id, task_id)).fetchone()
            if not run_row:
                return {"ok": False, "error": "Run not found", "task_id": task_id, "run_id": run_id}
            current_result = conn.execute(
                "SELECT * FROM task_step_results WHERE run_id=? AND step_id=? ORDER BY started_at DESC LIMIT 1",
                (run_id, step_id),
            ).fetchone()
            feedback_id = "tfb_" + uuid.uuid4().hex[:10]
            conn.execute(
                "INSERT INTO task_feedback_events (id, task_id, run_id, step_id, agent_id, feedback_type, status, payload_json, summary, raw_excerpt, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    feedback_id, task_id, run_id, step_id, agent_id,
                    "result", status, json.dumps(signals or {}, ensure_ascii=False),
                    summary[:1500], raw_excerpt[:3000], _iso_now(),
                ),
            )
            if current_result:
                step_output = {"signals": signals or {}, "summary": summary, "raw_excerpt": raw_excerpt}
                if isinstance(signals, dict):
                    step_output.update(signals)
                conn.execute(
                    "UPDATE task_step_results SET finished_at=?, status=?, output_json=?, error_text=?, duration_ms=? WHERE id=?",
                    (
                        _iso_now(),
                        status,
                        json.dumps(step_output, ensure_ascii=False),
                        "" if status in {"completed", "ok"} else raw_excerpt[:1500],
                        _duration_ms(current_result["started_at"], _iso_now()) or 0,
                        current_result["id"],
                    ),
                )
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "task_id": task_id, "run_id": run_id}
    _record_task_event(task_id, "agent-feedback", f"{agent_id} feedback for {step_id}: {summary or status}", status=status, run_id=run_id)
    if status in {"waiting-feedback", "launch-pending", "pending"}:
        _task_update_run_tracking(run_id, status="waiting-feedback", output_excerpt=(summary or raw_excerpt)[:2000])
        return {"ok": True, "task_id": task_id, "run_id": run_id, "status": "waiting-feedback"}
    if status in {"failed", "cancelled", "error"}:
        return _task_mark_terminal(task_row, run_id, status="failed" if status != "cancelled" else "cancelled", text=summary, error_text=raw_excerpt or summary, current_step_id=step_id, terminal_reason=f"feedback-{status}", next_run_at=_task_next_run_at(task_row.get("schedule_kind") or "manual", task_row.get("interval_minutes") or 0))
    context = _task_context_from_history(task_row, run_id)
    next_step_id = ""
    if step_id in step_index:
        next_step_id = _task_resolve_next_step_id(steps, step_index[step_id], steps[step_index[step_id]], success=True)
    if not next_step_id:
        return _task_mark_terminal(task_row, run_id, status="completed", text=summary or "Feedback completed the workflow", current_step_id=step_id, terminal_reason="feedback-complete", next_run_at=_task_next_run_at(task_row.get("schedule_kind") or "manual", task_row.get("interval_minutes") or 0))
    return await _task_resume_workflow(task_row, run_id, source="feedback", start_step_id=next_step_id, parent_run_id=run_id, context=context)


@app.post("/api/tasks/{task_id}/run", name="api_task_run")
async def api_task_run(task_id: str):
    result = await _execute_task_record(task_id, source="manual")
    status_code = 200 if result.get("ok") else (409 if "already running" in (result.get("error") or "").lower() else 400)
    return JSONResponse(result, status_code=status_code)


@app.post("/api/tasks/{task_id}/start", name="api_task_start")
async def api_task_start(task_id: str):
    _record_task_event(task_id, "task-start-requested", "Task orchestration requested a task start.", status="requested")
    result = await _execute_task_record(task_id, source="start")
    status_code = 200 if result.get("ok") else (409 if "already running" in (result.get("error") or "").lower() else 400)
    return JSONResponse(result, status_code=status_code)


@app.post("/api/tasks/{task_id}/repeat", name="api_task_repeat")
async def api_task_repeat(task_id: str):
    _record_task_event(task_id, "task-repeat-requested", "Task timer requested an immediate repeat run.", status="requested")
    result = await _execute_task_record(task_id, source="repeat")
    status_code = 200 if result.get("ok") else (409 if "already running" in (result.get("error") or "").lower() else 400)
    return JSONResponse(result, status_code=status_code)


@app.post("/api/tasks/{task_id}/restart", name="api_task_restart")
async def api_task_restart(task_id: str):
    _record_task_event(task_id, "task-restart-requested", "Task orchestration requested a restart.", status="requested")
    result = await _execute_task_record(task_id, source="restart")
    status_code = 200 if result.get("ok") else (409 if "already running" in (result.get("error") or "").lower() else 400)
    return JSONResponse(result, status_code=status_code)


@app.post("/api/tasks/{task_id}/pause", name="api_task_pause")
async def api_task_pause(task_id: str):
    row = _task_fetch_row(task_id)
    if not row:
        return JSONResponse({"ok": False, "error": "Task not found", "task_id": task_id}, status_code=404)
    detail = "Task timer paused future scheduled runs."
    if task_id in _task_runner_ids:
        detail += " Current run remains active until it finishes."
    result = _task_update_activation(task_id, active=False, last_status="paused", event_type="task-paused", detail=detail)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/tasks/{task_id}/resume", name="api_task_resume")
async def api_task_resume(task_id: str):
    row = _task_fetch_row(task_id)
    if not row:
        return JSONResponse({"ok": False, "error": "Task not found", "task_id": task_id}, status_code=404)
    task = _task_row_to_dict(row)
    last_status = "live" if task.get("schedule_kind") == "continuous" else ("repeating" if task.get("schedule_kind") == "recurring" else "ready")
    result = _task_update_activation(
        task_id,
        active=True,
        last_status=last_status,
        event_type="task-resumed",
        detail=f"Task timer resumed the {task.get('schedule_label')} flow.",
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/tasks/{task_id}/clone", name="api_task_clone")
async def api_task_clone(task_id: str):
    result = _task_clone_definition(task_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 404)


@app.post("/api/tasks/{task_id}/redo", name="api_task_redo")
async def api_task_redo(task_id: str):
    _record_task_event(task_id, "task-redo-requested", "Completed task requested a redo run.", status="requested")
    result = await _execute_task_record(task_id, source="redo")
    status_code = 200 if result.get("ok") else (409 if "already running" in (result.get("error") or "").lower() else 400)
    return JSONResponse(result, status_code=status_code)


@app.post("/api/tasks/{task_id}/archive", name="api_task_archive")
async def api_task_archive(task_id: str):
    result = _task_archive_definition(task_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 404)


@app.post("/api/task-feedback", name="api_task_feedback")
async def api_task_feedback(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    task_id = (body.get("task_id") or "").strip()
    run_id = (body.get("run_id") or "").strip()
    step_id = (body.get("step_id") or "").strip()
    agent_id = (body.get("agent_id") or "").strip()
    status = (body.get("status") or "completed").strip().lower()
    signals = body.get("signals") if isinstance(body.get("signals"), dict) else {}
    summary = (body.get("summary") or "").strip()
    raw_excerpt = (body.get("raw_excerpt") or "").strip()
    if not task_id or not run_id or not step_id or not agent_id:
        return JSONResponse({"ok": False, "error": "task_id, run_id, step_id, and agent_id are required"}, status_code=400)
    result = await _task_apply_feedback(task_id, run_id, step_id, agent_id=agent_id, status=status, signals=signals, summary=summary, raw_excerpt=raw_excerpt)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/api/task-completed", name="api_task_completed")
async def api_task_completed(task_id: str = "", status: str = "completed,failed,cancelled", limit: int = 100):
    statuses = [item.strip().lower() for item in str(status).split(",") if item.strip()]
    if not statuses:
        statuses = ["completed", "failed", "cancelled"]
    limit = max(1, min(200, limit))
    try:
        with _db() as conn:
            params: list[object] = []
            where = [f"r.status IN ({','.join(['?'] * len(statuses))})"]
            params.extend(statuses)
            if task_id:
                where.append("r.task_id=?")
                params.append(task_id)
            rows = conn.execute(
                "SELECT r.*, t.name AS task_name, t.mode AS task_mode, t.executor_target AS task_executor_target, "
                "t.archived_at AS task_archived_at, t.alert_policy_json AS task_alert_policy_json "
                "FROM task_runs r LEFT JOIN task_definitions t ON t.id=r.task_id "
                f"WHERE {' AND '.join(where)} ORDER BY COALESCE(r.completed_at, r.finished_at, r.created_at) DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            items = []
            for row in rows:
                run = _task_run_to_dict(row)
                latest_alert_row = conn.execute(
                    "SELECT a.*, t.mode AS task_mode, t.template_key AS template_key, t.schedule_kind AS schedule_kind, "
                    "t.interval_minutes AS interval_minutes, t.tabs_required AS tabs_required, t.active AS active, "
                    "t.executor_target AS executor_target, t.workspace_dir AS workspace_dir, "
                    "t.sandbox_assist AS sandbox_assist, t.sandbox_assist_target AS sandbox_assist_target, "
                    "t.sandbox_assist_workspace_dir AS sandbox_assist_workspace_dir "
                    "FROM task_alerts a LEFT JOIN task_definitions t ON t.id=a.task_id WHERE a.run_id=? ORDER BY a.created_at DESC LIMIT 1",
                    (run["id"],),
                ).fetchone()
                feedback_rows = conn.execute(
                    "SELECT * FROM task_feedback_events WHERE run_id=? ORDER BY created_at ASC",
                    (run["id"],),
                ).fetchall()
                step_rows = conn.execute(
                    "SELECT * FROM task_step_results WHERE run_id=? ORDER BY started_at ASC",
                    (run["id"],),
                ).fetchall()
                items.append({
                    "run": run,
                    "task_name": row["task_name"] or run.get("task_id") or "Tasked",
                    "task_mode": row["task_mode"] or run.get("mode") or "chat",
                    "task_executor_target": _task_sandbox_target(row["task_executor_target"]) if row["task_executor_target"] else "",
                    "task_archived": bool(row["task_archived_at"]),
                    "latest_alert": _task_alert_to_dict(latest_alert_row) if latest_alert_row else None,
                    "feedback": [_task_feedback_to_dict(item) for item in feedback_rows],
                    "steps": [_task_step_result_to_dict(item) for item in step_rows],
                    "completed_url": f"/task-completed?task_id={quote(str(run.get('task_id') or ''))}",
                })
        return JSONResponse({"ok": True, "items": items})
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc), "items": []}, status_code=500)


@app.get("/api/alerts", name="api_alerts")
async def api_alerts(limit: int = 100):
    limit = max(1, min(500, limit))
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT a.*, "
                "t.name AS task_name, "
                "t.mode AS task_mode, "
                "t.template_key AS template_key, "
                "t.schedule_kind AS schedule_kind, "
                "t.interval_minutes AS interval_minutes, "
                "t.tabs_required AS tabs_required, "
                "t.active AS active, "
                "t.executor_target AS executor_target, "
                "t.workspace_dir AS workspace_dir, "
                "t.sandbox_assist AS sandbox_assist, "
                "t.sandbox_assist_target AS sandbox_assist_target, "
                "t.sandbox_assist_workspace_dir AS sandbox_assist_workspace_dir, "
                "r.status AS run_status, "
                "r.current_step_id AS current_step_id, "
                "r.terminal_reason AS terminal_reason, "
                "r.completed_at AS completed_at "
                "FROM task_alerts a "
                "LEFT JOIN task_definitions t ON t.id=a.task_id "
                "LEFT JOIN task_runs r ON r.id=a.run_id "
                "ORDER BY a.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        alerts = [_task_alert_to_dict(row) for row in rows]
        return JSONResponse({"ok": True, "alerts": alerts})
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc), "alerts": []}, status_code=500)


@app.post("/api/alerts/{alert_id}/status", name="api_alert_status")
async def api_alert_status(alert_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    status = (body.get("status") or "").strip().lower()
    if status not in {"open", "acknowledged", "resolved", "snoozed"}:
        return JSONResponse({"ok": False, "error": "invalid status"}, status_code=400)
    snooze_minutes = max(0, int(body.get("snooze_minutes") or 0))
    payload, status_code = _update_alert_status_record(alert_id, status=status, snooze_minutes=snooze_minutes)
    return JSONResponse(payload, status_code=status_code)


@app.get("/api/task-pipelines", name="api_task_pipelines")
async def api_task_pipelines(task_id: str = "", run_id: str = "", status: str = "", limit: int = 100):
    limit = max(1, min(200, limit))
    try:
        with _db() as conn:
            pipelines = []
            if run_id:
                run_rows = conn.execute(
                    "SELECT * FROM task_runs WHERE id=? ORDER BY created_at DESC LIMIT 1",
                    (run_id,),
                ).fetchall()
            elif task_id:
                query = "SELECT * FROM task_runs WHERE task_id=?"
                params: list[object] = [task_id]
                if status:
                    query += " AND status=?"
                    params.append(status.strip().lower())
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                run_rows = conn.execute(query, tuple(params)).fetchall()
            else:
                query = "SELECT * FROM task_runs"
                params = []
                if status:
                    query += " WHERE status=?"
                    params.append(status.strip().lower())
                query += " ORDER BY COALESCE(completed_at, finished_at, created_at) DESC LIMIT ?"
                params.append(limit)
                run_rows = conn.execute(query, tuple(params)).fetchall()

            if run_rows:
                for run_row in run_rows:
                    task_row = conn.execute(
                        "SELECT * FROM task_definitions WHERE id=? LIMIT 1",
                        (run_row["task_id"],),
                    ).fetchone()
                    if not task_row:
                        continue
                    all_task_runs = conn.execute(
                        "SELECT * FROM task_runs WHERE task_id=? ORDER BY created_at ASC",
                        (task_row["id"],),
                    ).fetchall()
                    task_alerts = conn.execute(
                        "SELECT a.*, t.mode AS task_mode, t.template_key AS template_key, t.schedule_kind AS schedule_kind, "
                        "t.interval_minutes AS interval_minutes, t.tabs_required AS tabs_required, t.active AS active, "
                        "t.executor_target AS executor_target, t.workspace_dir AS workspace_dir, "
                        "t.sandbox_assist AS sandbox_assist, t.sandbox_assist_target AS sandbox_assist_target, "
                        "t.sandbox_assist_workspace_dir AS sandbox_assist_workspace_dir "
                        "FROM task_alerts a LEFT JOIN task_definitions t ON t.id=a.task_id "
                        "WHERE a.task_id=? ORDER BY a.created_at ASC",
                        (task_row["id"],),
                    ).fetchall()
                    task_events = conn.execute(
                        "SELECT * FROM task_events WHERE task_id=? AND (run_id='' OR run_id IS NULL OR run_id=?) ORDER BY created_at ASC",
                        (task_row["id"], run_row["id"]),
                    ).fetchall()
                    step_rows = conn.execute(
                        "SELECT * FROM task_step_results WHERE run_id=? ORDER BY started_at ASC, id ASC",
                        (run_row["id"],),
                    ).fetchall()
                    feedback_rows = conn.execute(
                        "SELECT * FROM task_feedback_events WHERE run_id=? ORDER BY created_at ASC",
                        (run_row["id"],),
                    ).fetchall()
                    pipelines.append(_task_pipeline_build(dict(task_row), all_task_runs, task_alerts, task_events, step_rows, feedback_rows, selected_run_id=str(run_row["id"])))
            elif task_id:
                task_row = conn.execute(
                    "SELECT * FROM task_definitions WHERE id=? ORDER BY created_at DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                if task_row:
                    task_alerts = conn.execute(
                        "SELECT a.*, t.mode AS task_mode, t.template_key AS template_key, t.schedule_kind AS schedule_kind, "
                        "t.interval_minutes AS interval_minutes, t.tabs_required AS tabs_required, t.active AS active, "
                        "t.executor_target AS executor_target, t.workspace_dir AS workspace_dir, "
                        "t.sandbox_assist AS sandbox_assist, t.sandbox_assist_target AS sandbox_assist_target, "
                        "t.sandbox_assist_workspace_dir AS sandbox_assist_workspace_dir "
                        "FROM task_alerts a LEFT JOIN task_definitions t ON t.id=a.task_id "
                        "WHERE a.task_id=? ORDER BY a.created_at ASC",
                        (task_row["id"],),
                    ).fetchall()
                    task_events = conn.execute(
                        "SELECT * FROM task_events WHERE task_id=? ORDER BY created_at ASC",
                        (task_row["id"],),
                    ).fetchall()
                    pipelines.append(_task_pipeline_build(dict(task_row), [], task_alerts, task_events, [], [], selected_run_id=""))

        return JSONResponse({"ok": True, "pipelines": pipelines})
    except sqlite3.Error as exc:
        return JSONResponse({"ok": False, "error": str(exc), "pipelines": []}, status_code=500)


@app.post("/api/alerts/{alert_id}/ack", name="api_alert_ack")
async def api_alert_ack(alert_id: int):
    payload, status_code = _update_alert_status_record(alert_id, status="acknowledged")
    return JSONResponse(payload, status_code=status_code)


@app.get("/api/runtime-status", name="api_runtime_status")
async def api_runtime_status(force: bool = False):
    """Classified runtime status for C1/C3/C10/C11 + C3 pool + M365 session state."""
    return JSONResponse(await _get_runtime_status_snapshot(force=force))


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
    Body: {agent_id, prompt, chat_mode?, work_mode?, attachments?, stream?,
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
    stream     = bool(payload_in.get("stream"))
    attachments = payload_in.get("attachments") or []
    messages_in = payload_in.get("messages")  # full history array (optional)
    session_id  = (payload_in.get("session_id") or "").strip()
    messages = messages_in if isinstance(messages_in, list) and len(messages_in) > 0 else None
    prompt_text = _chat_prompt(prompt, messages)

    if not prompt_text and not messages:
        return JSONResponse({"ok": False, "error": "prompt or messages required"}, status_code=400)

    if not session_id:
        session_id = "cs_" + uuid.uuid4().hex[:8]

    if stream:
        async def generate():
            client = _get_http()
            headers = _build_chat_headers(agent_id, chat_mode=chat_mode, work_mode=work_mode)
            body = _build_chat_body(prompt_text, attachments=attachments, messages=messages, stream=True)
            full_text = ""
            http_status = 200
            t0 = time.monotonic()
            try:
                async with client.stream(
                    "POST",
                    f"{c1}/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=360,
                ) as resp:
                    http_status = resp.status_code
                    if resp.status_code < 200 or resp.status_code >= 300:
                        raw_error = (await resp.aread()).decode("utf-8", errors="replace")
                        diagnosis = await _diagnose_copilot_issue(
                            _error_text(raw_error) or f"HTTP {resp.status_code}",
                            client=client,
                        )
                        error_text = diagnosis["message"]
                        now = datetime.now(timezone.utc).isoformat()
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        _log_chat_failure(
                            session_id,
                            agent_id,
                            prompt_text,
                            error_text,
                            now,
                            http_status=resp.status_code,
                            elapsed_ms=elapsed_ms,
                            source="chat-stream",
                        )
                        yield _sse_event({"type": "error", "message": error_text})
                        return

                    line_iter = resp.aiter_lines()
                    waited_s = 0
                    while True:
                        try:
                            raw_line = await asyncio.wait_for(line_iter.__anext__(), timeout=WAIT_HEARTBEAT_S)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            waited_s += int(WAIT_HEARTBEAT_S)
                            runtime = None
                            try:
                                runtime = await _get_runtime_status_snapshot(client=client)
                            except Exception:
                                runtime = None
                            yield _sse_event({
                                "type": "status",
                                "text": f"Working on the response... Please wait. ({waited_s}s) {_runtime_wait_message(runtime)}",
                                "waited_s": waited_s,
                            })
                            continue
                        if not raw_line.startswith("data:"):
                            continue
                        data_str = raw_line[5:].strip()
                        if not data_str:
                            continue
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if "error" in chunk:
                            diagnosis = await _diagnose_copilot_issue(
                                _error_text(chunk["error"]) or "Upstream streaming error",
                                client=client,
                            )
                            error_text = diagnosis["message"]
                            now = datetime.now(timezone.utc).isoformat()
                            elapsed_ms = int((time.monotonic() - t0) * 1000)
                            _log_chat_failure(
                                session_id,
                                agent_id,
                                prompt_text,
                                error_text,
                                now,
                                http_status=http_status,
                                elapsed_ms=elapsed_ms,
                                source="chat-stream",
                            )
                            yield _sse_event({"type": "error", "message": error_text})
                            return
                        for ch in chunk.get("choices", []):
                            token = (ch.get("delta") or {}).get("content") or ""
                            if token:
                                full_text += token
                                yield _sse_event({"type": "token", "text": token})
            except Exception as exc:
                now = datetime.now(timezone.utc).isoformat()
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                diagnosis = await _diagnose_copilot_issue(str(exc), client=client)
                error_text = diagnosis["message"]
                _log_chat_failure(
                    session_id,
                    agent_id,
                    prompt_text,
                    error_text,
                    now,
                    http_status=http_status,
                    elapsed_ms=elapsed_ms,
                    source="chat-stream",
                )
                yield _sse_event({"type": "error", "message": error_text})
                return

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if not full_text:
                now = datetime.now(timezone.utc).isoformat()
                diagnosis = await _diagnose_copilot_issue("empty response from Copilot", client=client)
                error_text = diagnosis["message"]
                _log_chat_failure(
                    session_id,
                    agent_id,
                    prompt_text,
                    error_text,
                    now,
                    http_status=http_status,
                    elapsed_ms=elapsed_ms,
                    source="chat-stream",
                )
                yield _sse_event({"type": "error", "message": error_text})
                return

            now = datetime.now(timezone.utc).isoformat()
            try:
                token_est = _persist_chat_turn(
                    session_id,
                    agent_id,
                    prompt_text,
                    full_text,
                    now,
                    messages=messages,
                    http_status=http_status,
                    elapsed_ms=elapsed_ms,
                    source="chat-stream",
                )
            except sqlite3.Error as exc:
                yield _sse_event({"type": "error", "message": str(exc)})
                return

            yield _sse_event({
                "type": "done",
                "text": full_text,
                "session_id": session_id,
                "token_estimate": token_est,
                "http_status": http_status,
            })

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = await _chat_one(
        agent_id, prompt_text, c1,
        chat_mode=chat_mode,
        attachments=attachments,
        work_mode=work_mode,
        messages=messages,
    )

    now = datetime.now(timezone.utc).isoformat()
    resp_text = result.get("text") or ""
    if result.get("ok") and resp_text:
        try:
            token_est = _persist_chat_turn(
                session_id,
                agent_id,
                prompt_text,
                resp_text,
                now,
                messages=messages,
                http_status=result.get("http_status"),
                elapsed_ms=result.get("elapsed_ms"),
                source="chat",
            )
        except sqlite3.Error:
            token_est = _estimate_tokens(messages) if messages else (len(prompt_text) + len(resp_text)) // 4
    else:
        error_text = result.get("error") or resp_text or "C1 returned an empty reply"
        _log_chat_failure(
            session_id,
            agent_id,
            prompt_text,
            error_text,
            now,
            http_status=result.get("http_status"),
            elapsed_ms=result.get("elapsed_ms"),
            source="chat",
        )
        token_est = _estimate_tokens(messages) if messages else (len(prompt_text) + len(resp_text)) // 4

    result["session_id"] = session_id
    result["token_estimate"] = token_est
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

    # Pre-warm C3 pool before a parallel run so agents get pre-created tabs.
    # Non-fatal — on-demand tab creation is the fallback if this fails.
    if parallel and len(agents_to_run) > 1:
        c3 = _urls().get("c3", "http://browser-auth:8001")
        parallel_pool_size = max(1, int(os.environ.get("C3_POOL_SIZE_PARALLEL", "6")))
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
            "diagnosis": r.get("diagnosis"),
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
                llm_r = None
                async for item in _post_with_heartbeats(
                    client,
                    f"{c1}/v1/chat/completions",
                    headers=headers,
                    body=body,
                    request_timeout=180,
                ):
                    if item["kind"] == "heartbeat":
                        wait_msg = _runtime_wait_message(item.get("runtime"))
                        yield _sse("thinking", {
                            "step": step,
                            "text": f"⏳ Working on the response... Please wait. Waiting on Copilot ({item['waited_s']}s)... {wait_msg}",
                        })
                        continue
                    llm_r = item["response"]
                if llm_r is None:
                    raise RuntimeError("Copilot request ended without a response")
                if llm_r.status_code != 200:
                    raw = llm_r.text[:400]
                    diagnosis = await _diagnose_copilot_issue(raw or f"HTTP {llm_r.status_code}", client=client)
                    yield _sse("error", {"message": diagnosis["message"]})
                    return
                llm_data = llm_r.json()
                response_text: str = llm_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as exc:
                diagnosis = await _diagnose_copilot_issue(str(exc) or type(exc).__name__, client=client)
                err_detail = diagnosis["message"]
                # Transient errors (ReadTimeout, ConnectError, etc.) — retry up to 3 times
                service_error_retries += 1
                if service_error_retries <= 3:
                    wait_s = service_error_retries * 15  # 15s, 30s, 45s back-off
                    yield _sse("thinking", {"step": step, "text":
                        f"⚠️ {err_detail[:120]} — "
                        f"retrying in {wait_s}s (attempt {service_error_retries}/3)..."})
                    await asyncio.sleep(wait_s)
                    continue
                yield _sse("error", {"message":
                    f"{err_detail[:220]} after 3 retries. "
                    f"Use the runtime badge to identify whether C1, C3, or M365 is degraded, then resume the session from History."})
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
                diagnosis = await _diagnose_copilot_issue("empty response from Copilot", client=client)
                yield _sse("error", {"message": diagnosis["message"]})
                return

            if any(p in response_text.lower() for p in _SERVICE_ERROR_PHRASES):
                service_error_retries += 1
                if service_error_retries > 3:
                    diagnosis = await _diagnose_copilot_issue(response_text, client=client)
                    yield _sse("error", {"message": diagnosis["message"]})
                    return
                wait_s = service_error_retries * 15  # 15s, 30s, 45s
                diagnosis = await _diagnose_copilot_issue(response_text, client=client)
                yield _sse("thinking", {"step": step, "text":
                    f"⚠️ {diagnosis['summary']} — waiting {wait_s}s then retrying "
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
    """Execute a shell command in C10, C11, or C12b sandbox.
    Body: {command: str, sandbox: "c10"|"c11"|"c12b", timeout?: int, cwd?: str, session_id?: str}
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
    elif sandbox == "c12b":
        result = await _c12b_exec(command, timeout=timeout, cwd=cwd)
    else:
        result = await _c10_exec(command, timeout=timeout, cwd=cwd)
    return JSONResponse(result)


# ── Container control API (start/stop optional containers) ───────────────────

# Containers that can be toggled on/off to save resources
_OPTIONAL_CONTAINERS = {"C2_agent-terminal", "C5_claude-code", "C7a_openclaw-gateway", "C7b_openclaw-cli", "C8_hermes-agent", "C12_sandbox", "C12b_sandbox"}
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
            llm_r = None
            async for item in _post_with_heartbeats(
                client,
                f"{c1}/v1/chat/completions",
                headers=headers,
                body={"model": "copilot", "messages": messages, "stream": False},
                request_timeout=180,
            ):
                if item["kind"] == "heartbeat":
                    _q("pane_thinking", {
                        "step": step,
                        "text": f"⏳ Working on the response... Please wait. Waiting on Copilot ({item['waited_s']}s)... {_runtime_wait_message(item.get('runtime'))}",
                    })
                    continue
                llm_r = item["response"]
            if llm_r is None:
                raise RuntimeError("Copilot request ended without a response")
            if llm_r.status_code != 200:
                diagnosis = await _diagnose_copilot_issue(llm_r.text[:200] or f"HTTP {llm_r.status_code}", client=client)
                _q("pane_error", {"message": diagnosis["message"]})
                return {"role": role, "pane_id": pane_id, "done": False, "summary": "C1 error", "files": files_created, "steps": step}
            response_text: str = llm_r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            service_error_retries += 1
            diagnosis = await _diagnose_copilot_issue(str(exc), client=client)
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ {diagnosis['summary']} — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": diagnosis["message"]})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": diagnosis["summary"], "files": files_created, "steps": step}

        _SERVICE_PHRASES = ("something went wrong", "please try again", "experiencing high demand", "we're experiencing")
        if not response_text.strip():
            diagnosis = await _diagnose_copilot_issue("empty response from Copilot", client=client)
            _q("pane_error", {"message": diagnosis["message"]})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": "empty response", "files": files_created, "steps": step}

        if any(p in response_text.lower() for p in _SERVICE_PHRASES):
            service_error_retries += 1
            diagnosis = await _diagnose_copilot_issue(response_text, client=client)
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ {diagnosis['summary']} — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": diagnosis["message"]})
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
            pool_size = max(1, int(os.environ.get("C3_POOL_SIZE_PARALLEL", "6")))
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
            sup_r = None
            async for item in _post_with_heartbeats(
                client,
                f"{c1}/v1/chat/completions",
                headers={"Content-Type": "application/json", "X-Agent-ID": "ma-supervisor"},
                body={"model": "copilot", "messages": [{"role": "user", "content": supervisor_prompt}], "stream": False},
                request_timeout=60,
            ):
                if item["kind"] == "heartbeat":
                    yield _sse("supervisor", {
                        "step": 0,
                        "text": f"⏳ Working on the response... Please wait. Supervisor waiting on Copilot ({item['waited_s']}s)... {_runtime_wait_message(item.get('runtime'))}",
                    })
                    continue
                sup_r = item["response"]
            if sup_r is None:
                raise RuntimeError("Supervisor request ended without a response")
            sup_text = sup_r.json().get("choices", [{}])[0].get("message", {}).get("content", "") if sup_r.status_code == 200 else ""
        except Exception as exc:
            sup_text = ""
            diagnosis = await _diagnose_copilot_issue(str(exc), client=client)
            yield _sse("supervisor", {"step": 0, "text": f"⚠️ Supervisor failed: {diagnosis['summary']} — using default assignments"})

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
            llm_r = None
            async for item in _post_with_heartbeats(
                client,
                f"{c1}/v1/chat/completions",
                headers=headers,
                body={"model": "copilot", "messages": messages, "stream": False},
                request_timeout=180,
            ):
                if item["kind"] == "heartbeat":
                    _q("pane_thinking", {
                        "step": step,
                        "text": f"⏳ Working on the response... Please wait. Waiting on Copilot ({item['waited_s']}s)... {_runtime_wait_message(item.get('runtime'))}",
                    })
                    continue
                llm_r = item["response"]
            if llm_r is None:
                raise RuntimeError("Copilot request ended without a response")
            if llm_r.status_code != 200:
                diagnosis = await _diagnose_copilot_issue(llm_r.text[:200] or f"HTTP {llm_r.status_code}", client=client)
                _q("pane_error", {"message": diagnosis["message"]})
                return {"role": role, "pane_id": pane_id, "done": False, "summary": "C1 error", "files": files_created, "steps": step}
            response_text: str = llm_r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            service_error_retries += 1
            diagnosis = await _diagnose_copilot_issue(str(exc), client=client)
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ {diagnosis['summary']} — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": diagnosis["message"]})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": diagnosis["summary"], "files": files_created, "steps": step}

        _SERVICE_PHRASES = ("something went wrong", "please try again", "experiencing high demand", "we're experiencing")
        if not response_text.strip():
            diagnosis = await _diagnose_copilot_issue("empty response from Copilot", client=client)
            _q("pane_error", {"message": diagnosis["message"]})
            return {"role": role, "pane_id": pane_id, "done": False, "summary": "empty response", "files": files_created, "steps": step}

        if any(p in response_text.lower() for p in _SERVICE_PHRASES):
            service_error_retries += 1
            diagnosis = await _diagnose_copilot_issue(response_text, client=client)
            if service_error_retries <= 2:
                wait_s = service_error_retries * 12
                _q("pane_thinking", {"step": step, "text": f"⚠️ {diagnosis['summary']} — retrying in {wait_s}s..."})
                await asyncio.sleep(wait_s)
                continue
            _q("pane_error", {"message": diagnosis["message"]})
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
async def page_multi_agento_lower(request: Request, task: str = "", task_id: str = "", task_run_id: str = "", source: str = "", step_id: str = ""):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/multi-Agento" + (f"?{request.url.query}" if request.url.query else ""), status_code=301)

@app.get("/multi-Agento", response_class=HTMLResponse, name="page_multi_agento")
async def page_multi_agento(
    request: Request,
    task: str = "",
    task_id: str = "",
    task_run_id: str = "",
    source: str = "",
    step_id: str = "",
):
    """Full-featured multi-agent IDE with C11 session-scoped workspace."""
    return templates.TemplateResponse(request, "multi_agento.html", {
        "agents": AGENTS,
        "ma_roles": _MA_ROLES,
        "task": task,
        "task_launch": {
            "task": task,
            "task_id": task_id,
            "task_run_id": task_run_id,
            "source": source,
            "step_id": step_id,
        },
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
            sup_r = None
            async for item in _post_with_heartbeats(
                client,
                f"{c1}/v1/chat/completions",
                headers={"Content-Type": "application/json", "X-Agent-ID": agent_id},
                body={"model": "copilot", "messages": [{"role": "user", "content": sup_prompt}], "stream": False},
                request_timeout=60,
            ):
                if item["kind"] == "heartbeat":
                    yield _sse("supervisor", {
                        "text": f"⏳ Working on the response... Please wait. Supervisor waiting on Copilot ({item['waited_s']}s)... {_runtime_wait_message(item.get('runtime'))}"
                    })
                    continue
                sup_r = item["response"]
            if sup_r is None:
                raise RuntimeError("Supervisor request ended without a response")
            if sup_r.status_code == 200:
                sup_text = sup_r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                for line in sup_text.splitlines():
                    for r in active_roles:
                        if line.lower().startswith(r + ":"):
                            assignments[r] = line[len(r)+1:].strip()
                            break
        except Exception as exc:
            diagnosis = await _diagnose_copilot_issue(str(exc), client=client)
            yield _sse("supervisor", {"text": f"⚠️ Supervisor error: {diagnosis['summary']} — using default assignments"})

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
