"""
C10_SANDBOX — Isolated AI agent execution sandbox (FastAPI, port 8100).

Provides a secure REST API for the AI agent to:
  - Execute shell commands with timeout
  - Create / read / list / delete files in /workspace
  - Reset the workspace (wipe all files)

Security:
  - Runs as non-root user 'sandbox'
  - All file paths are validated to stay within WORKSPACE (no path traversal)
  - Command execution is time-bounded (default 30s, max 120s)
  - No host port binding — internal Docker network only
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace")).resolve()

app = FastAPI(title="C10 Sandbox", version="1.0.0")


# ── Path safety ───────────────────────────────────────────────────────────────

def _safe_path(relative: str) -> Path:
    """
    Resolve a relative path against WORKSPACE and ensure it stays inside.
    Raises HTTPException 400 on path traversal attempts.
    """
    # Strip leading slashes so Path(WORKSPACE / "/etc/passwd") doesn't escape
    clean = relative.lstrip("/").lstrip("\\")
    resolved = (WORKSPACE / clean).resolve()
    try:
        resolved.relative_to(WORKSPACE)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Path traversal denied: {relative!r}")
    return resolved


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    files = sum(1 for _ in WORKSPACE.rglob("*") if _.is_file())
    return {"status": "ok", "workspace": str(WORKSPACE), "file_count": files}


# ── Exec ──────────────────────────────────────────────────────────────────────

class ExecRequest(BaseModel):
    command: str
    timeout: int = 30    # seconds; capped at 120
    cwd: str = "."       # relative to WORKSPACE


def _is_background_command(cmd: str) -> bool:
    """Detect commands that should run in background without waiting."""
    stripped = cmd.strip()
    return (
        stripped.endswith("&")
        or "nohup " in stripped
        or stripped.startswith("nohup ")
    )


@app.post("/exec")
async def exec_command(req: ExecRequest):
    """
    Run a shell command inside the workspace.
    Returns {stdout, stderr, exit_code, timed_out}.

    Background commands (nohup / ending with &) are started in a new
    process group and return immediately — they keep running after this
    request completes. Use /exec again with `sleep N && curl ...` to
    verify the server started.
    """
    timeout = max(1, min(120, req.timeout))
    cwd = _safe_path(req.cwd)
    cwd.mkdir(parents=True, exist_ok=True)

    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "WORKSPACE": str(WORKSPACE),
        "PYTHONUNBUFFERED": "1",
    }

    # ── Background command: start and return immediately ──────────────────────
    if _is_background_command(req.command):
        try:
            # start_new_session=True puts the child in a new process group so
            # it is NOT killed when our shell exits and doesn't inherit our PIPE fds.
            proc = await asyncio.create_subprocess_shell(
                req.command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
                env=env,
                start_new_session=True,
            )
            # Give it 1s to fail-fast (e.g. syntax error)
            await asyncio.sleep(1)
            still_running = proc.returncode is None
            return JSONResponse({
                "stdout": f"Background process started (pid={proc.pid})",
                "stderr": "" if still_running else f"Process exited early (code {proc.returncode})",
                "exit_code": 0 if still_running else (proc.returncode or 1),
                "timed_out": False,
                "background": True,
                "pid": proc.pid,
                "command": req.command,
                "cwd": str(cwd),
            })
        except Exception as exc:
            return JSONResponse(
                {"stdout": "", "stderr": str(exc), "exit_code": -1,
                 "timed_out": False, "background": True, "command": req.command, "cwd": str(cwd)},
                status_code=500,
            )

    # ── Foreground command: wait for completion ───────────────────────────────
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
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            timed_out = False
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            stdout_bytes, stderr_bytes = b"", b"[killed: timeout - use nohup cmd & for long-running servers]"
            timed_out = True

        return JSONResponse({
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode if not timed_out else -1,
            "timed_out": timed_out,
            "background": False,
            "command": req.command,
            "cwd": str(cwd),
        })
    except Exception as exc:
        return JSONResponse(
            {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False, "command": req.command, "cwd": str(cwd)},
            status_code=500,
        )


# ── File: write ───────────────────────────────────────────────────────────────

class WriteRequest(BaseModel):
    path: str       # relative path inside workspace
    content: str    # file content (text)
    encoding: str = "utf-8"


@app.post("/file/write")
async def file_write(req: WriteRequest):
    """Create or overwrite a file in the workspace."""
    target = _safe_path(req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding=req.encoding)
    return {"ok": True, "path": str(target.relative_to(WORKSPACE)), "size": target.stat().st_size}


# ── File: read ────────────────────────────────────────────────────────────────

@app.get("/file/read")
async def file_read(path: str = Query(..., description="Relative path inside workspace")):
    """Read a text file from the workspace."""
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


# ── File: list ────────────────────────────────────────────────────────────────

class LsRequest(BaseModel):
    path: str = "."     # relative directory to list
    recursive: bool = True


@app.post("/file/ls")
async def file_ls(req: LsRequest):
    """List files (and directories) in a workspace directory."""
    target = _safe_path(req.path)
    if not target.exists():
        return {"ok": True, "path": req.path, "entries": []}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path!r}")

    entries = []
    if req.recursive:
        for item in sorted(target.rglob("*")):
            rel = str(item.relative_to(WORKSPACE))
            entries.append({
                "path": rel,
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
    else:
        for item in sorted(target.iterdir()):
            rel = str(item.relative_to(WORKSPACE))
            entries.append({
                "path": rel,
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
    return {"ok": True, "path": req.path, "entries": entries}


# ── File: delete ─────────────────────────────────────────────────────────────

class DeleteRequest(BaseModel):
    path: str


@app.delete("/file/delete")
async def file_delete(req: DeleteRequest):
    """Delete a file or directory from the workspace."""
    target = _safe_path(req.path)
    if not target.exists():
        return {"ok": True, "path": req.path, "message": "already absent"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": req.path, "deleted": True}


# ── Workspace: reset ─────────────────────────────────────────────────────────

@app.post("/workspace/reset")
async def workspace_reset():
    """Wipe all files in the workspace (reset to empty)."""
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
    uvicorn.run("app:app", host="0.0.0.0", port=8100, reload=False)
