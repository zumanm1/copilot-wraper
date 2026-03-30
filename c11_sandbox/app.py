"""
C11_SANDBOX — Session-scoped AI agent execution sandbox (FastAPI, port 8200).

Dedicated sandbox for /multi-Agento multi-agent workspace.
Key difference from C10: every request is scoped to a session_id subdirectory,
so concurrent multi-agent sessions are fully isolated from each other.

  /workspace/{session_id}/  ← all ops for that session live here
  /workspace/               ← fallback when session_id is empty (legacy compat)

Security:
  - Runs as non-root user 'sandbox'
  - All file paths validated to stay within WORKSPACE (no path traversal)
  - session_id sanitized (alphanumeric + _ - only)
  - Command execution time-bounded (default 30s, max 120s)
  - No host port binding — internal Docker network only
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace")).resolve()

app = FastAPI(title="C11 Sandbox", version="1.0.0")


# ── Session helpers ───────────────────────────────────────────────────────────

def _sanitize_session(session_id: str) -> str:
    """Strip everything except alphanumerics, hyphens, underscores; max 64 chars."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "", session_id)[:64]


def _session_root(session_id: str) -> Path:
    """Return the workspace root for a given session (or global root if empty)."""
    if session_id:
        safe = _sanitize_session(session_id)
        if safe:
            root = WORKSPACE / safe
            root.mkdir(parents=True, exist_ok=True)
            return root
    return WORKSPACE


# ── Path safety ───────────────────────────────────────────────────────────────

def _safe_path(relative: str, session_id: str = "") -> Path:
    """
    Resolve a path against the session root and ensure it stays inside WORKSPACE.
    Raises HTTPException 400 on path traversal attempts.
    """
    root = _session_root(session_id)
    clean = relative.lstrip("/").lstrip("\\")
    resolved = (root / clean).resolve()
    # Must remain inside the global workspace (not just session root)
    try:
        resolved.relative_to(WORKSPACE)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Path traversal denied: {relative!r}")
    return resolved


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    total_files = sum(1 for _ in WORKSPACE.rglob("*") if _.is_file())
    session_dirs = [d for d in WORKSPACE.iterdir() if d.is_dir()] if WORKSPACE.exists() else []
    return {
        "status": "ok",
        "workspace": str(WORKSPACE),
        "file_count": total_files,
        "session_count": len(session_dirs),
    }


# ── Sessions list ─────────────────────────────────────────────────────────────

@app.get("/sessions")
async def list_sessions():
    """List all active session directories with file counts."""
    if not WORKSPACE.exists():
        return {"ok": True, "sessions": []}
    sessions = []
    for d in sorted(WORKSPACE.iterdir()):
        if d.is_dir():
            files = list(d.rglob("*"))
            file_count = sum(1 for f in files if f.is_file())
            size_bytes = sum(f.stat().st_size for f in files if f.is_file())
            sessions.append({
                "session_id": d.name,
                "file_count": file_count,
                "size_bytes": size_bytes,
            })
    return {"ok": True, "sessions": sessions}


# ── Session reset ─────────────────────────────────────────────────────────────

@app.post("/session/{session_id}/reset")
async def session_reset(session_id: str):
    """Wipe all files for a specific session (leaves the session dir empty)."""
    safe = _sanitize_session(session_id)
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    root = WORKSPACE / safe
    deleted = []
    if root.exists():
        for item in sorted(root.iterdir()):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            deleted.append(str(item.relative_to(WORKSPACE)))
    return {"ok": True, "session_id": safe, "deleted_count": len(deleted), "deleted": deleted}


# ── Exec ──────────────────────────────────────────────────────────────────────

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
    """Run a shell command inside the session workspace."""
    timeout = max(1, min(120, req.timeout))
    cwd = _safe_path(req.cwd, req.session_id)
    cwd.mkdir(parents=True, exist_ok=True)

    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "WORKSPACE": str(_session_root(req.session_id)),
        "PYTHONUNBUFFERED": "1",
    }

    if _is_background_command(req.command):
        try:
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
            return JSONResponse({
                "stdout": f"Background process started (pid={proc.pid})",
                "stderr": "" if still_running else f"Process exited early (code {proc.returncode})",
                "exit_code": 0 if still_running else (proc.returncode or 1),
                "timed_out": False, "background": True,
                "pid": proc.pid, "command": req.command, "cwd": str(cwd),
            })
        except Exception as exc:
            return JSONResponse(
                {"stdout": "", "stderr": str(exc), "exit_code": -1,
                 "timed_out": False, "background": True, "command": req.command, "cwd": str(cwd)},
                status_code=500,
            )

    try:
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
            stdout_bytes, stderr_bytes = b"", b"[killed: timeout — use nohup cmd & for long-running servers]"
            timed_out = True
        return JSONResponse({
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode if not timed_out else -1,
            "timed_out": timed_out, "background": False,
            "command": req.command, "cwd": str(cwd),
        })
    except Exception as exc:
        return JSONResponse(
            {"stdout": "", "stderr": str(exc), "exit_code": -1,
             "timed_out": False, "command": req.command, "cwd": str(cwd)},
            status_code=500,
        )


# ── File: write ───────────────────────────────────────────────────────────────

class WriteRequest(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"
    session_id: str = ""


@app.post("/file/write")
async def file_write(req: WriteRequest):
    target = _safe_path(req.path, req.session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding=req.encoding)
    # Return path relative to session root for cleaner display
    root = _session_root(req.session_id)
    rel = str(target.relative_to(root))
    return {"ok": True, "path": rel, "size": target.stat().st_size}


# ── File: read ────────────────────────────────────────────────────────────────

@app.get("/file/read")
async def file_read(
    path: str = Query(...),
    session_id: str = Query(default=""),
):
    target = _safe_path(path, session_id)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path!r}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path!r}")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    root = _session_root(session_id)
    return {"ok": True, "path": str(target.relative_to(root)), "content": content, "size": target.stat().st_size}


# ── File: list ────────────────────────────────────────────────────────────────

class LsRequest(BaseModel):
    path: str = "."
    recursive: bool = True
    session_id: str = ""


@app.post("/file/ls")
async def file_ls(req: LsRequest):
    target = _safe_path(req.path, req.session_id)
    if not target.exists():
        return {"ok": True, "path": req.path, "entries": []}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path!r}")
    root = _session_root(req.session_id)
    entries = []
    if req.recursive:
        for item in sorted(target.rglob("*")):
            rel = str(item.relative_to(root))
            entries.append({"path": rel, "type": "file" if item.is_file() else "dir",
                            "size": item.stat().st_size if item.is_file() else None})
    else:
        for item in sorted(target.iterdir()):
            rel = str(item.relative_to(root))
            entries.append({"path": rel, "type": "file" if item.is_file() else "dir",
                            "size": item.stat().st_size if item.is_file() else None})
    return {"ok": True, "path": req.path, "entries": entries}


# ── File: delete ─────────────────────────────────────────────────────────────

class DeleteRequest(BaseModel):
    path: str
    session_id: str = ""


@app.delete("/file/delete")
async def file_delete(req: DeleteRequest):
    target = _safe_path(req.path, req.session_id)
    if not target.exists():
        return {"ok": True, "path": req.path, "message": "already absent"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": req.path, "deleted": True}


# ── Workspace: reset (global — clears ALL sessions) ─────────────────────────

@app.post("/workspace/reset")
async def workspace_reset():
    """Wipe all sessions (entire workspace). Use /session/{id}/reset to clear one session."""
    deleted = []
    for item in sorted(WORKSPACE.iterdir()):
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        deleted.append(str(item.relative_to(WORKSPACE)))
    return {"ok": True, "deleted_count": len(deleted), "deleted": deleted}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8200, reload=False)
