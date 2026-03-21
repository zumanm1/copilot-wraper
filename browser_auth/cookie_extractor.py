"""
browser_auth/cookie_extractor.py
=================================
Headless Chromium cookie extractor for copilot.microsoft.com + bing.com.

Strategy:
  1. Launch Chromium (headed, visible via noVNC on :6080)
  2. Navigate to copilot.microsoft.com — waits for login if needed
  3. Navigate to bing.com — captures _U and other bing cookies
  4. Extract all session cookies from both domains via Playwright
  5. Write combined cookie string to /app/.env
  6. Signal Container 1 to hot-reload config

Key fixes vs v1:
  - Must navigate to EACH domain before calling context.cookies() — browsers
    only return cookies for URLs they've actually visited in the session.
  - is_logged_in() checks URL + page title, not cross-domain cookie presence.
  - Persistent profile stored in /browser-profile so login survives restarts.
"""
from __future__ import annotations
import asyncio
import os
import re
import time
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page, Browser

# ── Cookie targets ─────────────────────────────────────────────────────────────
# domain → list of cookie names to collect
TARGET_COOKIES = {
    "https://copilot.microsoft.com": [
        "__cf_bm", "_C_ETH", "_EDGE_S", "MUID", "MUIDB", "_EDGE_V",
        "__Host-copilot-anon", "MSFPC",
    ],
    "https://www.bing.com": [
        "_U", "MUID", "MUIDB", "SRCHHPGUSR", "SRCHD", "SRCHUID",
        "_EDGE_S", "_EDGE_V", "_RwBf",
    ],
}
REQUIRED_COOKIES = ["MUID"]      # minimum for anonymous access
AUTH_COOKIE = "_U"               # present only when logged into Microsoft account

# ── Singleton state ────────────────────────────────────────────────────────────
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()


async def _get_context() -> BrowserContext:
    """Return (or create) the persistent browser context."""
    global _playwright, _browser, _context

    if _context is not None:
        return _context

    profile_dir = Path(os.getenv("BROWSER_PROFILE_DIR", "/browser-profile"))
    profile_dir.mkdir(parents=True, exist_ok=True)

    _playwright = await async_playwright().start()
    _context = await _playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,               # headed so VNC/noVNC shows the browser
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,800",
        ],
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=False,
        accept_downloads=False,
    )
    return _context


async def _get_or_create_page(context: BrowserContext) -> Page:
    """Reuse an existing page or open a new one."""
    pages = context.pages
    for p in pages:
        if not p.is_closed():
            return p
    return await context.new_page()


async def _is_logged_in(page: Page) -> bool:
    """
    Detect Microsoft account login by checking for the user menu / avatar
    that only appears after sign-in on copilot.microsoft.com.
    Falls back to checking for _U cookie on bing.com.
    """
    try:
        # Check URL — if redirected to login page, definitely not logged in
        url = page.url
        if "login.microsoft.com" in url or "login.live.com" in url:
            return False

        # Check for the signed-in user avatar button (aria-label contains the
        # user's name or "Account manager")
        avatar = page.locator('[aria-label*="Account manager"], [data-testid="user-avatar"]')
        if await avatar.count() > 0:
            return True

        # Fallback: check cookies for _U on bing.com (requires prior navigation)
        cookies = await page.context.cookies(["https://www.bing.com"])
        return any(c["name"] == "_U" for c in cookies)
    except Exception:
        return False


async def _collect_cookies(context: BrowserContext, page: Page) -> dict[str, str]:
    """
    Navigate to each target domain and collect all target cookies.
    Must visit each domain explicitly — browsers scope cookies by domain.
    """
    collected: dict[str, str] = {}

    for url, names in TARGET_COOKIES.items():
        try:
            # Navigate to the domain so its cookies are accessible
            current = page.url
            if not current.startswith(url.rstrip("/")):
                print(f"[cookie_extractor] Navigating to {url} to collect cookies...")
                await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(1)  # allow session cookies to settle

            domain_cookies = await context.cookies([url])
            for cookie in domain_cookies:
                name = cookie["name"]
                value = cookie.get("value", "")
                if name in names and value and name not in collected:
                    collected[name] = value
                    print(f"[cookie_extractor] Got: {name} ({len(value)} chars)")
        except Exception as e:
            print(f"[cookie_extractor] Warning: could not collect from {url}: {e}")

    return collected


def _build_cookie_string(cookies: dict[str, str]) -> str:
    return ";".join(f"{k}={v}" for k, v in cookies.items())


def _patch_env(env_path: str, key: str, value: str) -> None:
    """Atomically update or append a KEY=VALUE line in the .env file."""
    try:
        lines = Path(env_path).read_text().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []

    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    found = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")

    tmp = env_path + ".tmp"
    Path(tmp).write_text("".join(lines))
    os.replace(tmp, env_path)


async def extract_and_save(env_path: str = "/app/.env") -> dict:
    """
    Main extraction flow.
    1. Launch browser (or reuse existing).
    2. Navigate to copilot.microsoft.com.
    3. If not logged in, wait up to 5 min for user to authenticate via noVNC.
    4. Collect cookies from copilot + bing.
    5. Write to .env.
    Returns a result dict with status and cookie info.
    """
    async with _lock:
        context = await _get_context()
        page = await _get_or_create_page(context)

        # Step 1: Navigate to Copilot
        print("[cookie_extractor] Navigating to copilot.microsoft.com...")
        try:
            await page.goto("https://copilot.microsoft.com", wait_until="domcontentloaded", timeout=20_000)
        except Exception as e:
            return {"status": "error", "message": f"Failed to navigate to Copilot: {e}"}

        # Step 2: Wait for login (up to 5 minutes via noVNC)
        logged_in = await _is_logged_in(page)
        if not logged_in:
            print("[cookie_extractor] Not logged in. Waiting for user to authenticate via noVNC...")
            print("[cookie_extractor] Open http://localhost:6080/vnc.html and log in to copilot.microsoft.com")
            deadline = time.time() + 300  # 5-minute timeout
            while time.time() < deadline:
                await asyncio.sleep(4)
                if await _is_logged_in(page):
                    print("[cookie_extractor] Login detected!")
                    logged_in = True
                    break
            if not logged_in:
                print("[cookie_extractor] WARNING: Login timeout. Extracting available cookies anyway.")

        # Wait for cookies to stabilize after login
        await asyncio.sleep(2)

        # Step 3: Collect cookies from all domains
        cookies = await _collect_cookies(context, page)

        # Step 4: Navigate back to Copilot for a clean state
        try:
            await page.goto("https://copilot.microsoft.com", wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass

        # Step 5: Validate
        missing = [r for r in REQUIRED_COOKIES if r not in cookies]
        if missing:
            return {
                "status": "error",
                "message": f"Missing required cookies: {missing}. Is the browser logged in?",
                "cookies_found": list(cookies.keys()),
            }

        cookie_str = _build_cookie_string(cookies)
        has_auth = AUTH_COOKIE in cookies

        # Step 6: Write to .env
        _patch_env(env_path, "COPILOT_COOKIES", cookie_str)
        _patch_env(env_path, "BING_COOKIES", cookie_str)

        print(f"[cookie_extractor] Saved {len(cookies)} cookies to {env_path}")
        return {
            "status": "ok",
            "authenticated": has_auth,
            "cookies_extracted": len(cookies),
            "cookie_names": list(cookies.keys()),
            "message": "Cookies saved. Trigger /v1/reload-config on Container 1 to apply.",
        }


async def get_context() -> BrowserContext:
    """Public alias for _get_context()."""
    return await _get_context()


async def close():
    """Gracefully shut down browser."""
    global _context, _browser, _playwright
    if _context:
        await _context.close()
        _context = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
