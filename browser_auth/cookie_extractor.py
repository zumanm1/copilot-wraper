"""
browser_auth/cookie_extractor.py
=================================
Headless Chromium cookie extractor for Copilot / M365 hub + bing.com.

Strategy:
  1. Launch Chromium (headed, visible via noVNC on :6080)
  2. Navigate to portal (consumer or m365.cloud.microsoft per .env) — waits for login if needed
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
from urllib.parse import urlparse
from playwright.async_api import async_playwright, BrowserContext, Page, Browser

from portal_urls import m365_hub_default_landing, normalize_copilot_portal_url

# ── Cookie targets ─────────────────────────────────────────────────────────────
# domain → list of cookie names to collect (must visit each URL before context.cookies)
_COPILOT_PORTAL_COOKIE_NAMES = [
    "__cf_bm", "_C_ETH", "_EDGE_S", "MUID", "MUIDB", "_EDGE_V",
    "__Host-copilot-anon", "MSFPC",
]
_M365_HUB_COOKIE_NAMES = [
    # MS ecosystem cookies shared across portals
    "__cf_bm", "_C_ETH", "_EDGE_S", "MUID", "MUIDB", "_EDGE_V",
    "__Host-copilot-anon", "MSFPC",
    # M365-specific session cookies (set by m365.cloud.microsoft)
    "OH.SID", "OH.FLID", "OH.DCAffinity",
    # Auth cookies that may appear after M365 login
    "_U", "SRCHHPGUSR", "SRCHD", "SRCHUID",
]

_BING_COOKIE_NAMES = [
    "_U", "MUID", "MUIDB", "SRCHHPGUSR", "SRCHD", "SRCHUID",
    "_EDGE_S", "_EDGE_V", "_RwBf",
]

_VALID_PROFILES = frozenset({"consumer", "m365_hub"})

# M365 chat may load on canonical host or the common *.microsoft.com alias; both
# can carry distinct cookies.  Extraction stays on these hosts only — no
# navigation to bing.com or copilot.microsoft.com to avoid disrupting the
# user's M365 session.  (Phase B will route C1 WSS through m365 APIs.)
_M365_PORTAL_URLS = (
    "https://m365.cloud.microsoft",
    "https://m365.cloud.microsoft.com",
)


def target_cookies_for_profile(profile: str) -> list[tuple[str, list[str]]]:
    profile = (profile or "consumer").strip().lower()
    if profile not in _VALID_PROFILES:
        profile = "consumer"
    bing: tuple[str, list[str]] = ("https://www.bing.com", _BING_COOKIE_NAMES)
    if profile == "m365_hub":
        # M365 hub: only visit m365 portal URLs — do NOT navigate to bing.com
        # or copilot.microsoft.com to avoid disrupting the user's M365 session.
        out: list[tuple[str, list[str]]] = []
        for u in _M365_PORTAL_URLS:
            out.append((u, _M365_HUB_COOKIE_NAMES))
        return out
    # Consumer profile: copilot.microsoft.com + bing.com
    return [
        ("https://copilot.microsoft.com", _COPILOT_PORTAL_COOKIE_NAMES),
        bing,
    ]


def _read_env_keys(env_path: str, keys: tuple[str, ...]) -> dict[str, str]:
    """Parse KEY=value from a mounted .env (no python-dotenv)."""
    out: dict[str, str] = {}
    try:
        text = Path(env_path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k in keys:
            out[k] = v.strip().strip('"').strip("'")
    return out


def portal_settings_from_env_file(env_path: str) -> tuple[str, str, str]:
    """
    Returns (profile, portal_base_url, api_base_url) from disk so C3 picks up
    /setup changes without container restart. Empty strings mean use config defaults.
    """
    data = _read_env_keys(
        env_path,
        (
            "COPILOT_PORTAL_PROFILE",
            "COPILOT_PORTAL_BASE_URL",
            "COPILOT_PORTAL_API_BASE_URL",
        ),
    )
    profile = (data.get("COPILOT_PORTAL_PROFILE") or "m365_hub").strip().lower()
    if profile not in _VALID_PROFILES:
        profile = "consumer"
    return (
        profile,
        (data.get("COPILOT_PORTAL_BASE_URL") or "").strip(),
        (data.get("COPILOT_PORTAL_API_BASE_URL") or "").strip(),
    )


def portal_landing_url(profile: str, portal_base_override: str) -> str:
    """First navigation URL for login + cookie scope (no trailing slash)."""
    if portal_base_override:
        u = portal_base_override.strip().rstrip("/")
        if not u.startswith("http://") and not u.startswith("https://"):
            u = "https://" + u.lstrip("/")
        return normalize_copilot_portal_url(u).rstrip("/")
    if (profile or "m365_hub").strip().lower() == "m365_hub":
        return normalize_copilot_portal_url(m365_hub_default_landing()).rstrip("/")
    return "https://copilot.microsoft.com"
# Cookies present in both anonymous and authenticated sessions
ANON_COOKIES = ["MUID", "MUIDB", "_EDGE_S", "_EDGE_V", "MSFPC", "__Host-copilot-anon", "_C_ETH", "SRCHHPGUSR"]
# Additional cookies only present when signed in to a Microsoft account
AUTH_COOKIES = ["_U"]
# Extraction succeeds if required auth/session cookies are present (profile-dependent)
REQUIRED_COOKIES_CONSUMER = ["MUID"]          # bing/copilot domain cookie
REQUIRED_COOKIES_M365 = ["OH.SID"]            # signed-in M365 session cookie
# Legacy alias for tests that import REQUIRED_COOKIES
REQUIRED_COOKIES = REQUIRED_COOKIES_CONSUMER


def required_cookies_for_profile(profile: str) -> list[str]:
    """Return the list of cookies that must be present for extraction to succeed."""
    if (profile or "consumer").strip().lower() == "m365_hub":
        return REQUIRED_COOKIES_M365
    return REQUIRED_COOKIES_CONSUMER

# ── Singleton state ────────────────────────────────────────────────────────────
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()
_DISMISS_AUTH_DIALOG = os.getenv("BROWSER_AUTH_AUTO_DISMISS_AUTH_DIALOG", "false").strip().lower() == "true"


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
            "--disable-quic",
            "--window-size=1280,1024",
            "--window-position=0,0",
            "--start-maximized",
            "--hide-crash-restore-bubble",
            "--test-type",
            # Allow M365 silent-auth popup windows (token refresh flow).
            # Without this, Chromium blocks the hidden login.microsoftonline.com
            # popup that M365 uses for silent token renewal, causing the
            # 'Authentication required' dialog and ?from=PopupFailed redirects.
            "--disable-popup-blocking",
        ],
        ignore_default_args=["--enable-automation", "--disable-infobars"],
        viewport=None,    # let --start-maximized + --window-size fill the Xvfb display
        ignore_https_errors=False,
        accept_downloads=False,
    )

    # Handle M365 silent-auth popup windows.
    # M365 opens a hidden popup to login.microsoftonline.com to silently refresh
    # its session token. We must let it complete and then close it.
    def _on_new_page(popup: Page) -> None:
        asyncio.ensure_future(_handle_auth_popup(popup))

    _context.on("page", _on_new_page)

    # Keep manual mouse/keyboard control by default.
    # Opt-in only when explicit auto-dismiss is desired.
    if _DISMISS_AUTH_DIALOG:
        asyncio.ensure_future(_auth_dialog_monitor())

    return _context


async def _handle_auth_popup(popup: Page) -> None:
    """
    Allow M365 silent-auth popup windows to complete their OAuth flow then close.
    These are the hidden login.microsoftonline.com popups that refresh session tokens.
    Closing them after load mimics normal browser behaviour and prevents the
    'Authentication required' dialog from appearing.
    """
    try:
        url = popup.url
        # Ignore regular/new tabs (start as about:blank); only handle auth popups.
        if url in ("", "about:blank"):
            return
        auth_hosts = ("login.microsoftonline.com", "login.live.com", "account.live.com")
        if not any(h in url for h in auth_hosts):
            return
        print(f"[cookie_extractor] Auth popup opened: {url[:80]}")
        # Wait for the popup to finish its auth redirect (up to 10s)
        await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
        final_url = popup.url
        print(f"[cookie_extractor] Auth popup final URL: {final_url[:80]} — closing")
        await popup.close()
    except Exception as e:
        print(f"[cookie_extractor] Auth popup handler error (non-fatal): {e}")
        try:
            await popup.close()
        except Exception:
            pass


async def _auth_dialog_monitor() -> None:
    """
    Background task: every 15 seconds, check all open pages for the M365
    'Authentication required' dialog and dismiss it by clicking 'Continue'.
    This is the fallback for when the popup handler alone is insufficient.
    """
    await asyncio.sleep(15)  # initial delay — let browser settle
    while True:
        try:
            if _context is not None:
                for page in list(_context.pages):
                    try:
                        if page.is_closed():
                            continue
                        content = (await page.content()).lower()
                        if "authentication required" in content and "continue" in content:
                            print("[cookie_extractor] Auth dialog detected — auto-clicking Continue")
                            # Find and click the Continue button
                            btn = page.locator("button", has_text="Continue")
                            if await btn.count() > 0:
                                await btn.first.click(timeout=5_000)
                                print("[cookie_extractor] Auth dialog dismissed")
                                await asyncio.sleep(3)  # let re-auth complete
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(15)


async def _get_or_create_page(context: BrowserContext) -> Page:
    """Reuse an existing page or open a new one."""
    pages = context.pages
    for p in pages:
        if not p.is_closed():
            return p
    return await context.new_page()


async def _is_logged_in(
    page: Page, portal_host_markers: tuple[str, ...], profile: str = "consumer"
) -> bool:
    """
    Detect login state from the current page WITHOUT navigating away.

    Previous implementation navigated to bing.com on every poll to check for
    the _U cookie, causing a visible bounce loop (bing → portal → bing …).
    The _U cookie is collected later by _collect_cookies() which visits bing
    exactly once after login is confirmed.

    Checks (fast, no navigation):
      1. If URL is on a Microsoft auth page → not logged in.
      2. If page shows 'Authentication required' dialog → not logged in.
      3. If URL is on a known portal host and profile-auth signal exists
         (cookie or signed-in UI markers) → logged in.
      4. If on about:blank / setup page → not logged in.
    """
    try:
        url = page.url
        # Definite not-logged-in: redirected to a Microsoft auth page
        if any(h in url for h in ("login.microsoft.com", "login.live.com",
                                   "login.microsoftonline.com", "account.live.com")):
            return False
        # M365 shell can show an in-app auth gate ("Authentication required" + Continue)
        # even while URL remains on the portal host; treat this as not logged in.
        try:
            html = (await page.content()).lower()
            if "authentication required" in html and "continue" in html:
                return False
        except Exception:
            pass

        # Must be on one of the expected portal hosts.
        on_portal = any(marker in url for marker in portal_host_markers)
        if not on_portal:
            return False

        # Profile-aware auth detection: avoid "URL only" false positives.
        cookie_names: set[str] = set()
        try:
            jar = await page.context.cookies([url])
            cookie_names = {
                c.get("name", "")
                for c in jar
                if isinstance(c, dict) and c.get("name")
            }
        except Exception:
            pass

        if (profile or "").strip().lower() == "m365_hub":
            # Strong signed-in signal on m365.
            if "OH.SID" in cookie_names:
                return True
            # Fallback UI heuristics when cookie inspection lags.
            signed_in_ui_markers = ("my account", "sign out", "logout")
            if any(tok in html for tok in signed_in_ui_markers):
                return True
            if any(tok in html for tok in ("sign in", "log in")):
                return False
            return False

        # Consumer flow keeps previous behavior (portal host is enough).
        return True
    except Exception:
        return False


async def _collect_cookies(
    context: BrowserContext,
    page: Page,
    targets: list[tuple[str, list[str]]],
) -> dict[str, str]:
    """
    Navigate to each target domain and collect all target cookies.
    Must visit each domain explicitly — browsers scope cookies by domain.
    Later URLs overwrite same cookie name so consumer Copilot values win for API/WSS.
    """
    collected: dict[str, str] = {}

    for url, names in targets:
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
                if name in names and value:
                    collected[name] = value
                    print(f"[cookie_extractor] Got: {name} ({len(value)} chars)")
        except Exception as e:
            print(f"[cookie_extractor] Warning: could not collect from {url}: {e}")

    return collected


async def _collect_shadow_cookies(
    context: BrowserContext,
    targets: list[tuple[str, list[str]]],
) -> dict[str, str]:
    """
    Collect cookies from additional domains using a temporary hidden page so
    the user's visible portal tab is not displaced.
    """
    collected: dict[str, str] = {}
    shadow = await context.new_page()
    try:
        for url, names in targets:
            nav_ok = False
            try:
                for attempt in range(2):
                    try:
                        await shadow.goto(url, wait_until="domcontentloaded", timeout=15_000)
                        nav_ok = True
                        break
                    except Exception:
                        if attempt == 0:
                            await asyncio.sleep(1)
                if nav_ok:
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"[cookie_extractor] Shadow warning for {url}: {e}")
            try:
                # Even when navigation is flaky, context may already have valid cookies.
                domain_cookies = await context.cookies([url])
                found = 0
                for cookie in domain_cookies:
                    name = cookie["name"]
                    value = cookie.get("value", "")
                    if name in names and value:
                        collected[name] = value
                        found += 1
                if found:
                    print(f"[cookie_extractor] Shadow collected {found} cookies from {url}")
                elif not nav_ok:
                    print(f"[cookie_extractor] Shadow skipped noisy failure for {url} (no cookies available)")
            except Exception as e:
                print(f"[cookie_extractor] Shadow warning for {url}: {e}")
    finally:
        try:
            await shadow.close()
        except Exception:
            pass
    return collected


def _build_cookie_string(cookies: dict[str, str]) -> str:
    return ";".join(f"{k}={v}" for k, v in cookies.items())


def _parse_cookie_string(cookie_str: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if k and v:
            out[k] = v
    return out


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


def patch_env_variable(env_path: str, key: str, value: str) -> None:
    """Public wrapper for server /setup and tests."""
    _patch_env(env_path, key, value)


async def extract_and_save(env_path: str = "/app/.env") -> dict:
    """
    Main extraction flow.
    1. Launch browser (or reuse existing).
    2. Navigate to portal (consumer Copilot or M365 hub per .env).
    3. If not logged in, wait up to 60s for user to authenticate via noVNC.
    4. Collect cookies from portal + bing.
    5. Write to .env.
    Returns a result dict with status and cookie info.
    """
    profile, portal_base_override, _api_override = portal_settings_from_env_file(env_path)
    landing = portal_landing_url(profile, portal_base_override)
    netloc = (urlparse(landing).netloc or "").lower()
    if netloc:
        host_markers = (netloc,)
    else:
        host_markers = ("copilot.microsoft.com", "m365.cloud.microsoft")
    target_cookies = target_cookies_for_profile(profile)

    async with _lock:
        context = await _get_context()
        page = await _get_or_create_page(context)

        # Step 1: Navigate to portal (skip if already there)
        current_url = page.url
        landing_norm = landing.rstrip("/")
        if not current_url.startswith(landing_norm):
            print(f"[cookie_extractor] Navigating to {landing} (profile={profile})...")
            try:
                await page.goto(landing, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(2)
            except Exception as e:
                return {"status": "error", "message": f"Failed to navigate to portal: {e}"}
        else:
            print(f"[cookie_extractor] Already on {current_url} — skipping navigation")

        # Step 2: Check login (up to 60 seconds for user to log in via noVNC)
        logged_in = await _is_logged_in(page, host_markers, profile)
        if not logged_in:
            print("[cookie_extractor] Not logged in. Waiting up to 60s for authentication via noVNC...")
            print(f"[cookie_extractor] Open http://localhost:6080 and complete sign-in for {landing}")
            deadline = time.time() + 60
            while time.time() < deadline:
                await asyncio.sleep(3)
                if await _is_logged_in(page, host_markers, profile):
                    print("[cookie_extractor] Login detected!")
                    logged_in = True
                    break
            if not logged_in:
                print("[cookie_extractor] WARNING: Portal session not confirmed. Extracting available cookies anyway.")

        # Wait for cookies to stabilize after login
        await asyncio.sleep(2)

        # Step 3: Collect cookies from all domains
        cookies = await _collect_cookies(context, page, target_cookies)
        # Phase B bridge: for m365_hub, also fetch copilot/bing cookies in a shadow tab
        # so C1 can fallback to copilot provider without disrupting the user's visible page.
        if profile == "m365_hub":
            shadow_targets = [
                ("https://copilot.microsoft.com", _COPILOT_PORTAL_COOKIE_NAMES),
                ("https://www.bing.com", _BING_COOKIE_NAMES),
            ]
            shadow_cookies = await _collect_shadow_cookies(context, shadow_targets)
            cookies.update(shadow_cookies)

        # Step 4: Navigate back to portal for a clean state
        try:
            await page.goto(landing, wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass

        # Step 5: Validate (profile-aware: m365 uses different required cookies)
        req = required_cookies_for_profile(profile)
        # For m365_hub, pass if ANY required cookie is present (OR logic)
        if profile == "m365_hub":
            missing = req if not any(r in cookies for r in req) else []
        else:
            missing = [r for r in req if r not in cookies]
        if missing:
            return {
                "status": "error",
                "message": f"Missing required cookies: {missing}. Is the browser logged in?",
                "cookies_found": list(cookies.keys()),
            }

        # Preserve previously extracted cross-domain cookies if a shadow fetch
        # transiently fails (timeouts/QUIC errors).
        prev_env = _read_env_keys(env_path, ("COPILOT_COOKIES", "BING_COOKIES"))
        prev = _parse_cookie_string(prev_env.get("COPILOT_COOKIES", "") or prev_env.get("BING_COOKIES", ""))
        merged = dict(prev)
        merged.update(cookies)
        cookie_str = _build_cookie_string(merged)
        if profile == "m365_hub":
            has_auth = "OH.SID" in cookies
        else:
            has_auth = any(c in cookies for c in AUTH_COOKIES)
        mode = "authenticated" if has_auth else "anonymous"

        # Step 6: Write to .env (works for both authenticated and anonymous sessions)
        _patch_env(env_path, "COPILOT_COOKIES", cookie_str)
        _patch_env(env_path, "BING_COOKIES", cookie_str)

        # Profile-aware status message
        if has_auth:
            if profile == "m365_hub":
                msg = f"Extracted {len(cookies)} cookies in authenticated mode (OH.SID present)."
            else:
                msg = f"Extracted {len(cookies)} cookies in authenticated mode (_U cookie present)."
        else:
            if profile == "m365_hub":
                msg = (
                    f"Extracted {len(cookies)} cookies but no OH.SID cookie. "
                    f"M365 session is not authenticated yet. "
                    f"To fix: (1) Open http://localhost:6080, (2) Complete M365 sign-in, "
                    f"(3) Re-run: curl -X POST http://localhost:8001/extract"
                )
            else:
                msg = (
                    f"Extracted {len(cookies)} cookies but NO _U COOKIE (Microsoft account auth). "
                    f"Copilot API calls will fail with 403. "
                    f"To fix: (1) Open http://localhost:6080, (2) Sign in with your Microsoft account, "
                    f"(3) Re-run: curl -X POST http://localhost:8001/extract"
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


async def warm_browser_for_novnc() -> None:
    """
    Launch Chromium and open the local /setup page so noVNC shows a framebuffer
    instead of a black Xvfb before the first /extract or /navigate.
    """
    setup_url = os.getenv("BROWSER_AUTH_SETUP_URL", "http://127.0.0.1:8001/setup")
    async with _lock:
        context = await _get_context()
        page = await _get_or_create_page(context)
        await page.goto(setup_url, wait_until="domcontentloaded", timeout=60_000)
    print(f"[cookie_extractor] noVNC warm: opened {setup_url}")


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
