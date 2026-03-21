"""
browser_auth/cookie_extractor.py
=================================
Headless Chromium cookie extractor for copilot.microsoft.com.

Strategy:
  1. Launch Chromium with a persistent profile
  2. Navigate to copilot.microsoft.com
  3. If not logged in: wait for user to authenticate via noVNC
  4. Extract cookies via Playwright CDP
  5. Build combined cookie string and write to /app/.env
  6. Signal Container 1 to hot-reload

The noVNC web UI at http://localhost:6080 lets the user
watch and interact with the browser.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import time
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page

# ── Targets ───────────────────────────────────────────────────────────────────
COPILOT_URL = "https://copilot.microsoft.com"
LOGIN_INDICATOR = "copilot.microsoft.com"

# All cookies to extract (from both bing.com and copilot.microsoft.com)
TARGET_COOKIES = {
    "copilot.microsoft.com": [
        "__cf_bm", "_C_ETH", "_EDGE_S", "MUID", "MUIDB", "_EDGE_V",
        "__Host-copilot-anon", "MSFPC",
    ],
    ".bing.com": [
        "_U", "MUID", "MUIDB", "SRCHHPGUSR", "SRCHD", "SRCHUID",
        "_EDGE_S", "_EDGE_V", "_RwBf",
    ],
}
REQUIRED_COOKIES = ["_U", "MUID"]

# ── State ──────────────────────────────────────────────────────────────────────
_browser_context: BrowserContext | None = None
_page: Page | None = None
_lock = asyncio.Lock()


async def get_context() -> BrowserContext:
    global _browser_context
    if _browser_context:
        return _browser_context
    playwright = await async_playwright().start()
    profile_dir = Path("/browser-profile")
    profile_dir.mkdir(exist_ok=True)
    _browser_context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,  # headed so VNC can show it
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
        ignore_https_errors=False,
    )
    return _browser_context


async def is_logged_in(page: Page) -> bool:
    """Check if copilot.microsoft.com session is authenticated."""
    try:
        # Look for the message input — only present when logged in or anon
        # Check cookies for _U (Microsoft auth token)
        cookies = await page.context.cookies(["https://www.bing.com"])
        return any(c["name"] == "_U" for c in cookies)
    except Exception:
        return False


async def wait_for_login(page: Page, timeout_seconds: int = 300) -> bool:
    """Navigate to Copilot and wait for user to log in."""
    print("[browser_auth] Navigating to copilot.microsoft.com...")
    await page.goto(COPILOT_URL, wait_until="domcontentloaded")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if await is_logged_in(page):
            print("[browser_auth] Login detected!")
            return True
        await asyncio.sleep(3)
    return False


async def extract_cookies(context: BrowserContext) -> dict[str, str]:
    """Pull all target cookies from browser context."""
    collected: dict[str, str] = {}

    # Get cookies for all relevant domains
    all_cookies = await context.cookies([
        "https://copilot.microsoft.com",
        "https://www.bing.com",
        "https://bing.com",
    ])

    for cookie in all_cookies:
        name = cookie["name"]
        value = cookie["value"]
        domain = cookie.get("domain", "")
        if not value:
            continue
        # Include if it's a target cookie
        for domain_pattern, names in TARGET_COOKIES.items():
            if name in names and domain_pattern.lstrip(".") in domain:
                if name not in collected:
                    collected[name] = value
                    print(f"[browser_auth] Extracted: {name} from {domain}")

    return collected


def build_cookie_string(cookies: dict[str, str]) -> str:
    return ";".join(f"{k}={v}" for k, v in cookies.items())


def patch_env_file(env_path: str, key: str, value: str) -> bool:
    """Atomically update a key in the .env file."""
    try:
        lines = Path(env_path).read_text().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []

    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            if lines[i].rstrip() != f"{key}={value}":
                lines[i] = f"{key}={value}\n"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}\n")

    tmp = env_path + ".tmp"
    Path(tmp).write_text("".join(lines))
    os.replace(tmp, env_path)
    return True


async def extract_and_save(env_path: str = "/app/.env") -> dict:
    """Main extraction flow. Returns result dict."""
    async with _lock:
        context = await get_context()

        # Reuse or create page
        global _page
        if not _page or _page.is_closed():
            _page = await context.new_page()

        # Navigate to Copilot and check/wait for login
        logged_in = await wait_for_login(_page, timeout_seconds=300)
        if not logged_in:
            return {"status": "error", "message": "Login timeout — user did not authenticate within 5 minutes"}

        # Wait a moment for cookies to settle
        await asyncio.sleep(2)

        cookies = await extract_cookies(context)
        missing = [r for r in REQUIRED_COOKIES if r not in cookies]
        if missing:
            return {
                "status": "error",
                "message": f"Missing required cookies: {missing}. Please log in with a Microsoft account.",
            }

        cookie_str = build_cookie_string(cookies)

        # Write to .env (both COPILOT_COOKIES and BING_COOKIES for compatibility)
        patch_env_file(env_path, "COPILOT_COOKIES", cookie_str)
        patch_env_file(env_path, "BING_COOKIES", cookie_str)

        return {
            "status": "ok",
            "cookies_extracted": len(cookies),
            "cookie_names": list(cookies.keys()),
        }
