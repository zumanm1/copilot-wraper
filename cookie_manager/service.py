"""
cookie_manager/service.py
=========================
Scheduler that extracts cookies from Chrome/Firefox every 15 minutes
and updates the project's .env file, then hot-reloads the running server.

Usage
-----
    # Foreground (for debugging):
    python3 cookie_manager/service.py

    # Background via macOS LaunchAgent (auto-installs via setup.sh):
    launchctl load ~/Library/LaunchAgents/com.copilot-wrapper.cookies.plist

Environment variables (optional overrides)
------------------------------------------
    COOKIE_REFRESH_INTERVAL   Seconds between refreshes (default: 900 = 15 min)
    COOKIE_APP_URL            Base URL of the FastAPI server (default: http://localhost:8000)
    COOKIE_ENV_PATH           Path to the .env file to patch (default: auto-detected)
"""
from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import schedule

# ── Resolve project paths ─────────────────────────────────────────────────────
# service.py lives at <project>/cookie_manager/service.py
# The .env file lives at <project>/.env
_THIS_DIR   = Path(__file__).parent.resolve()
_PROJECT_DIR = _THIS_DIR.parent

ENV_PATH    = Path(os.getenv("COOKIE_ENV_PATH", str(_PROJECT_DIR / ".env")))
APP_URL     = os.getenv("COOKIE_APP_URL", "http://localhost:8000")
INTERVAL    = int(os.getenv("COOKIE_REFRESH_INTERVAL", "900"))   # seconds

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cookie_manager] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("cookie_manager")


# ── Lazy imports (extractor / updater) ───────────────────────────────────────
# Imported lazily so missing pycryptodome only fails at first refresh, not import.

def _import_deps():
    """Import extractor and updater; return (extract_all, update_and_reload) or raise."""
    # Add cookie_manager dir to sys.path so sibling imports work
    cm_dir = str(_THIS_DIR)
    if cm_dir not in sys.path:
        sys.path.insert(0, cm_dir)
    proj_dir = str(_PROJECT_DIR)
    if proj_dir not in sys.path:
        sys.path.insert(0, proj_dir)

    from extractor import extract_all
    from updater   import update_and_reload
    return extract_all, update_and_reload


# ── Core refresh logic ────────────────────────────────────────────────────────

def refresh() -> None:
    """Extract cookies → patch .env → hot-reload server."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("Starting cookie refresh…")
    try:
        extract_all, update_and_reload = _import_deps()
    except ImportError as exc:
        log.error(
            "Import failed — is pycryptodome installed? "
            "Run: pip3 install -r cookie_manager/requirements.txt\n  %s", exc,
        )
        return

    try:
        cookies = extract_all()
    except Exception as exc:
        log.error("extract_all() raised: %s", exc)
        return

    found    = [k for k, v in cookies.items() if v]
    missing  = [k for k, v in cookies.items() if not v]

    if found:
        log.info("Extracted: %s", found)
    if missing:
        log.warning(
            "Not extracted (not logged in or browser closed?): %s", missing
        )

    try:
        result = update_and_reload(ENV_PATH, cookies, APP_URL)
        if result["env_changed"]:
            log.info(".env updated. Hot-reload: %s", "OK" if result["reload_ok"] else "skipped")
        else:
            log.info("Cookies unchanged — .env not modified.")
    except Exception as exc:
        log.error("update_and_reload() raised: %s", exc)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def main() -> None:
    log.info(
        "Cookie manager started. Refresh every %d seconds (%d min). "
        "ENV=%s  APP=%s",
        INTERVAL, INTERVAL // 60, ENV_PATH, APP_URL,
    )

    # Run immediately on start
    refresh()

    # Schedule future runs
    schedule.every(INTERVAL).seconds.do(refresh)

    log.info("Scheduler running. Press Ctrl-C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)   # check every 30 s; fine-grained enough for 15-min interval


if __name__ == "__main__":
    main()
