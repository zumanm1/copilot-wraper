"""
cookie_manager/updater.py
=========================
Atomically patches the project's .env file with fresh cookie values
and signals the running FastAPI app to hot-reload its config.

Atomic write strategy
---------------------
We write to `.env.tmp` first, then call `os.replace()` to rename it
over the real `.env`.  On POSIX systems `os.replace()` is atomic at the
filesystem level — if the process is killed mid-write the original .env
is never corrupted.

Hot-reload strategy
-------------------
After updating .env we POST to /v1/reload-config so the running server
picks up the new cookies without a Docker restart.  Falls back silently
if the server is not running (e.g. first-time setup).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

import httpx


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_env(path: Path) -> list[str]:
    """Return the raw lines of an .env file (preserves comments, ordering)."""
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _set_or_append(lines: list[str], key: str, value: str) -> list[str]:
    """
    Replace the VALUE of `key` if it already appears, else append a new line.
    Handles quoted and unquoted values; leaves comment lines untouched.
    """
    pattern = re.compile(rf"^({re.escape(key)}\s*=\s*)(.*?)(\s*)$")
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        m = pattern.match(line.rstrip("\n\r"))
        if m and not line.lstrip().startswith("#"):
            new_lines.append(f"{key}={value}\n")
            replaced = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")
    if not replaced:
        # Ensure we start on a fresh line
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")
    return new_lines


# ── Public API ────────────────────────────────────────────────────────────────

def patch_env(env_path: str | Path, updates: dict[str, str]) -> bool:
    """
    Patch `env_path` with the key=value pairs in `updates`.

    Returns True if the file was actually changed, False if every value was
    already present and identical (so the caller can skip the hot-reload).

    Writes atomically: .env.tmp → os.replace() → .env
    """
    path = Path(env_path)
    lines = _parse_env(path)

    # Detect current values
    current: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line.rstrip("\n\r"))
        if m and not line.lstrip().startswith("#"):
            current[m.group(1)] = m.group(2)

    # Check if anything actually changed
    changed = any(current.get(k) != v for k, v in updates.items() if v is not None)
    if not changed:
        return False

    # Apply changes
    for key, value in updates.items():
        if value is not None:
            lines = _set_or_append(lines, key, value)

    # Atomic write
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp_path, path)
    return True


def notify_app(base_url: str = "http://localhost:8000", timeout: float = 5.0) -> bool:
    """
    POST /v1/reload-config to the running server so it picks up the new
    cookies without a restart.

    Returns True on success, False if the server is not reachable.
    """
    url = base_url.rstrip("/") + "/v1/reload-config"
    try:
        resp = httpx.post(url, timeout=timeout)
        if resp.status_code == 200:
            print(f"[cookie_manager] Hot-reload OK — server acknowledged new config.")
            return True
        else:
            print(
                f"[cookie_manager] Hot-reload returned HTTP {resp.status_code}: {resp.text}",
                file=sys.stderr,
            )
            return False
    except httpx.ConnectError:
        # Server not running — that's fine during initial setup or if Docker is down
        print(
            "[cookie_manager] Server not reachable — skipping hot-reload "
            "(config will be picked up on next start).",
            file=sys.stderr,
        )
        return False
    except Exception as exc:
        print(f"[cookie_manager] Hot-reload error: {exc}", file=sys.stderr)
        return False


def update_and_reload(
    env_path: str | Path,
    cookies: dict[str, Optional[str]],
    app_url: str = "http://localhost:8000",
) -> dict[str, bool]:
    """
    High-level helper: patch .env, then hot-reload the server if anything changed.

    `cookies` may contain None values (service not found) — those keys are skipped.

    Returns {"env_changed": bool, "reload_ok": bool}.
    """
    valid = {k: v for k, v in cookies.items() if v is not None}
    if not valid:
        print("[cookie_manager] No cookies to write — nothing extracted.", file=sys.stderr)
        return {"env_changed": False, "reload_ok": False}

    changed = patch_env(env_path, valid)
    if changed:
        print(f"[cookie_manager] .env updated with: {list(valid.keys())}")
        reload_ok = notify_app(app_url)
    else:
        print("[cookie_manager] Cookies unchanged — .env not modified.")
        reload_ok = False

    return {"env_changed": changed, "reload_ok": reload_ok}


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile, json
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("BING_COOKIES=old_value\nCOPILOT_STYLE=balanced\n")
        tmp = f.name

    result = update_and_reload(
        tmp,
        {"BING_COOKIES": "new_value_abc", "CHATGPT_COOKIES": "tok123"},
        app_url="http://localhost:8000",   # likely not running in test
    )
    print(json.dumps(result, indent=2))
    with open(tmp) as f:
        print(f.read())
    os.unlink(tmp)
