"""
Playwright UI validation for C3 GET /setup (two portal URLs; M365 default).

Runs after other tests (see conftest collection order) to avoid asyncio conflicts
with Playwright's sync API.

Requires a running browser-auth container with current code:
  docker compose up -d --force-recreate browser-auth

Skips fast if /health is not browser-auth or /setup does not contain the new UI.
Does not perform Microsoft login — stop after UI checks; you sign in via noVNC then /extract.
"""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.request

import pytest
from playwright.sync_api import expect, sync_playwright

C3_URL = os.getenv("BROWSER_AUTH_URL", "http://localhost:8001").rstrip("/")


def _c3_setup_ui_ready() -> bool:
    """True when C3 is up and serving the portal setup page we expect."""
    try:
        with urllib.request.urlopen(f"{C3_URL}/health", timeout=3) as r:
            body = r.read().decode("utf-8", errors="replace")
        if "browser-auth" not in body:
            return False
        with urllib.request.urlopen(f"{C3_URL}/setup", timeout=3) as r2:
            html = r2.read().decode("utf-8", errors="replace")
        return (
            "Choose Copilot portal" in html
            and 'value="m365_hub"' in html
            and 'value="consumer"' in html
        )
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


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
def c3_page(_pw_browser):
    ctx = _pw_browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_navigation_timeout(20_000)
    page.set_default_timeout(15_000)
    yield page
    ctx.close()


class TestBrowserAuthC3SetupPage:
    def test_setup_two_portal_urls_m365_selected_by_default(self, c3_page):
        if not _c3_setup_ui_ready():
            pytest.skip(
                f"C3 setup UI not ready at {C3_URL} — "
                "docker compose up -d --force-recreate browser-auth"
            )
        c3_page.goto(f"{C3_URL}/setup", wait_until="domcontentloaded")
        expect(
            c3_page.get_by_role("heading", name=re.compile(r"Choose Copilot portal", re.I))
        ).to_be_visible()
        expect(c3_page.get_by_text("m365.cloud.microsoft", exact=False)).to_be_visible()
        expect(c3_page.get_by_text("copilot.microsoft.com", exact=False)).to_be_visible()
        m365 = c3_page.locator('input[type="radio"][value="m365_hub"]')
        consumer = c3_page.locator('input[type="radio"][value="consumer"]')
        expect(m365).to_be_checked()
        expect(consumer).not_to_be_checked()

    def test_setup_can_select_consumer_portal(self, c3_page):
        if not _c3_setup_ui_ready():
            pytest.skip(
                f"C3 setup UI not ready at {C3_URL} — "
                "docker compose up -d --force-recreate browser-auth"
            )
        c3_page.goto(f"{C3_URL}/setup", wait_until="domcontentloaded")
        c3_page.locator('input[type="radio"][value="consumer"]').click()
        expect(c3_page.locator('input[type="radio"][value="consumer"]')).to_be_checked()
        expect(c3_page.locator('input[type="radio"][value="m365_hub"]')).not_to_be_checked()
