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
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace")).resolve()

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


@app.get("/health")
async def health():
    files = sum(1 for item in WORKSPACE.rglob("*") if item.is_file())
    return {
        "status": "ok",
        "workspace": str(WORKSPACE),
        "file_count": files,
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
    timeout = max(1, min(120, req.timeout))
    cwd = _safe_path(req.cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    session_id = (req.session_id or "").strip() or ("c12b_" + uuid.uuid4().hex[:10])

    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "WORKSPACE": str(WORKSPACE),
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
                "timed_out": False,
                "background": True,
                "pid": proc.pid,
                "session_id": session_id,
                "command": req.command,
                "cwd": str(cwd),
            })
        except Exception as exc:
            return JSONResponse(
                {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False, "background": True, "session_id": session_id},
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
            stdout_bytes, stderr_bytes = b"", b"[killed: timeout - use nohup cmd & for long-running servers]"
            timed_out = True

        return JSONResponse({
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode if not timed_out else -1,
            "timed_out": timed_out,
            "background": False,
            "session_id": session_id,
            "command": req.command,
            "cwd": str(cwd),
        })
    except Exception as exc:
        return JSONResponse(
            {"stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False, "session_id": session_id, "command": req.command, "cwd": str(cwd)},
            status_code=500,
        )


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
