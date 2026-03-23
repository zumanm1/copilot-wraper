"""
Playwright: noVNC on C3 must complete RFB handshake (status shows Connected).

Requires docker compose up for browser-auth (ports 6080 + 8001).
Skips when C3 is not reachable.

RFB over websockify can be flaky on some hosts (Docker Desktop, timing). Use
NOVNC_E2E_RETRIES (default 4) to control retries for the Connected assertion.

After Connected, C3 warms Chromium to /setup — we assert the VNC framebuffer is not
uniformly black (canvas sample + full-page screenshot under tests/reports/).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

NOVNC_URL = os.getenv("NOVNC_URL", "http://localhost:6080").rstrip("/")
C3_API = os.getenv("BROWSER_AUTH_URL", "http://localhost:8001").rstrip("/")
E2E_RETRIES = max(1, int(os.getenv("NOVNC_E2E_RETRIES", "4")))
E2E_RETRY_DELAY_S = float(os.getenv("NOVNC_E2E_RETRY_DELAY_S", "5"))
NOVNC_FRAMEBUF_WAIT_S = float(os.getenv("NOVNC_FRAMEBUF_WAIT_S", "10"))
NOVNC_FRAMEBUF_MIN_MEAN = float(os.getenv("NOVNC_FRAMEBUF_MIN_MEAN", "4"))


def _reports_dir() -> Path:
    d = Path(__file__).resolve().parent / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _canvas_mean_rgb(page) -> float:
    return page.evaluate(
        """() => {
          const c = document.querySelector('#noVNC_canvas') || document.querySelector('canvas');
          if (!c || !c.getContext) return -1;
          const ctx = c.getContext('2d', { willReadFrequently: true });
          if (!ctx) return -1;
          const w = Math.min(320, c.width || 0), h = Math.min(240, c.height || 0);
          if (w < 2 || h < 2) return -1;
          let data;
          try {
            data = ctx.getImageData(0, 0, w, h).data;
          } catch (e) {
            return -1;
          }
          let s = 0, n = 0;
          for (let i = 0; i < data.length; i += 16) {
            s += data[i] + data[i + 1] + data[i + 2];
            n += 3;
          }
          return n ? s / n : -1;
        }"""
    )


def _fetch_status_json() -> dict | None:
    try:
        with urllib.request.urlopen(f"{C3_API}/status", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def _c3_novnc_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{C3_API}/health", timeout=3) as r:
            body = r.read().decode("utf-8", errors="replace")
        if "browser-auth" not in body:
            return False
        with urllib.request.urlopen(f"{NOVNC_URL}/", timeout=3) as r2:
            if r2.status != 200:
                return False
        return True
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def _fetch_root_html() -> str:
    with urllib.request.urlopen(f"{NOVNC_URL}/", timeout=5) as r:
        return r.read().decode("utf-8", errors="replace")


def _expect_connected(
    page, setup_url: str, *, expect_resize_query: bool = False
) -> None:
    """Wait for RFB Connected; retry navigation + assertion on transient WebSocket failures."""
    last_err: Exception | None = None
    for attempt in range(E2E_RETRIES):
        try:
            page.goto(setup_url, wait_until="domcontentloaded")
            if expect_resize_query:
                expect(page).to_have_url(re.compile(r"resize=scale"), timeout=15_000)
            status = page.locator("#noVNC_status")
            expect(status).to_contain_text(
                re.compile(r"Connected", re.I), timeout=45_000
            )
            return
        except AssertionError as e:
            last_err = e
            if attempt < E2E_RETRIES - 1:
                time.sleep(E2E_RETRY_DELAY_S)
    raise AssertionError(
        f"noVNC did not reach Connected after {E2E_RETRIES} attempts"
    ) from last_err


@pytest.fixture(scope="module")
def _pw_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        yield browser
        browser.close()


@pytest.fixture
def novnc_page(_pw_browser):
    ctx = _pw_browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_navigation_timeout(45_000)
    page.set_default_timeout(30_000)
    yield page
    ctx.close()


class TestBrowserAuthNoVNC:
    def test_novnc_root_serves_client_html(self):
        """Smoke: / returns 200 and noVNC client markup (no browser required)."""
        if not _c3_novnc_ready():
            pytest.skip(
                f"C3 noVNC not ready ({NOVNC_URL}) — docker compose up -d browser-auth"
            )
        html = _fetch_root_html()
        assert "noVNC" in html.lower() or "rfb" in html.lower() or "novnc" in html.lower()
        assert "noVNC_status" in html or "vnc_auto" in html or "websockify" in html.lower()

    def test_root_url_reaches_connected(self, novnc_page):
        """GET / serves noVNC at same path (/?resize=… after one client redirect)."""
        if not _c3_novnc_ready():
            pytest.skip(
                f"C3 noVNC not ready ({NOVNC_URL}) — docker compose up -d browser-auth"
            )
        _expect_connected(novnc_page, f"{NOVNC_URL}/", expect_resize_query=True)

    def test_vnc_auto_reaches_connected(self, novnc_page):
        if not _c3_novnc_ready():
            pytest.skip(
                f"C3 noVNC not ready ({NOVNC_URL}) — docker compose up -d browser-auth"
            )
        _expect_connected(
            novnc_page,
            f"{NOVNC_URL}/vnc_auto.html?autoconnect=true&resize=scale",
            expect_resize_query=False,
        )

    def test_novnc_framebuffer_not_black_after_warm_screenshot(self, novnc_page):
        """
        Regression: connected but black VNC (empty X11 before warm). Expect some
        non-black pixels after warm + RFB catch-up; save PNG for manual review.
        """
        if not _c3_novnc_ready():
            pytest.skip(
                f"C3 noVNC not ready ({NOVNC_URL}) — docker compose up -d browser-auth"
            )
        _expect_connected(novnc_page, f"{NOVNC_URL}/", expect_resize_query=True)
        time.sleep(NOVNC_FRAMEBUF_WAIT_S)
        out = _reports_dir() / "novnc_playwright_after_warm.png"
        novnc_page.screenshot(path=str(out), full_page=True)

        mean = _canvas_mean_rgb(novnc_page)
        if mean < 0:
            pytest.fail(
                "noVNC canvas missing or getImageData failed (tainted canvas / WebGL render path)"
            )
        assert mean >= NOVNC_FRAMEBUF_MIN_MEAN, (
            f"VNC framebuffer too dark (mean RGB {mean:.2f} < {NOVNC_FRAMEBUF_MIN_MEAN}); "
            f"see {out} — possible black screen / warm failed"
        )

        st = _fetch_status_json()
        assert st is not None, "GET /status should return JSON"
        assert st.get("status") == "ok", st
        assert st.get("browser") == "running", st
        assert int(st.get("open_pages") or 0) >= 1, st
