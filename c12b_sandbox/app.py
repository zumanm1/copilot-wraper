"""
C12B_SANDBOX — Lean coding/test sandbox (FastAPI, port 8210).

Provides a lightweight REST API for host-side and C9-driven sandbox tasks:
  - Execute shell commands with timeout
  - Create / read / list / delete files in /workspace
  - Reset the workspace
  - Report installed tool versions for Python / pip / uv / node / npm / git

Security:
  - Runs as non-root user 'sandbox'
  - File paths are constrained to WORKSPACE
  - Command execution is time-bounded (default 30s, max 120s)
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace")).resolve()
SESSION_DB = Path(os.environ.get("SESSION_DB_PATH", str(Path.home() / ".c12b_sessions.db"))).resolve()

app = FastAPI(title="C12b Lean Sandbox", version="1.0.0")


def _safe_path(relative: str) -> Path:
    clean = relative.lstrip("/").lstrip("\\")
    resolved = (WORKSPACE / clean).resolve()
    try:
        resolved.relative_to(WORKSPACE)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Path traversal denied: {relative!r}")
    return resolved


def _tool_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return f"error: {exc}"
    text = (result.stdout or result.stderr or "").strip().splitlines()
    return text[0] if text else "unknown"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(SESSION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_session_db() -> None:
    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exec_sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                command TEXT NOT NULL,
                operation TEXT NOT NULL,
                cwd TEXT NOT NULL,
                requested_timeout_s INTEGER DEFAULT 30,
                adaptive_timeout_s INTEGER DEFAULT 30,
                elapsed_ms INTEGER DEFAULT 0,
                exit_code INTEGER DEFAULT 0,
                timed_out INTEGER DEFAULT 0,
                background INTEGER DEFAULT 0,
                pid INTEGER,
                last_error TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exec_metrics (
                operation TEXT PRIMARY KEY,
                sample_count INTEGER DEFAULT 0,
                avg_elapsed_ms REAL DEFAULT 0,
                max_elapsed_ms INTEGER DEFAULT 0,
                last_elapsed_ms INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_sessions_status_updated ON exec_sessions(status, updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_sessions_operation_updated ON exec_sessions(operation, updated_at DESC)")


def _session_row_to_dict(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    item = dict(row)
    for key in ("requested_timeout_s", "adaptive_timeout_s", "elapsed_ms", "exit_code", "timed_out", "background", "pid"):
        item[key] = int(item.get(key) or 0)
    item["timed_out"] = bool(item.get("timed_out"))
    item["background"] = bool(item.get("background"))
    return item


def _session_get(session_id: str) -> dict | None:
    try:
        with _db() as conn:
            row = conn.execute("SELECT * FROM exec_sessions WHERE id=?", (session_id,)).fetchone()
        return _session_row_to_dict(row)
    except sqlite3.Error:
        return None


def _session_list(status: str = "", limit: int = 50) -> list[dict]:
    limit = max(1, min(200, int(limit or 50)))
    sql = "SELECT * FROM exec_sessions"
    params: list[object] = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    try:
        with _db() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_session_row_to_dict(row) or {} for row in rows]
    except sqlite3.Error:
        return []


def _command_operation(command: str) -> str:
    try:
        parts = shlex.split(command or "")
    except Exception:
        parts = []
    if not parts:
        return "exec"
    base = Path(parts[0]).name.strip().lower()
    return base or "exec"


def _metric_row(operation: str) -> dict:
    try:
        with _db() as conn:
            row = conn.execute("SELECT * FROM exec_metrics WHERE operation=?", (operation,)).fetchone()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}


def _adaptive_timeout_seconds(requested_timeout_s: int, operation: str) -> int:
    requested_timeout_s = max(1, min(180, int(requested_timeout_s or 30)))
    metric = _metric_row(operation)
    if not metric:
        return requested_timeout_s
    adaptive_s = requested_timeout_s
    avg_ms = float(metric.get("avg_elapsed_ms") or 0)
    max_ms = int(metric.get("max_elapsed_ms") or 0)
    last_ms = int(metric.get("last_elapsed_ms") or 0)
    if avg_ms > 0:
        adaptive_s = max(adaptive_s, int(avg_ms / 1000.0 * 1.5) + 3)
    if max_ms > 0:
        adaptive_s = max(adaptive_s, int(max_ms / 1000.0 * 1.25) + 3)
    if last_ms > 0:
        adaptive_s = max(adaptive_s, int(last_ms / 1000.0 * 1.15) + 2)
    return min(180, adaptive_s)


def _record_metric(operation: str, elapsed_ms: int) -> None:
    if elapsed_ms <= 0:
        return
    now = _iso_now()
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT sample_count, avg_elapsed_ms, max_elapsed_ms FROM exec_metrics WHERE operation=?",
                (operation,),
            ).fetchone()
            if row:
                sample_count = int(row["sample_count"] or 0) + 1
                avg_elapsed_ms = (((float(row["avg_elapsed_ms"] or 0) * (sample_count - 1)) + elapsed_ms) / sample_count)
                max_elapsed_ms = max(int(row["max_elapsed_ms"] or 0), elapsed_ms)
                conn.execute(
                    "UPDATE exec_metrics SET sample_count=?, avg_elapsed_ms=?, max_elapsed_ms=?, last_elapsed_ms=?, updated_at=? WHERE operation=?",
                    (sample_count, avg_elapsed_ms, max_elapsed_ms, elapsed_ms, now, operation),
                )
            else:
                conn.execute(
                    "INSERT INTO exec_metrics (operation, sample_count, avg_elapsed_ms, max_elapsed_ms, last_elapsed_ms, updated_at) VALUES (?,?,?,?,?,?)",
                    (operation, 1, float(elapsed_ms), elapsed_ms, elapsed_ms, now),
                )
    except sqlite3.Error:
        return


def _session_start(
    session_id: str,
    *,
    command: str,
    operation: str,
    cwd: str,
    requested_timeout_s: int,
    adaptive_timeout_s: int,
) -> None:
    now = _iso_now()
    try:
        with _db() as conn:
            conn.execute(
                """
                INSERT INTO exec_sessions (
                    id, created_at, updated_at, status, command, operation, cwd,
                    requested_timeout_s, adaptive_timeout_s, elapsed_ms, exit_code,
                    timed_out, background, pid, last_error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status='running',
                    command=excluded.command,
                    operation=excluded.operation,
                    cwd=excluded.cwd,
                    requested_timeout_s=excluded.requested_timeout_s,
                    adaptive_timeout_s=excluded.adaptive_timeout_s,
                    elapsed_ms=0,
                    exit_code=0,
                    timed_out=0,
                    background=0,
                    pid=NULL,
                    last_error=''
                """,
                (
                    session_id, now, now, "running", command, operation, cwd,
                    requested_timeout_s, adaptive_timeout_s, 0, 0, 0, 0, None, "",
                ),
            )
    except sqlite3.Error:
        return


def _session_finish(
    session_id: str,
    *,
    status: str,
    elapsed_ms: int,
    exit_code: int,
    timed_out: bool,
    background: bool,
    pid: int | None = None,
    last_error: str = "",
) -> None:
    now = _iso_now()
    try:
        with _db() as conn:
            row = conn.execute("SELECT operation FROM exec_sessions WHERE id=?", (session_id,)).fetchone()
            conn.execute(
                "UPDATE exec_sessions SET updated_at=?, status=?, elapsed_ms=?, exit_code=?, timed_out=?, background=?, pid=?, last_error=? WHERE id=?",
                (now, status, elapsed_ms, exit_code, 1 if timed_out else 0, 1 if background else 0, pid, last_error[:1500], session_id),
            )
        if row and elapsed_ms > 0 and not background:
            _record_metric(str(row["operation"]), elapsed_ms)
    except sqlite3.Error:
        return


_ensure_session_db()


@app.get("/health")
async def health():
    files = sum(1 for item in WORKSPACE.rglob("*") if item.is_file())
    sessions = _session_list(limit=5)
    running = sum(1 for item in sessions if item.get("status") in {"running", "background-running"})
    failed = sum(1 for item in sessions if item.get("status") in {"failed", "timed-out"})
    return {
        "status": "ok",
        "workspace": str(WORKSPACE),
        "file_count": files,
        "session_db": str(SESSION_DB),
        "session_manager": {
            "recent": len(sessions),
            "running": running,
            "failed_or_timed_out": failed,
        },
        "tools": {
            "python3": _tool_version(["python3", "--version"]),
            "pip": _tool_version(["pip", "--version"]),
            "uv": _tool_version(["uv", "--version"]),
            "node": _tool_version(["node", "--version"]),
            "npm": _tool_version(["npm", "--version"]),
            "git": _tool_version(["git", "--version"]),
        },
    }


@app.get("/tooling")
async def tooling():
    return {
        "ok": True,
        "workspace": str(WORKSPACE),
        "session_db": str(SESSION_DB),
        "tools": {
            "python3": _tool_version(["python3", "--version"]),
            "pip": _tool_version(["pip", "--version"]),
            "uv": _tool_version(["uv", "--version"]),
            "node": _tool_version(["node", "--version"]),
            "npm": _tool_version(["npm", "--version"]),
            "git": _tool_version(["git", "--version"]),
            "bash": _tool_version(["bash", "--version"]),
        },
    }


class ExecRequest(BaseModel):
    command: str
    timeout: int = 30
    cwd: str = "."
    session_id: str = ""


def _is_background_command(cmd: str) -> bool:
    stripped = cmd.strip()
    return stripped.endswith("&") or "nohup " in stripped or stripped.startswith("nohup ")


@app.post("/exec")
async def exec_command(req: ExecRequest):
    requested_timeout = max(1, min(120, req.timeout))
    cwd = _safe_path(req.cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    session_id = (req.session_id or "").strip() or ("c12b_" + uuid.uuid4().hex[:10])
    operation = _command_operation(req.command)
    adaptive_timeout = _adaptive_timeout_seconds(requested_timeout, operation)
    timeout = max(requested_timeout, adaptive_timeout)
    _session_start(
        session_id,
        command=req.command,
        operation=operation,
        cwd=str(cwd),
        requested_timeout_s=requested_timeout,
        adaptive_timeout_s=adaptive_timeout,
    )

    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "WORKSPACE": str(WORKSPACE),
        "PYTHONUNBUFFERED": "1",
    }

    if _is_background_command(req.command):
        try:
            t0 = time.monotonic()
            proc = await asyncio.create_subprocess_shell(
                req.command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
                env=env,
                start_new_session=True,
            )
            await asyncio.sleep(1)
            still_running = proc.returncode is None
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            _session_finish(
                session_id,
                status="background-running" if still_running else "failed",
                elapsed_ms=elapsed_ms,
                exit_code=0 if still_running else (proc.returncode or 1),
                timed_out=False,
                background=True,
                pid=proc.pid,
                last_error="" if still_running else f"Process exited early (code {proc.returncode})",
            )
            return JSONResponse({
                "stdout": f"Background process started (pid={proc.pid})",
                "stderr": "" if still_running else f"Process exited early (code {proc.returncode})",
                "exit_code": 0 if still_running else (proc.returncode or 1),
                "timed_out": False,
                "background": True,
                "pid": proc.pid,
                "session_id": session_id,
                "command": req.command,
                "cwd": str(cwd),
                "requested_timeout_s": requested_timeout,
                "adaptive_timeout_s": adaptive_timeout,
            })
        except Exception as exc:
            _session_finish(
                session_id,
                status="failed",
                elapsed_ms=0,
                exit_code=-1,
                timed_out=False,
                background=True,
                last_error=str(exc),
            )
            return JSONResponse(
                {
                    "stdout": "",
                    "stderr": str(exc),
                    "exit_code": -1,
                    "timed_out": False,
                    "background": True,
                    "session_id": session_id,
                    "requested_timeout_s": requested_timeout,
                    "adaptive_timeout_s": adaptive_timeout,
                },
                status_code=500,
            )

    try:
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            req.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(cwd),
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            timed_out = False
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            stdout_bytes, stderr_bytes = b"", b"[killed: timeout - use nohup cmd & for long-running servers]"
            timed_out = True
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        exit_code = proc.returncode if not timed_out else -1
        _session_finish(
            session_id,
            status="timed-out" if timed_out else ("completed" if exit_code == 0 else "failed"),
            elapsed_ms=elapsed_ms,
            exit_code=exit_code,
            timed_out=timed_out,
            background=False,
            last_error=stderr_bytes.decode("utf-8", errors="replace") if exit_code != 0 or timed_out else "",
        )

        return JSONResponse({
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "background": False,
            "session_id": session_id,
            "command": req.command,
            "cwd": str(cwd),
            "requested_timeout_s": requested_timeout,
            "adaptive_timeout_s": adaptive_timeout,
        })
    except Exception as exc:
        _session_finish(
            session_id,
            status="failed",
            elapsed_ms=0,
            exit_code=-1,
            timed_out=False,
            background=False,
            last_error=str(exc),
        )
        return JSONResponse(
            {
                "stdout": "",
                "stderr": str(exc),
                "exit_code": -1,
                "timed_out": False,
                "session_id": session_id,
                "command": req.command,
                "cwd": str(cwd),
                "requested_timeout_s": requested_timeout,
                "adaptive_timeout_s": adaptive_timeout,
            },
            status_code=500,
        )


@app.get("/sessions")
async def sessions(status: str = "", limit: int = 50):
    return {"ok": True, "sessions": _session_list(status=status, limit=limit)}


@app.get("/sessions/{session_id}")
async def session_get(session_id: str):
    session = _session_get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {"ok": True, "session": session}


class WriteRequest(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"


@app.post("/file/write")
async def file_write(req: WriteRequest):
    target = _safe_path(req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding=req.encoding)
    return {"ok": True, "path": str(target.relative_to(WORKSPACE)), "size": target.stat().st_size}


@app.get("/file/read")
async def file_read(path: str = Query(..., description="Relative path inside workspace")):
    target = _safe_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path!r}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path!r}")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "path": str(target.relative_to(WORKSPACE)), "content": content, "size": target.stat().st_size}


class LsRequest(BaseModel):
    path: str = "."
    recursive: bool = True


@app.post("/file/ls")
async def file_ls(req: LsRequest):
    target = _safe_path(req.path)
    if not target.exists():
        return {"ok": True, "path": req.path, "entries": []}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path!r}")

    entries = []
    if req.recursive:
        for item in sorted(target.rglob("*")):
            entries.append({
                "path": str(item.relative_to(WORKSPACE)),
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
    else:
        for item in sorted(target.iterdir()):
            entries.append({
                "path": str(item.relative_to(WORKSPACE)),
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
    return {"ok": True, "path": req.path, "entries": entries}


class DeleteRequest(BaseModel):
    path: str


@app.delete("/file/delete")
async def file_delete(req: DeleteRequest):
    target = _safe_path(req.path)
    if not target.exists():
        return {"ok": True, "path": req.path, "message": "already absent"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": req.path, "deleted": True}


@app.post("/workspace/reset")
async def workspace_reset():
    deleted = []
    for item in sorted(WORKSPACE.iterdir()):
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        deleted.append(str(item.relative_to(WORKSPACE)))
    return {"ok": True, "deleted_count": len(deleted), "deleted": deleted}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8210, reload=False)
