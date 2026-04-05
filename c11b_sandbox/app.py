"""
C11B_SANDBOX — Multi-agent sandbox (FastAPI, port 8200).

Cloned from C11 with enhanced session management, similar to C10b.
Purpose:
  - Multi-agent workspace with session-scoped isolation
  - Each session gets its own /workspace/{session_id}/ directory
  - Supports concurrent agent runs with isolated workspaces

API endpoints: /health /sessions /session/{id}/reset
               /exec /file/write /file/read /file/ls /file/delete /workspace/reset
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace")).resolve()

app = FastAPI(title="C11b Multi-Agent Sandbox", version="1.0.0")

_SESSION_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _safe_session(session_id: str) -> str:
    sid = (session_id or "").strip()
    if not _SESSION_RE.match(sid):
        sid = "sess_" + uuid.uuid4().hex[:10]
    return sid


def _safe_path(session_id: str, relative: str) -> Path:
    sid = _safe_session(session_id)
    clean = relative.lstrip("/").lstrip("\\")
    resolved = (WORKSPACE / sid / clean).resolve()
    prefix = (WORKSPACE / sid).resolve()
    try:
        resolved.relative_to(prefix)
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


@app.get("/health")
async def health():
    sessions = []
    if WORKSPACE.exists():
        for item in sorted(WORKSPACE.iterdir()):
            if item.is_dir():
                files = sum(1 for f in item.rglob("*") if f.is_file())
                sessions.append({
                    "id": item.name,
                    "files": files,
                    "size_bytes": sum(f.stat().st_size for f in item.rglob("*") if f.is_file()),
                })
    total_files = sum(s["files"] for s in sessions)
    return {
        "status": "ok",
        "workspace": str(WORKSPACE),
        "session_count": len(sessions),
        "total_files": total_files,
        "tools": {
            "python3": _tool_version(["python3", "--version"]),
            "pip": _tool_version(["pip", "--version"]),
            "uv": _tool_version(["uv", "--version"]),
            "node": _tool_version(["node", "--version"]),
            "npm": _tool_version(["npm", "--version"]),
            "git": _tool_version(["git", "--version"]),
        },
    }


@app.get("/sessions")
async def list_sessions():
    sessions = []
    if WORKSPACE.exists():
        for item in sorted(WORKSPACE.iterdir()):
            if item.is_dir():
                files = sum(1 for f in item.rglob("*") if f.is_file())
                sessions.append({
                    "id": item.name,
                    "files": files,
                    "size_bytes": sum(f.stat().st_size for f in item.rglob("*") if f.is_file()),
                    "created": _iso_now(),
                })
    return {"ok": True, "sessions": sessions}


@app.post("/session/{session_id}/reset")
async def reset_session(session_id: str):
    sid = _safe_session(session_id)
    target = WORKSPACE / sid
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "session_id": sid, "message": "session workspace reset"}


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
    session_id = _safe_session(req.session_id)
    session_ws = WORKSPACE / session_id
    session_ws.mkdir(parents=True, exist_ok=True)
    cwd_path = session_ws / req.cwd.lstrip("/")
    cwd_path.mkdir(parents=True, exist_ok=True)
    timeout_s = max(1, min(120, req.timeout))
    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "WORKSPACE": str(session_ws),
        "PYTHONUNBUFFERED": "1",
    }

    if _is_background_command(req.command):
        try:
            proc = await asyncio.create_subprocess_shell(
                req.command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(cwd_path),
                env=env,
                start_new_session=True,
            )
            await asyncio.sleep(1)
            still_running = proc.returncode is None
            return JSONResponse({
                "stdout": f"Background process started (pid={proc.pid})",
                "stderr": "" if still_running else f"Process exited early (code {proc.returncode})",
                "exit_code": 0 if still_running else (proc.returncode or 1),
                "timed_out": False,
                "background": True,
                "pid": proc.pid,
                "session_id": session_id,
                "command": req.command,
                "cwd": str(cwd_path),
            })
        except Exception as exc:
            return JSONResponse(
                {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False, "background": True, "session_id": session_id},
                status_code=500,
            )

    try:
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            req.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(cwd_path),
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
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
        return JSONResponse({
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "background": False,
            "session_id": session_id,
            "command": req.command,
            "cwd": str(cwd_path),
            "elapsed_ms": elapsed_ms,
        })
    except Exception as exc:
        return JSONResponse(
            {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False, "session_id": session_id},
            status_code=500,
        )


class WriteRequest(BaseModel):
    path: str
    content: str
    session_id: str = ""
    encoding: str = "utf-8"


@app.post("/file/write")
async def file_write(req: WriteRequest):
    session_id = _safe_session(req.session_id)
    target = _safe_path(session_id, req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding=req.encoding)
    return {"ok": True, "session_id": session_id, "path": req.path, "size": target.stat().st_size}


@app.get("/file/read")
async def file_read(path: str = Query(...), session_id: str = Query("")):
    session_id = _safe_session(session_id)
    target = _safe_path(session_id, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path!r}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path!r}")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "session_id": session_id, "path": path, "content": content, "size": target.stat().st_size}


class LsRequest(BaseModel):
    path: str = "."
    session_id: str = ""
    recursive: bool = True


@app.post("/file/ls")
async def file_ls(req: LsRequest):
    session_id = _safe_session(req.session_id)
    target = _safe_path(session_id, req.path)
    if not target.exists():
        return {"ok": True, "session_id": session_id, "path": req.path, "entries": []}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path!r}")
    entries = []
    if req.recursive:
        for item in sorted(target.rglob("*")):
            entries.append({
                "path": str(item.relative_to(WORKSPACE / session_id)),
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
    else:
        for item in sorted(target.iterdir()):
            entries.append({
                "path": str(item.relative_to(WORKSPACE / session_id)),
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
    return {"ok": True, "session_id": session_id, "path": req.path, "entries": entries}


class DeleteRequest(BaseModel):
    path: str
    session_id: str = ""


@app.delete("/file/delete")
async def file_delete(req: DeleteRequest):
    session_id = _safe_session(req.session_id)
    target = _safe_path(session_id, req.path)
    if not target.exists():
        return {"ok": True, "session_id": session_id, "path": req.path, "message": "already absent"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "session_id": session_id, "path": req.path, "deleted": True}


@app.post("/workspace/reset")
async def workspace_reset(session_id: str = Query("")):
    sid = _safe_session(session_id)
    target = WORKSPACE / sid
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "session_id": sid, "message": "workspace reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8200, reload=False)
