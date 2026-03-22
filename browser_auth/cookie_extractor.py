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
# Cookies present in both anonymous and authenticated sessions
ANON_COOKIES = ["MUID", "MUIDB", "_EDGE_S", "_EDGE_V", "MSFPC"]
# Additional cookies only present when signed in to a Microsoft account
AUTH_COOKIES = ["_U", "_C_ETH", "SRCHHPGUSR", "__Host-copilot-anon"]
# Extraction succeeds if at least one anon cookie is present
REQUIRED_COOKIES = ["MUID"]

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

    # Remove stale Chrome lock files left by a previous container restart.
    # Chrome uses dangling symlinks for locks — Path.exists() returns False for
    # dangling symlinks, so we must use os.path.lexists() or os.remove() directly.
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = profile_dir / lock
        if os.path.lexists(lock_path):   # True even for dangling symlinks
            try:
                os.remove(lock_path)
                print(f"[cookie_extractor] Removed stale lock: {lock_path}")
            except OSError:
                pass

    _playwright = await async_playwright().start()
    _context = await _playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,               # headed so VNC/noVNC shows the browser
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,900",
            "--hide-crash-restore-bubble",
            "--test-type",
        ],
        ignore_default_args=["--enable-automation", "--disable-infobars"],
        viewport={"width": 1280, "height": 900},
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
    Detect login state. Primary check is URL-based (fast, reliable).
    If we're on copilot.microsoft.com and NOT on a Microsoft login page,
    we consider the session valid enough to extract cookies.
    """
    try:
        url = page.url
        # Definite not-logged-in: redirected to a Microsoft auth page
        if any(h in url for h in ("login.microsoft.com", "login.live.com",
                                   "login.microsoftonline.com", "account.live.com")):
            return False
        # On copilot.microsoft.com (or a subpath) — session is active
        if "copilot.microsoft.com" in url:
            return True
        # On bing.com or another Microsoft domain — check for auth cookie
        cookies = await page.context.cookies(["https://www.bing.com"])
        if any(c["name"] == "_U" for c in cookies):
            return True
        # Unknown page — treat as not confirmed
        return False
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
    """Update or append a KEY=VALUE line in the .env file.
    Writes directly (no atomic rename) because the file is a Docker bind mount
    which does not support cross-device os.replace().
    Access is serialised by the asyncio _lock in extract_and_save().
    """
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

    Path(env_path).write_text("".join(lines))


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

        # Step 1: Navigate to Copilot (skip if already there)
        current_url = page.url
        if "copilot.microsoft.com" not in current_url:
            print("[cookie_extractor] Navigating to copilot.microsoft.com...")
            try:
                await page.goto("https://copilot.microsoft.com",
                                wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(2)
            except Exception as e:
                return {"status": "error", "message": f"Failed to navigate to Copilot: {e}"}
        else:
            print(f"[cookie_extractor] Already on {current_url} — skipping navigation")

        # Step 2: Check login (up to 60 seconds for user to log in via noVNC)
        logged_in = await _is_logged_in(page)
        if not logged_in:
            print("[cookie_extractor] Not logged in. Waiting up to 60s for authentication via noVNC...")
            print("[cookie_extractor] Open http://localhost:6080 and log in to copilot.microsoft.com")
            deadline = time.time() + 60
            while time.time() < deadline:
                await asyncio.sleep(3)
                if await _is_logged_in(page):
                    print("[cookie_extractor] Login detected!")
                    logged_in = True
                    break
            if not logged_in:
                print("[cookie_extractor] WARNING: Not on copilot.microsoft.com. Extracting available cookies anyway.")

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
        has_auth = any(c in cookies for c in AUTH_COOKIES)
        mode = "authenticated" if has_auth else "anonymous"

        # Step 6: Write to .env (works for both authenticated and anonymous sessions)
        _patch_env(env_path, "COPILOT_COOKIES", cookie_str)
        _patch_env(env_path, "BING_COOKIES", cookie_str)

        msg = (
            f"Extracted {len(cookies)} cookies in {mode} mode."
            if has_auth else
            f"Extracted {len(cookies)} anonymous cookies (no Microsoft account sign-in detected). "
            "Copilot will work in anonymous mode with lower rate limits. "
            "Log in via the noVNC browser at http://localhost:6080 and re-run /extract for authenticated access."
        )
        print(f"[cookie_extractor] {msg}")
        return {
            "status": "ok",
            "authenticated": has_auth,
            "mode": mode,
            "cookies_extracted": len(cookies),
            "cookie_names": list(cookies.keys()),
            "message": msg,
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
