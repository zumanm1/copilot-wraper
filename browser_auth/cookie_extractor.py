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
_context_init_lock = asyncio.Lock()
_chat_lock = asyncio.Lock()  # kept as fallback; pool mode bypasses this
_pool_pages: set = set()      # pages owned by PagePool; skipped by _get_or_create_page
_page_pool: "PagePool | None" = None  # initialized lazily on first browser_chat call
_chat_semaphore: asyncio.Semaphore | None = None  # limits concurrent Playwright operations
_DISMISS_AUTH_DIALOG = os.getenv("BROWSER_AUTH_AUTO_DISMISS_AUTH_DIALOG", "false").strip().lower() == "true"


async def _get_context() -> BrowserContext:
    """Return (or create) the persistent browser context."""
    global _playwright, _browser, _context

    if _context is not None:
        return _context

    async with _context_init_lock:
        if _context is not None:
            return _context

        profile_dir = Path(os.getenv("BROWSER_PROFILE_DIR", "/browser-profile"))
        profile_dir.mkdir(parents=True, exist_ok=True)

        for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = profile_dir / lock
            if os.path.lexists(lock_path):
                try:
                    os.remove(lock_path)
                    print(f"[cookie_extractor] Removed stale lock: {lock_path}")
                except OSError:
                    pass

        _playwright = await async_playwright().start()
        _context = await _playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
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
                "--disable-popup-blocking",
            ],
            ignore_default_args=["--enable-automation", "--disable-infobars"],
            viewport=None,
            ignore_https_errors=False,
            accept_downloads=False,
        )

        def _on_new_page(popup: Page) -> None:
            asyncio.ensure_future(_handle_auth_popup(popup))

        _context.on("page", _on_new_page)

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
    """Reuse an existing non-pool page or open a new one."""
    pages = context.pages
    for p in pages:
        if not p.is_closed() and p not in _pool_pages:
            return p
    return await context.new_page()


class PagePool:
    """
    Agent-keyed pool of browser tabs for M365 Chat.

    Pre-creates N tabs at startup (with concurrency limit of 2 to avoid
    overwhelming Chromium).  Tabs are then lazily **assigned** to agents
    on first request — each AI agent (c2-aider, c5-claude-code, etc.) gets
    a dedicated sticky tab.  Per-agent asyncio.Lock serialises concurrent
    requests within one agent while different agents run in full parallel.
    """

    _M365_CHAT_URL = "https://m365.cloud.microsoft/chat"
    _COMPOSER_SEL = (
        '[data-testid="composer-input"], '
        '[role="textbox"][contenteditable="true"], '
        'textarea'
    )
    _CREATE_CONCURRENCY = 2

    def __init__(self, size: int) -> None:
        self._size = size
        self._agent_tabs: dict[str, Page] = {}
        self._agent_locks: dict[str, asyncio.Lock] = {}
        self._free_tabs: asyncio.Queue[Page] = asyncio.Queue()
        self._meta_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._context: BrowserContext | None = None

    async def initialize(self, context: BrowserContext) -> None:
        """Pre-create N tabs (concurrency-limited to avoid browser overload)."""
        async with self._init_lock:
            if self._initialized:
                return
            self._context = context
            _t0 = time.monotonic()
            print(f"[PagePool] Pre-creating {self._size} tabs (max {self._CREATE_CONCURRENCY} concurrent)...")
            sem = asyncio.Semaphore(self._CREATE_CONCURRENCY)

            async def _init_one(idx: int) -> "Page | None":
                async with sem:
                    try:
                        page = await context.new_page()
                        _pool_pages.add(page)
                        await page.goto(
                            self._M365_CHAT_URL,
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        try:
                            await page.wait_for_selector(
                                self._COMPOSER_SEL, state="visible", timeout=25_000,
                            )
                        except Exception:
                            pass
                        print(f"[PagePool] Tab {idx + 1}/{self._size} ready: {page.url[:60]}")
                        return page
                    except Exception as exc:
                        print(f"[PagePool] Tab {idx + 1} init error (skipped): {exc}")
                        return None

            results = await asyncio.gather(*[_init_one(i) for i in range(self._size)])
            for page in results:
                if page is not None:
                    await self._free_tabs.put(page)
            _ms = int((time.monotonic() - _t0) * 1000)
            print(f"[PagePool] {self._free_tabs.qsize()}/{self._size} tabs ready in {_ms}ms")
            self._initialized = True

    async def _create_tab(self, label: str) -> Page:
        """Create one new tab (for replacement after failures)."""
        assert self._context is not None
        page = await self._context.new_page()
        _pool_pages.add(page)
        await page.goto(
            self._M365_CHAT_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        try:
            await page.wait_for_selector(
                self._COMPOSER_SEL, state="visible", timeout=25_000,
            )
        except Exception:
            pass
        print(f"[PagePool] Replacement tab for '{label}' ready: {page.url[:60]}")
        return page

    async def acquire(self, agent_id: str = "", timeout: float = 120.0) -> Page:
        """Acquire the dedicated tab for *agent_id*.

        First call for an agent assigns a pre-created tab from the free pool.
        Subsequent calls reuse the same sticky tab.  Blocks on the per-agent
        lock so concurrent requests from the same agent are serialised.
        """
        if not agent_id:
            agent_id = "__default__"

        async with self._meta_lock:
            if agent_id not in self._agent_locks:
                self._agent_locks[agent_id] = asyncio.Lock()

        lock = self._agent_locks[agent_id]
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"PagePool: tab for agent '{agent_id}' busy after {timeout:.0f}s"
            )

        if agent_id not in self._agent_tabs:
            try:
                page = await asyncio.wait_for(self._free_tabs.get(), timeout=30)
                self._agent_tabs[agent_id] = page
                print(f"[PagePool] Assigned tab to agent '{agent_id}'")
            except (asyncio.TimeoutError, Exception) as exc:
                lock.release()
                raise TimeoutError(
                    f"PagePool: no free tab for agent '{agent_id}': {exc}"
                )

        return self._agent_tabs[agent_id]

    def release(self, agent_id: str = "") -> None:
        """Release the per-agent lock so the tab can accept new work."""
        if not agent_id:
            agent_id = "__default__"
        lock = self._agent_locks.get(agent_id)
        if lock and lock.locked():
            try:
                lock.release()
            except RuntimeError:
                pass

    async def replace(self, agent_id: str, bad_page: Page, context: BrowserContext) -> Page:
        """Close a failed tab, create a healthy replacement for *agent_id*."""
        if not agent_id:
            agent_id = "__default__"
        _pool_pages.discard(bad_page)
        try:
            await bad_page.close()
        except Exception:
            pass
        new_page = await self._create_tab(agent_id)
        self._agent_tabs[agent_id] = new_page
        return new_page

    def update_tab(self, agent_id: str, page: Page) -> None:
        """Update the tab reference for an agent (e.g. after page replacement)."""
        if not agent_id:
            agent_id = "__default__"
        _pool_pages.discard(self._agent_tabs.get(agent_id))
        _pool_pages.add(page)
        self._agent_tabs[agent_id] = page

    @property
    def available(self) -> int:
        return self._free_tabs.qsize()

    @property
    def size(self) -> int:
        return self._size

    @property
    def agents(self) -> list[str]:
        """Agent IDs that currently have a dedicated tab."""
        return list(self._agent_tabs.keys())


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


async def extract_access_token() -> dict:
    """Extract access_token from the browser's localStorage on M365 Copilot.

    Checks m365.cloud.microsoft localStorage first, then falls back to
    copilot.microsoft.com cookies (__Host-copilot-anon).

    Returns dict with 'access_token' and optionally 'useridentitytype'.
    """
    context = await _get_context()
    page = await _get_or_create_page(context)

    # Navigate to M365 chat if not already there
    current = page.url or ""
    if "m365.cloud.microsoft" not in current:
        try:
            await page.goto("https://m365.cloud.microsoft/chat", wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[cookie_extractor] navigate for token extraction failed: {e}")

    result = await page.evaluate("""
        (() => {
            for (var i = 0; i < localStorage.length; i++) {
                try {
                    const key = localStorage.key(i);
                    const item = JSON.parse(localStorage.getItem(key));
                    if (item?.body?.access_token) {
                        return {
                            access_token: "" + item.body.access_token,
                            useridentitytype: "m365"
                        };
                    } else if (key.includes("chatai")) {
                        return {
                            access_token: "" + item.secret,
                            useridentitytype: null
                        };
                    }
                } catch(e) {}
            }
            return null;
        })()
    """)

    if result and result.get("access_token"):
        print(f"[cookie_extractor] access_token extracted (length={len(result['access_token'])})")
        return result

    # Fallback: try copilot.microsoft.com anon cookie from browser context
    for domain_url in ["https://m365.cloud.microsoft", "https://copilot.microsoft.com"]:
        cookies = await context.cookies(domain_url)
        for c in cookies:
            if c["name"] == "__Host-copilot-anon":
                print(f"[cookie_extractor] Using __Host-copilot-anon from {domain_url}")
                return {"access_token": c["value"], "useridentitytype": None}

    print("[cookie_extractor] No access_token found in localStorage or cookies")
    return {"access_token": None, "useridentitytype": None}


async def browser_chat(prompt: str, mode: str = "chat", timeout_ms: int = 60000, agent_id: str = "") -> dict:
    """Execute a Copilot chat via the M365 browser UI + WebSocket frame interception.

    Each AI agent gets a **dedicated** browser tab (agent-to-tab affinity).
    Tabs are pre-created at startup and assigned lazily on first request.
    A global semaphore limits concurrent Playwright operations to 3 to prevent
    browser resource exhaustion.  Different agents run in parallel; concurrent
    requests from the same agent are serialised by a per-agent lock.

    Returns dict with 'text', 'events', 'success', and optionally 'error'.
    """
    global _page_pool, _chat_semaphore

    context = await _get_context()

    pool_size = max(1, int(os.getenv("C3_CHAT_TAB_POOL_SIZE", "6")))
    async with _chat_lock:
        if _page_pool is None:
            _page_pool = PagePool(pool_size)
        if _chat_semaphore is None:
            max_concurrent = max(1, int(os.getenv("C3_CHAT_MAX_CONCURRENT", "3")))
            _chat_semaphore = asyncio.Semaphore(max_concurrent)
    await _page_pool.initialize(context)

    _aid = agent_id or "__default__"
    lock_timeout = (timeout_ms / 1000.0) + 30

    async with _chat_semaphore:
        try:
            page = await _page_pool.acquire(agent_id=_aid, timeout=lock_timeout)
        except TimeoutError as exc:
            return {"success": False, "error": str(exc), "events": [], "text": ""}

        final_page = page
        try:
            result, final_page = await _browser_chat_on_page(
                page, context, prompt, mode=mode, timeout_ms=timeout_ms
            )
            result["agent_tab"] = _aid
        except Exception as exc:
            print(f"[browser_chat] [{_aid}] Unexpected error: {exc}")
            result = {"success": False, "error": str(exc), "events": [], "text": ""}
            try:
                final_page = await _page_pool.replace(_aid, page, context)
            except Exception:
                final_page = page
        finally:
            if final_page is not page:
                _page_pool.update_tab(_aid, final_page)
            _page_pool.release(agent_id=_aid)

    return result


async def _browser_chat_on_page(
    page: Page,
    context: BrowserContext,
    prompt: str,
    mode: str = "chat",
    timeout_ms: int = 60000,
) -> "tuple[dict, Page]":
    """
    Execute one chat request on a given browser page.

    Returns (result_dict, final_page).  final_page may differ from `page`
    when the original page was unresponsive and replaced with a fresh one.

    Phase 3: tries 'New chat' button click first (no navigation overhead);
    falls back to full about:blank → m365 teardown only when needed.
    """
    import json as _json

    # ── Timing instrumentation ──────────────────────────────────────────
    _t_start = time.monotonic()
    _timings: dict = {"prompt_len": len(prompt)}

    # Health check: verify the page is responsive before doing anything.
    # A stale/crashed page will hang on goto; detect and replace it early.
    try:
        await asyncio.wait_for(page.evaluate("1+1"), timeout=5)
    except Exception:
        print("[browser_chat] Page unresponsive — replacing with fresh page")
        try:
            await page.close()
        except Exception:
            pass
        page = await context.new_page()
        _pool_pages.add(page)
    _timings["health_check_ms"] = int((time.monotonic() - _t_start) * 1000)

    # State for WebSocket frame interception
    collected_text = []
    events_recv = []
    events_sent = []
    ws_urls = []
    done_event = asyncio.Event()

    def _parse_frame(payload: str) -> list[dict]:
        """Parse one or more JSON messages from a WebSocket frame.
        SignalR frames are delimited by \\x1e (record separator)."""
        results = []
        for part in payload.split("\x1e"):
            part = part.strip()
            if not part:
                continue
            try:
                results.append(_json.loads(part))
            except Exception:
                pass
        return results

    def _on_ws_recv(payload: str) -> None:
        """Handle received WebSocket text frames (SignalR or Copilot protocol)."""
        for msg in _parse_frame(payload):
            # SignalR protocol (M365 Copilot via substrate.office.com)
            sr_type = msg.get("type")
            if sr_type is not None:
                if sr_type == 1:  # Invocation
                    target = msg.get("target", "")
                    events_recv.append(f"sr:{target}")
                    args = msg.get("arguments", [])
                    _texts_this_event: list = []
                    for arg in args:
                        if isinstance(arg, dict):
                            text = arg.get("text") or arg.get("messageText") or ""
                            if text:
                                _texts_this_event.append(text)
                            for m in arg.get("messages", []):
                                if isinstance(m, dict):
                                    t = m.get("text") or m.get("content") or ""
                                    if t:
                                        _texts_this_event.append(t)
                    if _texts_this_event:
                        collected_text.clear()
                        collected_text.extend(_texts_this_event)
                    print(f"[browser_chat] WS_RECV SR_INV: target={target} args_keys={[list(a.keys()) if isinstance(a, dict) else type(a).__name__ for a in args][:3]} text_so_far={len(''.join(collected_text))}")
                elif sr_type == 2:  # Completion with full response
                    events_recv.append("sr:completion")
                    # Extract bot response text from type=2 item.messages
                    item = msg.get("item", {})
                    for m in item.get("messages", []):
                        if not isinstance(m, dict):
                            continue
                        author = m.get("author", "")
                        if author == "user":
                            continue  # Skip user's own message echo
                        # Bot response text — try multiple field locations
                        t = m.get("text") or m.get("messageText") or ""
                        # Also check adaptiveCards body
                        if not t:
                            for card in m.get("adaptiveCards", []):
                                for body in card.get("body", []):
                                    t = body.get("text", "")
                                    if t:
                                        break
                                if t:
                                    break
                        if t and t not in "".join(collected_text):
                            collected_text.clear()  # type=2 has final text — replace partials
                            collected_text.append(t)
                    print(f"[browser_chat] WS_RECV SR_DONE: {str(payload)[:200]}")
                    done_event.set()
                elif sr_type == 3:  # Close/completion signal
                    events_recv.append("sr:close3")
                    print(f"[browser_chat] WS_RECV SR_TYPE3: {str(payload)[:200]}")
                    done_event.set()
                elif sr_type == 7:  # Close
                    events_recv.append("sr:close")
                    done_event.set()
                elif sr_type == 6:  # Ping
                    pass  # ignore pings
                else:
                    events_recv.append(f"sr:type{sr_type}")
                    print(f"[browser_chat] WS_RECV SR_OTHER: type={sr_type} {str(payload)[:150]}")
                continue

            # Copilot.microsoft.com protocol (legacy/consumer)
            ev = msg.get("event", "")
            if ev:
                events_recv.append(ev)
                print(f"[browser_chat] WS_RECV: {ev} {str(payload)[:150]}")
                if ev == "appendText":
                    collected_text.append(msg.get("text", ""))
                elif ev in ("done", "partCompleted"):
                    done_event.set()
                elif ev == "error":
                    events_recv.append(f"error:{msg.get('errorCode', 'unknown')}")
                    done_event.set()

    def _on_ws_sent(payload: str) -> None:
        """Log sent WebSocket frames."""
        for msg in _parse_frame(payload):
            sr_type = msg.get("type")
            ev = msg.get("event", "")
            label = f"sr:type{sr_type}" if sr_type is not None else (ev or "raw")
            events_sent.append(label)
            print(f"[browser_chat] WS_SENT: {label} {str(payload)[:200]}")

    def _on_websocket(ws) -> None:
        """Attach frame listeners to any new WebSocket on the page."""
        url = ws.url or ""
        ws_urls.append(url)
        print(f"[browser_chat] WS_OPEN: {url[:120]}")
        ws.on("framereceived", _on_ws_recv)
        ws.on("framesent", _on_ws_sent)
        ws.on("close", lambda: print("[browser_chat] WS_CLOSED"))

    # ── Attach WS listener — M365 opens a NEW WS per chat message ──
    page.on("websocket", _on_websocket)

    # ── Phase 3: fast reset ─────────────────────────────────────────────────────
    # Priority order:
    #   1. Composer already visible (fresh tab or idle) → use immediately
    #   2. Click "New chat" sidebar button → wait for composer
    #   3. Full page teardown (about:blank → m365 chat) as last resort
    _t_nav_start = time.monotonic()
    _M365_CHAT_URL = "https://m365.cloud.microsoft/chat"
    _fast_reset_ok = False
    _combined = (
        '[data-testid="composer-input"], '
        '[role="textbox"][contenteditable="true"], '
        'textarea'
    )
    if "m365.cloud.microsoft" in (page.url or ""):
        try:
            await page.wait_for_selector(_combined, state="visible", timeout=3_000)
            _fast_reset_ok = True
            print(f"[browser_chat] Composer ready (no reset needed) — {page.url[:60]}")
        except Exception:
            try:
                _clicked = await page.evaluate("""() => {
                    const nc = document.querySelector(
                        '[data-testid="sidebar-new-conversation-nav-item"]'
                    );
                    if (nc) { nc.click(); return true; }
                    for (const b of document.querySelectorAll('button,[role="button"]')) {
                        const t = ((b.textContent || '') +
                                   (b.getAttribute('aria-label') || '')).toLowerCase();
                        if (t.includes('new chat') || t.includes('new conversation')) {
                            b.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if _clicked:
                    try:
                        await page.wait_for_selector(_combined, state="visible", timeout=10_000)
                        _fast_reset_ok = True
                        print(f"[browser_chat] Fast reset via 'New chat' — {page.url[:60]}")
                    except Exception:
                        pass
            except Exception:
                pass

    if not _fast_reset_ok:
        print("[browser_chat] Fast reset unavailable — full page teardown")
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15_000)
            await page.goto(_M365_CHAT_URL, wait_until="domcontentloaded", timeout=30_000)
            _combined_teardown = (
                '[role="textbox"][contenteditable="true"], '
                '[contenteditable="true"], '
                'textarea'
            )
            try:
                await page.wait_for_selector(_combined_teardown, state="visible", timeout=25_000)
            except Exception:
                pass
            print(f"[browser_chat] Full teardown complete: {page.url}")
        except Exception as e:
            print(f"[browser_chat] navigate failed: {e}")
            try:
                page.remove_listener("websocket", _on_websocket)
            except Exception:
                pass
            return {"success": False, "error": f"Navigate failed: {e}", "events": [], "text": ""}, page

    _timings["nav_ms"] = int((time.monotonic() - _t_nav_start) * 1000)
    _timings["nav_method"] = "fast_reset" if _fast_reset_ok else "full_teardown"

    try:
        # Use page.evaluate() for auth dialog check — immune to overlay dialogs
        await asyncio.sleep(0.3)  # Brief settle after nav

        # Check for "Authentication required" dialog (M365 session expired)
        auth_blocked = await page.evaluate("""() => {
            const h = document.querySelector('h2');
            if (h && h.textContent.includes('Authentication required')) {
                // Try to click Sign in / Refresh button
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    const t = b.textContent.trim().toLowerCase();
                    if (t === 'sign in' || t === 'refresh' || t === 'ok') {
                        b.click();
                        return 'clicked:' + t;
                    }
                }
                return 'auth_dialog_present';
            }
            return null;
        }""")
        if auth_blocked:
            print(f"[browser_chat] Auth check: {auth_blocked}")
            if auth_blocked == "auth_dialog_present":
                return {
                    "success": False,
                    "error": "Authentication required on m365.cloud.microsoft — sign in via noVNC at http://localhost:6080 then retry",
                    "events": [], "text": "",
                }, page
            # Clicked a button — wait for re-auth
            await asyncio.sleep(8)
            # Re-check
            still_blocked = await page.evaluate("""() => {
                const h = document.querySelector('h2');
                return h && h.textContent.includes('Authentication required');
            }""")
            if still_blocked:
                return {
                    "success": False,
                    "error": "Authentication required on m365.cloud.microsoft — sign in via noVNC at http://localhost:6080 then retry",
                    "events": [], "text": "",
                }, page
            print("[browser_chat] Auth dialog dismissed after button click")

        # ── Discover DOM elements via page.evaluate (fast, overlay-immune) ──
        dom_info = await page.evaluate("""() => {
            const info = {url: location.href, title: document.title, composer: null, sendBtn: null};

            // Find composer
            const sels = [
                '[data-testid="composer-input"]',
                'textarea[placeholder*="Message"]',
                'textarea[placeholder*="Copilot"]',
                'textarea[placeholder*="Ask"]',
                'textarea',
                '[role="textbox"][contenteditable="true"]',
                '[contenteditable="true"]',
            ];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el && el.offsetParent !== null) {
                    info.composer = s;
                    break;
                }
            }

            // Find send button
            const btnSels = [
                'button[data-testid="composer-send-button"]',
                'button[data-testid="composer-create-button"]',
                'button[aria-label*="Send"]',
                'button[aria-label*="Submit"]',
            ];
            for (const s of btnSels) {
                const el = document.querySelector(s);
                if (el) { info.sendBtn = s; break; }
            }

            return info;
        }""")
        print(f"[browser_chat] DOM probe: url={dom_info.get('url','?')[:60]} composer={dom_info.get('composer')} sendBtn={dom_info.get('sendBtn')}")

        composer_sel = dom_info.get("composer")
        if not composer_sel:
            err = f"No composer found (page: {dom_info.get('url')}, title: {dom_info.get('title')})"
            print(f"[browser_chat] {err}")
            return {"success": False, "error": err, "events": [], "text": ""}, page

        # ── Text input via execCommand (fast + overlay-resilient) ────────
        _t_type_start = time.monotonic()
        composer = page.locator(composer_sel).first
        await composer.click(force=True, timeout=10_000)
        await asyncio.sleep(0.15)

        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")

        # Always use execCommand('insertText') — instant, fires React-compatible
        # beforeinput/input events, and doesn't depend on Playwright actionability
        # checks that can timeout on stale M365 pages.
        await page.evaluate("(text) => document.execCommand('insertText', false, text)", prompt)
        await asyncio.sleep(0.2)
        print(f"[browser_chat] Inserted prompt ({len(prompt)} chars)")

        # ── Submit via Enter key (most reliable for React apps) ──
        _timings["type_ms"] = int((time.monotonic() - _t_type_start) * 1000)
        _t_submit = time.monotonic()
        await page.keyboard.press("Enter")
        print("[browser_chat] Pressed Enter to submit")

        # Wait for the done event or timeout
        timeout_s = timeout_ms / 1000.0
        try:
            await asyncio.wait_for(done_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            print(f"[browser_chat] WS timeout after {timeout_s}s — trying DOM fallback")

        text = "".join(collected_text)

        # ── DOM fallback: extract response text from the page if WS gave nothing ──
        if not text:
            await asyncio.sleep(2)  # Give the UI a moment to finish rendering
            dom_text = await page.evaluate("""() => {
                // M365 Copilot renders responses in message containers
                // Look for the LAST assistant/bot message on the page
                const allMsgs = document.querySelectorAll(
                    '[data-content="ai-message"], [data-is-bot-message="true"], ' +
                    '.ac-textBlock, [class*="assistantMessage"], [class*="botMessage"], ' +
                    '[data-testid*="message"][data-testid*="bot"], ' +
                    '[role="article"]'
                );
                if (allMsgs.length > 0) {
                    const last = allMsgs[allMsgs.length - 1];
                    return last.innerText || last.textContent || '';
                }
                // Broader fallback: look for the last turn-container with paragraphs
                const turns = document.querySelectorAll('[class*="turn"]');
                if (turns.length > 1) {
                    const last = turns[turns.length - 1];
                    const ps = last.querySelectorAll('p, span, div');
                    const parts = [];
                    ps.forEach(p => { if (p.innerText.trim()) parts.push(p.innerText.trim()); });
                    return parts.join('\\n');
                }
                return '';
            }""")
            if dom_text and dom_text.strip():
                text = dom_text.strip()
                print(f"[browser_chat] DOM fallback extracted {len(text)} chars")

        success = len(text) > 0
        _timings["ws_wait_ms"] = int((time.monotonic() - _t_submit) * 1000)
        _timings["total_ms"] = int((time.monotonic() - _t_start) * 1000)
        _timings["text_len"] = len(text)
        _timings["success"] = success
        print(f"[browser_chat] PERF: {_timings}")
        print(f"[browser_chat] success={success}, recv={events_recv[:10]}, sent={events_sent[:10]}, ws_urls={[u[:60] for u in ws_urls]}, text_len={len(text)}")
        return {"success": success, "events": events_recv, "events_sent": events_sent, "ws_urls": ws_urls, "text": text, "perf": _timings}, page

    except Exception as e:
        print(f"[browser_chat] error: {e}")
        return {"success": False, "error": str(e), "events": events_recv, "text": "".join(collected_text)}, page
    finally:
        try:
            page.remove_listener("websocket", _on_websocket)
        except Exception:
            pass


async def close():
    """Gracefully shut down browser."""
    global _context, _browser, _playwright
    if _context:
        await _context.close()
        _context = None
    if _playwright:
        await _playwright.stop()
        _playwright = None


# Public alias so server.py can import without leading underscore
is_logged_in = _is_logged_in


async def check_session_health(env_path: str = "/app/.env") -> dict:
    """
    Lightweight M365 session health check — no navigation, no chat.

    Strategy (fast, page-URL-independent):
      1. Check browser-context cookies for the portal domain.
         m365_hub → OH.SID on m365.cloud.microsoft
         consumer → _U on copilot.microsoft.com / bing.com
      2. Fall back to _is_logged_in() if the warm page happens to be on the portal.

    Typically <100ms and safe to poll frequently.

    Returns:
        {"session": "active"|"expired"|"unknown", "profile": str, "reason": str|None}
    """
    import datetime
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        profile, portal_base_override, _ = portal_settings_from_env_file(env_path)
        landing = portal_landing_url(profile, portal_base_override)
        from urllib.parse import urlparse
        netloc = (urlparse(landing).netloc or "").lower()
        host_markers = (netloc,) if netloc else ("copilot.microsoft.com", "m365.cloud.microsoft")

        context = await _get_context()

        # --- Strategy 1: check context cookies directly (URL-independent) ---
        cookie_urls = [f"https://{netloc}"] if netloc else [
            "https://m365.cloud.microsoft",
            "https://copilot.microsoft.com",
        ]
        try:
            jar = await context.cookies(cookie_urls)
            cookie_names = {c.get("name", "") for c in jar if isinstance(c, dict)}
        except Exception:
            cookie_names = set()

        is_m365 = (profile or "").strip().lower() == "m365_hub"
        if is_m365 and "OH.SID" in cookie_names:
            return {"session": "active", "profile": profile, "reason": None, "checked_at": now}
        if not is_m365 and "_U" in cookie_names:
            return {"session": "active", "profile": profile, "reason": None, "checked_at": now}

        # --- Strategy 2: fall back to page-based check ---
        page = await _get_or_create_page(context)
        logged_in = await _is_logged_in(page, host_markers, profile)
        if logged_in:
            return {"session": "active", "profile": profile, "reason": None, "checked_at": now}

        return {
            "session": "expired",
            "profile": profile,
            "reason": "auth_required_or_not_on_portal",
            "checked_at": now,
        }
    except Exception as exc:
        return {"session": "unknown", "profile": "unknown", "reason": str(exc), "checked_at": now}
