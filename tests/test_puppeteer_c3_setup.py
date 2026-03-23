"""
Puppeteer: C3 /setup open-portal button (validate_c3_setup_button.mjs).

Requires Node.js and: cd tests/puppeteer_novnc && npm install
Skips when C3 not reachable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUPPETEER_DIR = ROOT / "tests" / "puppeteer_novnc"
VALIDATE_SCRIPT = PUPPETEER_DIR / "validate_c3_setup_button.mjs"
NODE_MODULES = PUPPETEER_DIR / "node_modules"


def _c3_up() -> bool:
    base = os.getenv("BROWSER_AUTH_URL", "http://localhost:8001").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as r:
            body = r.read().decode("utf-8", errors="replace")
        if "browser-auth" not in body:
            return False
        with urllib.request.urlopen(f"{base}/setup", timeout=3) as r2:
            html = r2.read().decode("utf-8", errors="replace")
        return 'id="openPortalBtn"' in html
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
@pytest.mark.skipif(
    not NODE_MODULES.is_dir(),
    reason="npm install in tests/puppeteer_novnc (see package.json)",
)
def test_puppeteer_c3_setup_open_portal_button():
    if not _c3_up():
        pytest.skip("C3 browser-auth not reachable — docker compose up -d browser-auth")
    if not VALIDATE_SCRIPT.is_file():
        pytest.fail(f"Missing {VALIDATE_SCRIPT}")

    env = {
        **os.environ,
        "BROWSER_AUTH_URL": os.getenv("BROWSER_AUTH_URL", "http://127.0.0.1:8001"),
    }
    r = subprocess.run(
        ["node", str(VALIDATE_SCRIPT)],
        cwd=str(PUPPETEER_DIR),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
