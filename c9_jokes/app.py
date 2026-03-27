"""
C9_JOKES — read-only validation console (FastAPI + httpx + SQLite).
Does not modify C1–C8; only HTTP GET/POST to peer URLs you configure.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3

# #region agent log
_DEBUG_LOG = os.getenv("DEBUG_LOG_PATH", "/app/.cursor/debug-aa3936.log")
def _dlog(loc, msg, data=None):
    import time as _t
    try:
        with open(_DEBUG_LOG, "a") as _f:
            _f.write(json.dumps({"sessionId":"aa3936","location":loc,"message":msg,"data":data or {},"timestamp":int(_t.time()*1000)}) + "\n")
    except Exception:
        pass
# #endregion
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("DATABASE_PATH", "/app/data/c9.db"))

# ── C10 Sandbox URL ───────────────────────────────────────────────────────────
C10_URL = os.environ.get("C10_URL", "http://c10-sandbox:8100").rstrip("/")

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


# ── Agentic loop — system prompt ──────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are an expert AI software engineer with access to an isolated Linux sandbox.
Your job is to complete the user's task by writing code, running it, testing it, and validating results.

You have access to these tools — use them by writing XML tags in your response:

1. Execute a shell command:
<tool_call><tool>exec</tool><command>BASH_COMMAND_HERE</command></tool_call>

2. Write a file:
<tool_call><tool>write_file</tool><path>relative/path/to/file.py</path><content>
FILE_CONTENT_HERE
</content></tool_call>

3. Read a file:
<tool_call><tool>read_file</tool><path>relative/path/to/file.py</path></tool_call>

4. List workspace files:
<tool_call><tool>list_files</tool></tool_call>

5. Install a package:
<tool_call><tool>install</tool><package>PACKAGE_NAME</package><manager>pip</manager></tool_call>
(manager can be "pip" or "npm")

RULES:
1. Think step by step. Plan before you code.
2. Write code, execute it, read the output, fix any errors, then validate.
3. The sandbox has: Python 3.11, Node.js, npm, git, bash, curl, pip.
4. You run as non-root user 'sandbox' in /workspace directory.
5. Always test/validate your code before declaring done.
6. When the task is fully complete and validated, write EXACTLY:
   <final_answer>YOUR_SUMMARY_OF_WHAT_WAS_BUILT_AND_VALIDATED</final_answer>
7. Maximum 15 steps — be efficient, do not repeat yourself.
8. Only one tool_call per response is allowed (keep the loop clean)."""


# ── Agentic loop — XML parser ─────────────────────────────────────────────────

def _parse_tool_call(text: str) -> dict | None:
    """
    Extract the first <tool_call>...</tool_call> block from LLM response text.
    Returns a dict with 'tool' key plus tool-specific fields, or None if not found.
    """
    match = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if not match:
        return None
    inner = match.group(1).strip()

    def _extract(tag: str) -> str:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", inner, re.DOTALL)
        return m.group(1).strip() if m else ""

    tool = _extract("tool")
    if not tool:
        return None

    result: dict = {"tool": tool}
    if tool == "exec":
        result["command"] = _extract("command")
    elif tool == "write_file":
        result["path"] = _extract("path")
        # content may contain newlines — use a more permissive extract
        cm = re.search(r"<content>(.*?)</content>", inner, re.DOTALL)
        result["content"] = cm.group(1) if cm else ""
    elif tool == "read_file":
        result["path"] = _extract("path")
    elif tool == "list_files":
        pass  # no args
    elif tool == "install":
        result["package"] = _extract("package")
        result["manager"] = _extract("manager") or "pip"
    return result


def _parse_final_answer(text: str) -> str | None:
    """Extract <final_answer> content if present."""
    m = re.search(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL)
    return m.group(1).strip() if m else None


def _strip_tool_xml(text: str) -> str:
    """Remove XML tool tags so the thinking portion is clean."""
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
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

async def _chat_one(agent_id: str, prompt: str, c1_url: str, chat_mode: str = "", attachments: list | None = None, work_mode: str = "") -> dict:
    """Call C1 for a single agent. Returns {ok, http_status, text, elapsed_ms}."""
    if attachments:
        # Build multi-part content: text + file_ref parts
        content: list = [{"type": "text", "text": prompt}]
        for att in attachments:
            if att.get("file_id"):
                content.append({"type": "file_ref", "file_id": att["file_id"], "filename": att.get("filename", "")})
        user_msg = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": prompt}
    body = {
        "model": "copilot",
        "messages": [user_msg],
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "X-Agent-ID": agent_id}
    if chat_mode:
        headers["X-Chat-Mode"] = chat_mode
    if work_mode in ("work", "web"):
        headers["X-Work-Mode"] = work_mode
    client = _get_http()
    t0 = time.monotonic()
    # #region agent log
    _dlog("app.py:_chat_one", "chat_request_start", {"agent_id": agent_id, "prompt": prompt[:80], "c1_url": c1_url, "hypothesisId": "H4"})
    # #endregion
    try:
        r = await client.post(
            f"{c1_url}/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=360,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        # #region agent log
        _dlog("app.py:_chat_one", "chat_request_done", {"agent_id": agent_id, "status": r.status_code, "elapsed_ms": elapsed_ms, "hypothesisId": "H4"})
        # #endregion
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
    """Proxy a single chat to C1. Body: {agent_id, prompt, chat_mode?, attachments?}."""
    c1 = _urls()["c1"]
    try:
        payload_in = await request.json()
    except Exception:
        payload_in = {}
    agent_id = (payload_in.get("agent_id") or "c9-jokes").strip()
    prompt = (payload_in.get("prompt") or "").strip()
    chat_mode = (payload_in.get("chat_mode") or "").strip().lower()
    work_mode = (payload_in.get("work_mode") or "").strip().lower()
    attachments = payload_in.get("attachments") or []  # [{file_id, filename}, ...]
    if not prompt:
        return JSONResponse({"ok": False, "error": "prompt required"}, status_code=400)
    result = await _chat_one(agent_id, prompt, c1, chat_mode=chat_mode, attachments=attachments, work_mode=work_mode)
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status, elapsed_ms, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    agent_id,
                    prompt[:200],
                    (result.get("text") or result.get("error") or "")[:500],
                    result.get("http_status"),
                    result.get("elapsed_ms"),
                    "chat",
                ),
            )
    except sqlite3.Error:
        pass
    return JSONResponse(result)


@app.post("/api/validate", name="api_validate")
async def api_validate(request: Request):
    """Run all agents with a prompt concurrently, persist to validation_runs + pair_results."""
    # #region agent log
    _dlog("app.py:api_validate", "validate_start", {"hypothesisId": "H4"})
    # #endregion
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
# AGENT WORKSPACE API ROUTES  (/api/agent/*)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/agent/run", name="api_agent_run")
async def api_agent_run(
    request: Request,
    task: str = "",
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
    max_steps = max(1, min(20, max_steps))
    c1 = _urls()["c1"]

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def generate():
        if not task:
            yield _sse("error", {"message": "No task provided."})
            return

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

        # Build multi-turn conversation history
        history: list[dict] = []
        files_created: list[str] = []

        yield _sse("thinking", {"step": 0, "text": f"🚀 Starting agent task: {task[:120]}...", "total_steps": max_steps})

        for step in range(1, max_steps + 1):
            yield _sse("step_done", {"step": step, "max_steps": max_steps, "status": "running"})

            # Build messages for C1
            messages: list[dict] = [
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            ]
            if not history:
                messages.append({"role": "user", "content": task})
            else:
                messages.extend(history)

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
                yield _sse("error", {"message": f"LLM call failed: {exc}"})
                return

            # Emit thinking text (stripped of XML)
            thinking_text = _strip_tool_xml(response_text)
            if thinking_text:
                yield _sse("thinking", {"step": step, "text": thinking_text})

            # Check for final answer first
            final_answer = _parse_final_answer(response_text)
            if final_answer:
                yield _sse("final", {
                    "summary": final_answer,
                    "steps_taken": step,
                    "files_created": files_created,
                })
                return

            # Parse tool call
            tool = _parse_tool_call(response_text)
            if not tool:
                # No tool call and no final answer — ask LLM to continue or finalize
                history.append({"role": "assistant", "content": response_text})
                history.append({
                    "role": "user",
                    "content": "Please continue. Use a tool to make progress, or write <final_answer> if the task is complete.",
                })
                continue

            # Emit tool_call SSE
            tool_event: dict = {"step": step, "tool": tool["tool"]}
            if tool.get("command"):
                tool_event["command"] = tool["command"]
            if tool.get("path"):
                tool_event["path"] = tool["path"]
            if tool.get("package"):
                tool_event["package"] = tool["package"]
            if tool.get("content"):
                # Send a preview of the content (first 200 chars)
                tool_event["preview"] = tool["content"][:200]
            yield _sse("tool_call", tool_event)

            # Execute the tool
            observation, meta = await _execute_tool(tool)

            # Track file updates
            if tool["tool"] == "write_file" and meta.get("ok"):
                path = meta.get("path", "")
                if path and path not in files_created:
                    files_created.append(path)
                yield _sse("file_update", {"path": path, "action": "created"})

            # Emit observation SSE
            obs_event: dict = {
                "step": step,
                "tool": tool["tool"],
                "result": observation[:800],  # truncate for stream
            }
            if "exit_code" in meta:
                obs_event["exit_code"] = meta["exit_code"]
            if meta.get("timed_out"):
                obs_event["timed_out"] = True
            yield _sse("observation", obs_event)

            # Append to conversation history
            if not history:
                history.append({"role": "user", "content": task})
            history.append({"role": "assistant", "content": response_text})
            history.append({
                "role": "user",
                "content": f"<observation>\n{observation}\n</observation>\n\nContinue with the next step, or write <final_answer> if the task is complete and validated.",
            })

        # Reached max_steps without final_answer
        yield _sse("final", {
            "summary": f"Reached maximum steps ({max_steps}). Task may be partially complete. Check the file tree for created files.",
            "steps_taken": max_steps,
            "files_created": files_created,
            "max_steps_reached": True,
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/agent/reset", name="api_agent_reset")
async def api_agent_reset():
    """Reset (wipe) the C10 workspace. Returns {ok, deleted_count}."""
    result = await _c10_reset()
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "6090"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=os.environ.get("FLASK_DEBUG") == "1")
