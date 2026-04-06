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
import datetime
import json
import os
import re
import time
import uuid
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

_AUTH_STEP_DEFS = [
    ("c3_health", "C3 browser-auth healthy"),
    ("tab1_setup", "Tab 1 opened on /setup"),
    ("work_mode", "Work mode selected"),
    ("portal_connect", "Portal connect issued"),
    ("m365_nav", "Tab 1 navigated to M365"),
    ("session_auth", "M365 session authenticated"),
    ("popup_check_1", "Initial auth popup checked"),
    ("popup_continue_1", "Initial Continue click handled if needed"),
    ("stabilize_1", "Tab 1 stabilized after auth"),
    ("hello_prepare", "First prompt prepared"),
    ("hello_type", "First prompt typed"),
    ("hello_submit", "First prompt submitted"),
    ("hello_popup_watch", "Popup watch during first prompt"),
    ("hello_reply", "First reply received"),
    ("follow_prepare", "Follow-up prompt prepared"),
    ("follow_type", "Follow-up prompt typed"),
    ("follow_submit", "Follow-up prompt submitted"),
    ("follow_popup_watch", "Popup watch during follow-up"),
    ("follow_reply", "Follow-up reply received"),
    ("pool_ready", "Tab 1 ready for pool creation"),
    ("pool_target", "Pool target selected"),
    ("pool_expand_request", "Pool expansion request accepted"),
    ("pool_expand_progress", "Pool tabs preparing"),
    ("pool_expand_verify", "Pool inheritance verified"),
    ("pool_expand_done", "Pool expansion complete"),
]


def _new_auth_progress_state() -> dict:
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "run_id": None,
        "active": False,
        "source": None,
        "started_at": None,
        "updated_at": now,
        "finished_at": None,
        "result": None,
        "error": None,
        "current_step_id": None,
        "steps": [
            {
                "id": step_id,
                "label": label,
                "status": "pending",
                "detail": "",
                "started_at": None,
                "ended_at": None,
                "last_ms": None,
                "updated_at": now,
            }
            for step_id, label in _AUTH_STEP_DEFS
        ],
    }


_tab1_auth_progress = _new_auth_progress_state()
_tab1_auth_step_stats: dict[str, dict[str, float | int | None]] = {
    step_id: {"runs": 0, "total_ms": 0.0, "min_ms": None, "max_ms": None}
    for step_id, _ in _AUTH_STEP_DEFS
}


def _new_pool_monitor_state() -> dict:
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "phase": "idle",
        "source": None,
        "detail": "",
        "base_size": 0,
        "requested_target": 0,
        "target_size": 0,
        "pool_size": 0,
        "pool_available": 0,
        "pool_initialized": False,
        "agent_tabs": 0,
        "last_added": 0,
        "last_reloaded": 0,
        "updated_at": now,
    }


_tab1_pool_monitor = _new_pool_monitor_state()

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
_tab1_ready_lock = asyncio.Lock()
_tab1_session_ready = False
_tab1_session_meta: dict[str, object] = {"ready": False, "reason": "startup"}
_DISMISS_AUTH_DIALOG = os.getenv("BROWSER_AUTH_AUTO_DISMISS_AUTH_DIALOG", "false").strip().lower() == "true"
_M365_CHAT_URL = "https://m365.cloud.microsoft/chat"
_M365_COMPOSER_SEL = (
    '[data-testid="composer-input"], '
    '[role="textbox"][contenteditable="true"], '
    'textarea'
)
_M365_SERVICE_ERROR_PHRASES = (
    "service communication is currently unavailable",
    "something went wrong",
    "please try again later",
    "experiencing high demand",
    "try again later",
    "we're experiencing",
)


async def _page_text_hint(page: Page, limit: int = 4000) -> str:
    """Return a small lower-cased title/body snapshot without serializing full HTML."""
    try:
        text = await page.evaluate(
            """(limit) => {
                const title = document.title || "";
                const body = (document.body && (document.body.innerText || document.body.textContent || "")) || "";
                return (title + "\\n" + body).slice(0, limit);
            }""",
            limit,
        )
        return (text or "").lower()
    except Exception:
        return ""


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
                "--disable-blink-features=AutomationControlled",
                "--disable-quic",
                "--window-size=1280,900",
                "--window-position=0,0",
                "--start-maximized",
                "--hide-crash-restore-bubble",
                "--test-type",
                "--disable-popup-blocking",
            ],
            ignore_default_args=["--enable-automation", "--disable-infobars", "--disable-dev-shm-usage"],
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
                        if await _page_has_auth_dialog(page):
                            print("[cookie_extractor] Auth dialog detected — auto-clicking Continue")
                            note = await _click_auth_dialog_button(page)
                            if note:
                                print(f"[cookie_extractor] Auth dialog dismissed via {note}")
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


def invalidate_tab1_ready_state(reason: str = "") -> None:
    """Mark Tab 1 as requiring re-validation before pool tabs may be created."""
    global _tab1_session_ready, _tab1_session_meta
    _tab1_session_ready = False
    _tab1_session_meta = {
        "ready": False,
        "reason": reason or "unknown",
        "updated_at": int(time.time()),
    }


def _auth_progress_step(step_id: str) -> dict | None:
    for step in _tab1_auth_progress["steps"]:
        if step["id"] == step_id:
            return step
    return None


def _auth_progress_now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _pool_step_ids() -> tuple[str, ...]:
    return (
        "pool_target",
        "pool_expand_request",
        "pool_expand_progress",
        "pool_expand_verify",
        "pool_expand_done",
    )


def _step_stats_view(step_id: str) -> dict:
    raw = _tab1_auth_step_stats.get(step_id) or {}
    runs = int(raw.get("runs") or 0)
    total_ms = float(raw.get("total_ms") or 0.0)
    avg_ms = round(total_ms / runs, 1) if runs else None
    return {
        "runs": runs,
        "min_ms": raw.get("min_ms"),
        "avg_ms": avg_ms,
        "max_ms": raw.get("max_ms"),
    }


def _record_step_duration(step_id: str, duration_ms: int) -> None:
    stats = _tab1_auth_step_stats.setdefault(
        step_id,
        {"runs": 0, "total_ms": 0.0, "min_ms": None, "max_ms": None},
    )
    stats["runs"] = int(stats.get("runs") or 0) + 1
    stats["total_ms"] = float(stats.get("total_ms") or 0.0) + float(duration_ms)
    current_min = stats.get("min_ms")
    current_max = stats.get("max_ms")
    stats["min_ms"] = duration_ms if current_min is None else min(int(current_min), duration_ms)
    stats["max_ms"] = duration_ms if current_max is None else max(int(current_max), duration_ms)


def _sync_pool_monitor(pool: "PagePool | None" = None, **updates) -> dict:
    global _tab1_pool_monitor
    monitor = dict(_tab1_pool_monitor)
    monitor.update(updates)
    pool = pool or _page_pool
    if pool is not None:
        monitor["base_size"] = getattr(pool, "_base_size", monitor.get("base_size") or 0)
        monitor["pool_size"] = pool.size
        monitor["pool_available"] = pool.available
        monitor["pool_initialized"] = pool._initialized
        monitor["agent_tabs"] = len(pool.agents)
    monitor["updated_at"] = _auth_progress_now()
    _tab1_pool_monitor = monitor
    return monitor


def get_pool_monitor_snapshot() -> dict:
    return json.loads(json.dumps(_tab1_pool_monitor))


def _resume_tab1_auth_progress(source: str) -> None:
    if not _tab1_auth_progress.get("run_id"):
        reset_tab1_auth_progress(source)
        return
    now = _auth_progress_now()
    _tab1_auth_progress["active"] = True
    _tab1_auth_progress["source"] = source
    _tab1_auth_progress["result"] = None
    _tab1_auth_progress["error"] = None
    _tab1_auth_progress["finished_at"] = None
    _tab1_auth_progress["updated_at"] = now
    for step_id in _pool_step_ids():
        step = _auth_progress_step(step_id)
        if step is None:
            continue
        step["status"] = "pending"
        step["detail"] = ""
        step["started_at"] = None
        step["ended_at"] = None
        step["last_ms"] = None
        step["updated_at"] = now
        step.pop("_started_mono", None)


def reset_tab1_auth_progress(source: str = "validate-auth") -> dict:
    global _tab1_auth_progress
    state = _new_auth_progress_state()
    state["run_id"] = f"auth-{uuid.uuid4().hex[:12]}"
    state["active"] = True
    state["source"] = source
    state["started_at"] = _auth_progress_now()
    state["updated_at"] = state["started_at"]
    _tab1_auth_progress = state
    _sync_pool_monitor(
        None,
        phase="idle",
        source=source,
        detail="",
        requested_target=0,
        target_size=0,
        last_added=0,
        last_reloaded=0,
    )
    return get_tab1_auth_progress_snapshot()


def update_tab1_auth_progress(step_id: str, status: str, detail: str = "") -> None:
    step = _auth_progress_step(step_id)
    if step is None:
        return
    now = _auth_progress_now()
    previous_status = step.get("status")
    if status == "running" and previous_status != "running":
        step["started_at"] = now
        step["ended_at"] = None
        step["last_ms"] = None
        step["_started_mono"] = time.monotonic()
    elif status in ("done", "error") and previous_status == "running":
        started_mono = step.pop("_started_mono", None)
        if started_mono is not None:
            duration_ms = int((time.monotonic() - float(started_mono)) * 1000)
            step["last_ms"] = duration_ms
            step["ended_at"] = now
            _record_step_duration(step_id, duration_ms)
    step["status"] = status
    step["detail"] = detail or step.get("detail", "")
    step["updated_at"] = now
    _tab1_auth_progress["current_step_id"] = step_id
    _tab1_auth_progress["updated_at"] = now


def finish_tab1_auth_progress(result: str, error: str | None = None) -> None:
    now = _auth_progress_now()
    _tab1_auth_progress["active"] = False
    _tab1_auth_progress["result"] = result
    _tab1_auth_progress["error"] = error
    _tab1_auth_progress["finished_at"] = now
    _tab1_auth_progress["updated_at"] = now


def get_tab1_auth_progress_snapshot() -> dict:
    snap = json.loads(json.dumps(_tab1_auth_progress))
    for step in snap.get("steps", []):
        step.pop("_started_mono", None)
        step["stats"] = _step_stats_view(step.get("id", ""))
    snap["pool_monitor"] = get_pool_monitor_snapshot()
    return snap


def mark_tab1_auth_progress_done(step_id: str, detail: str = "") -> None:
    step = _auth_progress_step(step_id)
    if step is None:
        return
    if step.get("status") == "done" and not detail:
        return
    update_tab1_auth_progress(step_id, "done", detail or step.get("detail", ""))


def mark_tab1_auth_progress_error(step_id: str, detail: str = "") -> None:
    step = _auth_progress_step(step_id)
    if step is None:
        return
    update_tab1_auth_progress(step_id, "error", detail or step.get("detail", ""))


def _maybe_invalidate_tab1_for_auth(page: Page, reason: str) -> bool:
    """Only poison Tab 1 readiness when the auth problem happened on the real Tab 1 page."""
    if page in _pool_pages:
        return False
    invalidate_tab1_ready_state(reason)
    return True


def _mark_tab1_ready(result: dict | None = None) -> None:
    global _tab1_session_ready, _tab1_session_meta
    _tab1_session_ready = True
    meta = {"ready": True, "updated_at": int(time.time())}
    if result:
        for key in ("tab1_url", "reply", "checked_at", "follow_up_validated"):
            if key in result:
                meta[key] = result[key]
    _tab1_session_meta = meta


async def _page_has_auth_dialog(page: Page) -> bool:
    """Detect the in-app M365 auth gate shown as 'Authentication required'."""
    try:
        blocked = await page.evaluate("""() => {
            const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')];
            return headings.some(h => ((h.textContent || '').trim().toLowerCase()).includes('authentication required'));
        }""")
        if blocked:
            return True
    except Exception:
        pass
    hint = await _page_text_hint(page, limit=2000)
    return "authentication required" in hint and "continue" in hint


async def _click_auth_dialog_button(page: Page) -> str | None:
    """Hit the auth-gate button with a real Playwright mouse click first."""
    labels = ("Continue", "Sign in", "Refresh", "OK")
    last_error: Exception | None = None

    for label in labels:
        candidates = (
            page.locator("button", has_text=label),
            page.locator("[role='button']", has_text=label),
        )
        for locator in candidates:
            try:
                if await locator.count() == 0:
                    continue
                button = locator.first
                try:
                    await button.scroll_into_view_if_needed(timeout=2_000)
                except Exception:
                    pass
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
                try:
                    box = await button.bounding_box()
                except Exception:
                    box = None
                if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                    x = box["x"] + (box["width"] / 2.0)
                    y = box["y"] + (box["height"] / 2.0)
                    await page.mouse.move(x, y, steps=8)
                    await asyncio.sleep(0.1)
                    await page.mouse.down()
                    await asyncio.sleep(0.05)
                    await page.mouse.up()
                    return f"clicked:{label.lower()}(mouse)"
                await button.click(timeout=5_000, force=True)
                return f"clicked:{label.lower()}(locator)"
            except Exception as exc:
                last_error = exc
    if last_error is not None:
        print(f"[browser_chat] Auth dialog real-click failed: {last_error}")
    return None


async def _clear_auth_dialog_if_present(page: Page, settle_seconds: float = 8.0) -> tuple[bool, str | None]:
    """Dismiss the M365 auth gate if present and report whether the page is clear."""
    try:
        auth_blocked = await page.evaluate("""() => {
            const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')];
            const authHeading = headings.find(h =>
                ((h.textContent || '').trim().toLowerCase()).includes('authentication required')
            );
            return authHeading ? 'auth_dialog_present' : null;
        }""")
    except Exception:
        auth_blocked = None

    if not auth_blocked:
        return True, None

    real_click_note = await _click_auth_dialog_button(page)
    if real_click_note:
        auth_blocked = real_click_note
    else:
        try:
            fallback_note = await page.evaluate("""() => {
                const btns = [...document.querySelectorAll('button,[role="button"]')];
                for (const b of btns) {
                    const t = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).trim().toLowerCase();
                    if (t === 'sign in' || t === 'refresh' || t === 'ok' || t === 'continue' || t.startsWith('continue')) {
                        b.dispatchEvent(new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window
                        }));
                        return 'clicked:' + t + '(dom)';
                    }
                }
                return 'auth_dialog_present';
            }""")
            if fallback_note:
                auth_blocked = str(fallback_note)
        except Exception:
            pass

    print(f"[browser_chat] Auth check: {auth_blocked}")
    await asyncio.sleep(settle_seconds)
    if await _page_has_auth_dialog(page):
        retry_note = await _click_auth_dialog_button(page)
        if retry_note:
            auth_blocked = retry_note
            print(f"[browser_chat] Auth check retry: {auth_blocked}")
            await asyncio.sleep(max(2.0, settle_seconds / 2.0))
    if await _page_has_auth_dialog(page):
        return False, "Authentication required on m365.cloud.microsoft — sign in via noVNC at http://localhost:6080 then retry"
    print("[browser_chat] Auth dialog dismissed — proceeding")
    return True, str(auth_blocked)


async def _wait_for_done_with_auth_watch(
    page: Page,
    done_event: asyncio.Event,
    timeout_s: float,
    popup_step_id: str | None = None,
) -> bool:
    """Wait for completion while re-clearing any auth dialog that reappears."""
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        slice_s = min(3.0, remaining)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=slice_s)
            return True
        except asyncio.TimeoutError:
            pass
        try:
            if await _page_has_auth_dialog(page):
                auth_ok, auth_note = await _clear_auth_dialog_if_present(page, settle_seconds=2.0)
                if auth_ok:
                    if popup_step_id:
                        update_tab1_auth_progress(
                            popup_step_id,
                            "running",
                            auth_note or "Auth dialog re-cleared during reply wait",
                        )
                    print("[browser_chat] Auth dialog re-cleared during wait loop")
                else:
                    if popup_step_id:
                        mark_tab1_auth_progress_error(
                            popup_step_id,
                            auth_note or "Auth dialog persisted during reply wait",
                        )
                    print(f"[browser_chat] Auth dialog persisted during wait loop: {auth_note}")
        except Exception as exc:
            print(f"[browser_chat] Auth wait-loop check error (non-fatal): {exc}")


def _is_m365_chat_url(url: str) -> bool:
    current = (url or "").lower()
    return "m365.cloud.microsoft/chat" in current or "copilot.microsoft.com" in current


async def _goto_m365_chat_page(page: Page, timeout_ms: int = 30_000) -> str:
    """Navigate to M365 chat and tolerate redirect-style ERR_ABORTED transitions."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            await page.goto(_M365_CHAT_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            last_error = exc
            current_url = page.url or ""
            if _is_m365_chat_url(current_url):
                return current_url
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 10_000))
            except Exception:
                pass
            current_url = page.url or ""
            if _is_m365_chat_url(current_url):
                return current_url
            if "ERR_ABORTED" in str(exc) and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            raise
        current_url = page.url or ""
        if _is_m365_chat_url(current_url):
            return current_url
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 10_000))
        except Exception:
            pass
        current_url = page.url or ""
        if _is_m365_chat_url(current_url):
            return current_url
    current_url = page.url or ""
    if _is_m365_chat_url(current_url):
        return current_url
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"M365 chat navigation did not reach target (url={current_url}){detail}")


async def _prepare_m365_chat_page(
    page: Page,
    timeout_ms: int = 30_000,
    settle_seconds: float = 8.0,
) -> tuple[bool, str | None]:
    """Only treat a page as ready once auth is clear and the M365 composer is visible."""
    try:
        await _goto_m365_chat_page(page, timeout_ms=timeout_ms)
    except Exception as exc:
        return False, f"navigate failed: {exc}"

    auth_ok, auth_error = await _clear_auth_dialog_if_present(page, settle_seconds=settle_seconds)
    if not auth_ok:
        return False, auth_error

    try:
        await page.wait_for_selector(_M365_COMPOSER_SEL, state="visible", timeout=25_000)
    except Exception as exc:
        return False, f"M365 composer not ready on {page.url or 'unknown'}: {exc}"

    if await _page_has_auth_dialog(page):
        return False, "Authentication required dialog persisted after page preparation"

    return True, None


async def _page_service_issue_text(page: Page) -> str | None:
    """Return the visible M365 service-error phrase when the page is not turn-ready."""
    phrases = list(_M365_SERVICE_ERROR_PHRASES)
    try:
        hit = await page.evaluate(
            """(phrases) => {
                const body = (document.body && (document.body.innerText || document.body.textContent || "")) || "";
                const text = body.toLowerCase();
                return phrases.find(p => text.includes(p)) || null;
            }""",
            phrases,
        )
        if hit:
            return str(hit)
    except Exception:
        pass
    hint = await _page_text_hint(page, limit=4000)
    for phrase in phrases:
        if phrase in hint:
            return phrase
    return None


async def _page_has_visible_composer(page: Page, timeout_ms: int = 2_000) -> bool:
    try:
        await page.wait_for_selector(_M365_COMPOSER_SEL, state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _wait_for_m365_chat_ready(
    page: Page,
    timeout_s: float = 30.0,
    stable_s: float = 5.0,
) -> tuple[bool, str | None]:
    """
    Wait until the visible M365 chat tab is actually ready for a turn.

    "Ready" means:
      - no auth dialog is blocking the page
      - no service-communication banner is visible
      - the composer is visible for a continuous stable window
    """
    deadline = time.monotonic() + max(3.0, timeout_s)
    stable_since: float | None = None
    last_issue: str | None = None
    refresh_attempted = False

    while time.monotonic() < deadline:
        auth_ok, auth_note = await _clear_auth_dialog_if_present(page, settle_seconds=1.5)
        if not auth_ok:
            stable_since = None
            last_issue = auth_note or "Authentication required on m365.cloud.microsoft"
            await asyncio.sleep(1.5)
            continue

        service_issue = await _page_service_issue_text(page)
        if service_issue:
            stable_since = None
            last_issue = service_issue
            if not refresh_attempted:
                refresh_attempted = True
                try:
                    await _goto_m365_chat_page(page, timeout_ms=20_000)
                    await asyncio.sleep(4.0)
                    continue
                except Exception as exc:
                    last_issue = f"{service_issue}; refresh failed: {exc}"
            await asyncio.sleep(2.0)
            continue

        composer_ready = await _page_has_visible_composer(page, timeout_ms=2_000)
        if not composer_ready:
            stable_since = None
            last_issue = f"Composer not ready on {page.url or 'unknown'}"
            await asyncio.sleep(1.5)
            continue

        if stable_since is None:
            stable_since = time.monotonic()
        elif (time.monotonic() - stable_since) >= max(1.0, stable_s):
            return True, None

        await asyncio.sleep(1.0)

    return False, last_issue or "M365 chat did not stabilize before prompt send"


async def _recover_tab1_after_turn_failure(
    page: Page,
    context: BrowserContext,
    *,
    reason: str = "",
) -> tuple[Page, str | None]:
    """
    Recover Tab 1 after a cold-start first-turn failure.

    This is intentionally narrow: it is only used by validate_tab1_with_hello
    when the first post-restart turn times out with no reply text.
    """
    reason = (reason or "unknown").strip()
    try:
        print(f"[validate_tab1] Recovering Tab 1 after first-turn failure: {reason}")
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            pass

        ok, prep_error = await _prepare_m365_chat_page(page, timeout_ms=30_000, settle_seconds=6.0)
        if not ok:
            raise RuntimeError(prep_error or "M365 chat page not ready after recovery refresh")

        ready_ok, ready_error = await _wait_for_m365_chat_ready(page, timeout_s=20.0, stable_s=3.0)
        if not ready_ok:
            raise RuntimeError(ready_error or "M365 chat did not stabilize after recovery refresh")
        return page, None
    except Exception as exc:
        print(f"[validate_tab1] Tab 1 refresh recovery failed: {exc}")
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass
        try:
            replacement = await context.new_page()
            ok, prep_error = await _prepare_m365_chat_page(replacement, timeout_ms=30_000, settle_seconds=6.0)
            if not ok:
                raise RuntimeError(prep_error or "replacement Tab 1 not ready")
            ready_ok, ready_error = await _wait_for_m365_chat_ready(replacement, timeout_s=20.0, stable_s=3.0)
            if not ready_ok:
                raise RuntimeError(ready_error or "replacement Tab 1 did not stabilize")
            return replacement, None
        except Exception as replacement_exc:
            return page, f"Tab 1 recovery failed after '{reason}': {replacement_exc}"


async def _restore_tab1_focus(context: BrowserContext | None = None) -> bool:
    """Return the visible noVNC browser to the dedicated non-pool auth tab."""
    try:
        ctx = context or await _get_context()
        tab1 = await _get_or_create_page(ctx)
        if tab1.is_closed():
            return False
        await tab1.bring_to_front()
        return True
    except Exception:
        return False


class PagePool:
    """
    Agent-keyed pool of browser tabs for M365 Chat.

    Pre-creates N tabs at startup (with concurrency limit of 2 to avoid
    overwhelming Chromium).  Tabs are then lazily **assigned** to agents
    on first request — each AI agent (c2-aider, c5-claude-code, etc.) gets
    a dedicated sticky tab.  Per-agent asyncio.Lock serialises concurrent
    requests within one agent while different agents run in full parallel.
    """

    _M365_CHAT_URL = _M365_CHAT_URL
    _COMPOSER_SEL = _M365_COMPOSER_SEL
    _CREATE_CONCURRENCY = 2

    def __init__(self, size: int) -> None:
        self._size = size
        self._base_size = size
        self._agent_tabs: dict[str, Page] = {}
        self._agent_locks: dict[str, asyncio.Lock] = {}
        self._free_tabs: asyncio.Queue[Page] = asyncio.Queue()
        self._meta_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._context: BrowserContext | None = None
        self._trim_generation = 0

    async def initialize(self, context: BrowserContext, progress_step_id: str | None = None) -> None:
        """Pre-create N tabs (concurrency-limited to avoid browser overload)."""
        async with self._init_lock:
            if self._initialized:
                return
            self._context = context
            _t0 = time.monotonic()
            print(f"[PagePool] Pre-creating {self._size} tabs (max {self._CREATE_CONCURRENCY} concurrent)...")
            if progress_step_id:
                update_tab1_auth_progress(
                    progress_step_id,
                    "running",
                    f"Preparing base pool tabs 0/{self._size} ready",
                )
            _sync_pool_monitor(
                self,
                phase="preparing",
                detail=f"Preparing base pool tabs 0/{self._size} ready",
                requested_target=self._size,
                target_size=self._size,
            )
            sem = asyncio.Semaphore(self._CREATE_CONCURRENCY)

            async def _init_one(idx: int) -> "Page | None":
                async with sem:
                    page = None
                    try:
                        page = await context.new_page()
                        _pool_pages.add(page)
                        ok, prep_error = await _prepare_m365_chat_page(page, timeout_ms=30_000)
                        if not ok:
                            raise RuntimeError(prep_error or "M365 chat page not ready")
                        ready = self._free_tabs.qsize() + 1
                        if progress_step_id:
                            update_tab1_auth_progress(
                                progress_step_id,
                                "running",
                                f"Preparing base pool tabs {ready}/{self._size} ready",
                            )
                        _sync_pool_monitor(
                            self,
                            phase="preparing",
                            detail=f"Preparing base pool tabs {ready}/{self._size} ready",
                            target_size=self._size,
                        )
                        print(f"[PagePool] Tab {idx + 1}/{self._size} ready: {page.url[:60]}")
                        return page
                    except Exception as exc:
                        print(f"[PagePool] Tab {idx + 1} init error (skipped): {exc}")
                        if page is not None:
                            _pool_pages.discard(page)
                            try:
                                await page.close()
                            except Exception:
                                pass
                        return None

            results = await asyncio.gather(*[_init_one(i) for i in range(self._size)])
            for page in results:
                if page is not None:
                    await self._free_tabs.put(page)
            _ms = int((time.monotonic() - _t0) * 1000)
            ready = self._free_tabs.qsize()
            print(f"[PagePool] {ready}/{self._size} tabs ready in {_ms}ms")
            _sync_pool_monitor(
                self,
                phase="ready" if ready > 0 else "error",
                detail=f"Base pool ready: {ready}/{self._size} tabs free",
                target_size=self._size,
            )
            # Only mark initialized if at least one tab succeeded.
            # If all tabs failed (e.g. DNS timing race at startup), stay
            # uninitialized so the next acquire() can trigger a re-init.
            if ready > 0:
                self._initialized = True
            else:
                print("[PagePool] 0 tabs ready — will retry on next acquire()")

    async def _create_tab(self, label: str) -> Page:
        """Create one new tab (for replacement after failures)."""
        assert self._context is not None
        page = await self._context.new_page()
        _pool_pages.add(page)
        ok, prep_error = await _prepare_m365_chat_page(page, timeout_ms=30_000)
        if not ok:
            _pool_pages.discard(page)
            try:
                await page.close()
            except Exception:
                pass
            raise RuntimeError(prep_error or f"M365 chat replacement tab '{label}' not ready")
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
            # Try to get a pre-created tab from the free pool (fast path).
            # If none are available (e.g. pool failed to init at startup),
            # create a new tab on-demand as a self-healing fallback.
            page: "Page | None" = None
            try:
                page = self._free_tabs.get_nowait()
                print(f"[PagePool] Assigned pre-created tab to agent '{agent_id}'")
            except asyncio.QueueEmpty:
                pass

            if page is None:
                # Free pool exhausted — create a tab on-demand (self-healing path).
                print(f"[PagePool] Free pool empty — creating on-demand tab for '{agent_id}'")
                assert self._context is not None, "PagePool context not set"
                try:
                    page = await self._create_tab(agent_id)
                except Exception as exc:
                    lock.release()
                    raise TimeoutError(
                        f"PagePool: failed to create on-demand tab for '{agent_id}': {exc}"
                    )

            self._agent_tabs[agent_id] = page

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

    async def reinitialize(self, context: "BrowserContext | None" = None) -> None:
        """Reset and re-run pool initialization, closing stale free tabs first.

        Tab 1 (the auth/setup tab opened by warm_browser_for_novnc) is NOT in
        _pool_pages and is never touched here — it stays dedicated to auth only.
        """
        async with self._init_lock:
            self._initialized = False
            if context is not None:
                self._context = context
            # Drain and close old free tabs so reinit doesn't double the pool size.
            # Agent-assigned tabs (_agent_tabs) are left alive — they're still in use.
            drained = 0
            while not self._free_tabs.empty():
                try:
                    old_page = self._free_tabs.get_nowait()
                    _pool_pages.discard(old_page)
                    try:
                        await old_page.close()
                    except Exception:
                        pass
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if drained:
                print(f"[PagePool] reinitialize: drained {drained} stale free tab(s)")
        await self.initialize(self._context or context)

    async def reload_all_tabs(self) -> int:
        """Reload every pool tab so fresh session cookies take effect.

        Called automatically after cookie extraction completes.
        Tab 1 (auth tab, NOT in _pool_pages) is not touched here.
        Skips tabs currently checked out by agents to prevent hanging.
        Returns number of tabs successfully reloaded.
        """
        reloaded = 0
        agent_pages = set(self._agent_tabs.values())
        pages_to_reload = [p for p in _pool_pages if p not in agent_pages]
        skipped = len(_pool_pages) - len(pages_to_reload)
        if skipped:
            print(f"[PagePool] reload_all_tabs: skipping {skipped} agent-checked-out tab(s)")
        _sync_pool_monitor(self, phase="reloading", detail=f"Reloading {len(pages_to_reload)} pool tab(s)")
        for page in pages_to_reload:
            try:
                if page.is_closed():
                    continue
                await page.reload(wait_until="domcontentloaded", timeout=15_000)
                reloaded += 1
                print(f"[PagePool] Reloaded pool tab: {page.url[:60]}")
            except Exception as exc:
                print(f"[PagePool] Tab reload failed (non-fatal): {exc}")
        await _restore_tab1_focus(self._context)
        _sync_pool_monitor(self, phase="ready", detail=f"Reloaded {reloaded} pool tab(s)", last_reloaded=reloaded)
        return reloaded

    async def expand_to(
        self,
        context: "BrowserContext",
        target_size: int,
        progress_step_id: str | None = None,
    ) -> int:
        """Add tabs until free + agent-assigned tabs total >= target_size.

        Never shrinks the pool — safe to call even if already at target.
        Called by C9 before bursty parallel runs to pre-warm extra tabs.
        Returns the number of new tabs actually created.
        """
        current_total = self._free_tabs.qsize() + len(self._agent_tabs)
        needed = max(0, target_size - current_total)
        if needed == 0:
            print(f"[PagePool] expand_to({target_size}): already at {current_total} tabs, no-op")
            _sync_pool_monitor(
                self,
                phase="ready",
                detail=f"Pool already at target {target_size}",
                requested_target=target_size,
                target_size=target_size,
                last_added=0,
            )
            return 0
        print(f"[PagePool] expand_to({target_size}): adding {needed} tab(s) (current={current_total})")
        if progress_step_id:
            update_tab1_auth_progress(
                progress_step_id,
                "running",
                f"Expanding pool to {target_size}: 0/{needed} additional tabs ready",
            )
        _sync_pool_monitor(
            self,
            phase="expanding",
            detail=f"Expanding pool to {target_size}: 0/{needed} additional tabs ready",
            requested_target=target_size,
            target_size=target_size,
        )
        sem = asyncio.Semaphore(self._CREATE_CONCURRENCY)

        async def _add_one(idx: int) -> "Page | None":
            async with sem:
                page = None
                try:
                    page = await context.new_page()
                    _pool_pages.add(page)
                    ok, prep_error = await _prepare_m365_chat_page(page, timeout_ms=30_000)
                    if not ok:
                        raise RuntimeError(prep_error or "M365 expansion tab not ready")
                    await self._free_tabs.put(page)
                    ready = max(0, self._free_tabs.qsize() + len(self._agent_tabs) - current_total)
                    if progress_step_id:
                        update_tab1_auth_progress(
                            progress_step_id,
                            "running",
                            f"Expanding pool to {target_size}: {ready}/{needed} additional tabs ready",
                        )
                    _sync_pool_monitor(
                        self,
                        phase="expanding",
                        detail=f"Expanding pool to {target_size}: {ready}/{needed} additional tabs ready",
                        target_size=target_size,
                    )
                    print(f"[PagePool] Expansion tab {idx + 1}/{needed} ready: {page.url[:60]}")
                    return page
                except Exception as exc:
                    print(f"[PagePool] Expansion tab {idx + 1} failed (skipped): {exc}")
                    if page is not None:
                        _pool_pages.discard(page)
                        try:
                            await page.close()
                        except Exception:
                            pass
                    return None

        results = await asyncio.gather(*[_add_one(i) for i in range(needed)])
        added = sum(1 for r in results if r is not None)
        if target_size > self._size:
            self._size = target_size  # update declared pool size
        await self._schedule_idle_trim()
        await _restore_tab1_focus(context)
        _sync_pool_monitor(
            self,
            phase="ready",
            detail=f"Pool expanded to {self._size} with {self._free_tabs.qsize()} free tab(s)",
            requested_target=target_size,
            target_size=target_size,
            last_added=added,
        )
        print(f"[PagePool] expand_to({target_size}): added {added}/{needed} tabs, pool now {self._free_tabs.qsize()} free")
        return added

    async def _schedule_idle_trim(self) -> None:
        idle_seconds = max(0, int(os.getenv("C3_POOL_IDLE_TRIM_SECONDS", "0") or "0"))
        if idle_seconds <= 0:
            return
        self._trim_generation += 1
        generation = self._trim_generation

        async def _trim_later() -> None:
            await asyncio.sleep(idle_seconds)
            if generation != self._trim_generation:
                return
            await self.trim_free_tabs()

        asyncio.create_task(_trim_later())

    async def trim_free_tabs(self) -> int:
        """Close surplus free tabs while keeping sticky agent tabs intact."""
        target_free = max(0, self._base_size - len(self._agent_tabs))
        to_close = max(0, self._free_tabs.qsize() - target_free)
        if to_close == 0:
            return 0
        closed = 0
        for _ in range(to_close):
            try:
                page = self._free_tabs.get_nowait()
            except asyncio.QueueEmpty:
                break
            _pool_pages.discard(page)
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
            closed += 1
        self._size = max(self._base_size, len(self._agent_tabs) + self._free_tabs.qsize())
        if closed:
            print(
                f"[PagePool] Trimmed {closed} surplus free tab(s); "
                f"free={self._free_tabs.qsize()} agent_tabs={len(self._agent_tabs)}"
            )
            _sync_pool_monitor(
                self,
                phase="trimmed",
                detail=f"Trimmed {closed} surplus pool tab(s)",
            )
        return closed

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
            page_hint = await _page_text_hint(page, limit=2000)
            if "authentication required" in page_hint and "continue" in page_hint:
                return False
        except Exception:
            page_hint = ""
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
            if any(tok in page_hint for tok in signed_in_ui_markers):
                return True
            if any(tok in page_hint for tok in ("sign in", "log in")):
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
    invalidate_tab1_ready_state("setup_page_loaded")
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
    A global semaphore limits concurrent Playwright operations to the configured
    C3_CHAT_MAX_CONCURRENT value to prevent
    browser resource exhaustion.  Different agents run in parallel; concurrent
    requests from the same agent are serialised by a per-agent lock.

    Returns dict with 'text', 'events', 'success', and optionally 'error'.
    """
    global _page_pool, _chat_semaphore

    context = await _get_context()
    ready_check = await ensure_tab1_ready_for_pool(timeout_ms=max(timeout_ms, 60_000))
    if not ready_check.get("validated"):
        return {
            "success": False,
            "error": ready_check.get("error") or "Tab 1 is not authenticated yet",
            "events": [],
            "text": "",
            "tab1_url": ready_check.get("tab1_url", ""),
        }

    pool_size = max(1, int(os.getenv("C3_CHAT_TAB_POOL_SIZE", "4")))
    async with _chat_lock:
        if _page_pool is None:
            _page_pool = PagePool(pool_size)
        if _chat_semaphore is None:
            max_concurrent = max(1, int(os.getenv("C3_CHAT_MAX_CONCURRENT", "4")))
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
    fresh_chat: bool = True,
    progress_steps: dict[str, str] | None = None,
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

    def _progress(kind: str, status: str, detail: str = "") -> None:
        if not progress_steps:
            return
        step_id = progress_steps.get(kind)
        if not step_id:
            return
        update_tab1_auth_progress(step_id, status, detail)

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

    def _parse_frame(payload) -> list[dict]:
        """Parse one or more JSON messages from a WebSocket frame.
        SignalR frames are delimited by \\x1e (record separator)."""
        if isinstance(payload, (bytes, bytearray, memoryview)):
            try:
                payload = bytes(payload).decode("utf-8", errors="replace")
            except Exception:
                return []
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

    def _on_ws_recv(payload) -> None:
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

    def _on_ws_sent(payload) -> None:
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

    # ── Phase 3: ready the chat surface ────────────────────────────────────────
    # fresh_chat=True:
    #   1. Composer already visible (fresh tab or idle) → use immediately
    #   2. Click "New chat" sidebar button → wait for composer
    #   3. Full page teardown (about:blank → m365 chat) as last resort
    # fresh_chat=False:
    #   Reuse the current conversation if the composer is still visible.
    _t_nav_start = time.monotonic()
    _fast_reset_ok = False
    _combined = _M365_COMPOSER_SEL
    if not fresh_chat and "m365.cloud.microsoft" in (page.url or ""):
        try:
            await page.wait_for_selector(_combined, state="visible", timeout=5_000)
            _fast_reset_ok = True
            print(f"[browser_chat] Reusing current conversation — {page.url[:60]}")
        except Exception:
            pass

    if fresh_chat and "m365.cloud.microsoft" in (page.url or ""):
        try:
            _clicked = await page.evaluate("""() => {
                // Use dispatchEvent so React's synthetic event system receives the click
                function _reactClick(el) {
                    el.dispatchEvent(new MouseEvent('click', {
                        bubbles: true, cancelable: true, view: window
                    }));
                }
                const nc = document.querySelector(
                    '[data-testid="sidebar-new-conversation-nav-item"]'
                );
                if (nc) { _reactClick(nc); return true; }
                for (const b of document.querySelectorAll('button,[role="button"]')) {
                    const t = ((b.textContent || '') +
                               (b.getAttribute('aria-label') || '')).toLowerCase();
                    if (t.includes('new chat') || t.includes('new conversation')) {
                        _reactClick(b);
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
            else:
                try:
                    await page.wait_for_selector(_combined, state="visible", timeout=3_000)
                    _fast_reset_ok = True
                    print(f"[browser_chat] Composer ready (no new-chat btn) — {page.url[:60]}")
                except Exception:
                    pass
        except Exception:
            pass

    if not _fast_reset_ok:
        print("[browser_chat] Fast reset unavailable — full page teardown")
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=15_000)
            ok, prep_error = await _prepare_m365_chat_page(page, timeout_ms=30_000, settle_seconds=6.0)
            if not ok:
                raise RuntimeError(prep_error or "M365 chat teardown failed")
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

    # ── Phase 3.5: Click Work or Web mode toggle ──────────────────────────────
    # M365 Copilot Chat shows a "Work | Web" segmented button at page top.
    # Per-request override via `mode` arg; env var sets the persistent default.
    _chat_mode_target = (mode or os.getenv("M365_CHAT_MODE", "work")).strip().lower()
    if _chat_mode_target in ("work", "web") and "m365.cloud.microsoft" in (page.url or ""):
        _mode_label = _chat_mode_target.capitalize()
        try:
            _clicked_mode = await page.evaluate("""(label) => {
                const lo = label.toLowerCase();
                const candidates = [
                    ...document.querySelectorAll('[role="tab"],[role="button"],button')
                ];
                const el = candidates.find(b => {
                    const txt = (b.textContent || '').trim().toLowerCase();
                    const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return txt === lo || aria === lo || aria.startsWith(lo);
                });
                if (el) {
                    el.dispatchEvent(new MouseEvent('click', {
                        bubbles: true, cancelable: true, view: window
                    }));
                    return true;
                }
                return false;
            }""", _mode_label)
            if _clicked_mode:
                await asyncio.sleep(0.3)  # let React re-render after mode switch
                print(f"[browser_chat] Mode set to '{_mode_label}'")
            else:
                print(f"[browser_chat] Work/Web toggle not found — current page mode unchanged")
        except Exception as _mode_err:
            print(f"[browser_chat] Mode click error (non-fatal): {_mode_err}")

    try:
        # Use page.evaluate() for auth dialog check — immune to overlay dialogs
        await asyncio.sleep(0.3)  # Brief settle after nav

        auth_ok, auth_note = await _clear_auth_dialog_if_present(page, settle_seconds=8.0)
        if not auth_ok:
            _progress("popup_watch", "error", auth_note or "Authentication required on m365.cloud.microsoft")
            _maybe_invalidate_tab1_for_auth(page, "auth_dialog_present")
            return {
                "success": False,
                "error": auth_note or "Authentication required on m365.cloud.microsoft",
                "events": [], "text": "",
            }, page

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
            _progress("prepare", "error", err)
            print(f"[browser_chat] {err}")
            return {"success": False, "error": err, "events": [], "text": ""}, page

        # ── Text input via Playwright keyboard (React-compatible) ────────
        _t_type_start = time.monotonic()
        _progress("prepare", "done", f"Chat surface ready on {dom_info.get('url', page.url or '')[:120]}")
        _progress("type", "running", f"Typing {len(prompt)} characters")
        composer = page.locator(composer_sel).first
        try:
            await composer.click(force=True, timeout=5_000)
        except Exception:
            await page.evaluate(f"""(sel) => {{
                const el = document.querySelector(sel);
                if (el) {{ el.focus(); el.click(); }}
            }}""", composer_sel)
        await asyncio.sleep(0.15)

        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.1)

        await page.keyboard.type(prompt)
        await asyncio.sleep(0.3)
        print(f"[browser_chat] Typed prompt ({len(prompt)} chars)")
        _progress("type", "done", f"Typed {len(prompt)} characters")

        # ── Submit: 3-tier strategy for React synthetic events ──
        # Tier 1: Enter key (keyboard events always reach React, no element targeting needed)
        # Tier 2: Playwright native click (dispatches full pointer events React handles)
        # Tier 3: dispatchEvent with bubbles:true (bubbles through React's event delegation)
        _timings["type_ms"] = int((time.monotonic() - _t_type_start) * 1000)
        _t_submit = time.monotonic()
        send_sel = dom_info.get("sendBtn")
        _progress("submit", "running", "Submitting prompt")

        # Tier 1: Enter key on the focused composer
        await page.keyboard.press("Enter")
        submit_note = "Submitted via Enter"
        print("[browser_chat] Tier1: submitted via Enter key")
        await asyncio.sleep(0.5)

        # Check if send button is still enabled (means Enter didn't submit)
        _btn_still_active = False
        if send_sel:
            try:
                _btn_still_active = await page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    return el ? !el.disabled : false;
                }""", send_sel)
            except Exception:
                pass

        if _btn_still_active:
            print(f"[browser_chat] Enter did not submit (btn still active) — Tier2/3")
            # Tier 2: Playwright native click (full pointer event chain)
            try:
                btn = page.locator(send_sel).first
                await btn.click(force=True, timeout=3_000)
                submit_note = "Submitted via send button"
                print(f"[browser_chat] Tier2: Playwright clicked send button")
            except Exception:
                # Tier 3: React-compatible dispatchEvent (bubbles through event delegation)
                await page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (el) {
                        el.dispatchEvent(new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window
                        }));
                    }
                }""", send_sel)
                submit_note = "Submitted via fallback dispatchEvent"
                print(f"[browser_chat] Tier3: dispatchEvent on send button")
        _progress("submit", "done", submit_note)
        _progress("popup_watch", "running", "Watching for auth popup while waiting for reply")
        _progress("reply", "running", "Waiting for Copilot reply")

        # ── Wait for response — with smart retry on Copilot service errors ──────
        # Three possible outcomes from each attempt:
        #   (a) Good WS response → done_event set, collected_text has data
        #   (b) "Something went wrong" → detect it, click "Try again", retry up to 3x
        #   (c) Timeout → check spinner (still generating?) → extend wait → DOM fallback
        timeout_s = timeout_ms / 1000.0
        _MAX_CHAT_RETRIES = 3
        text = ""

        for _attempt in range(_MAX_CHAT_RETRIES + 1):
            # ── Wait for WS completion signal ──────────────────────────────────
            got_done = await _wait_for_done_with_auth_watch(
                page,
                done_event,
                timeout_s,
                progress_steps.get("popup_watch") if progress_steps else None,
            )
            try:
                if not got_done:
                    raise asyncio.TimeoutError
                print(f"[browser_chat] WS done_event received (attempt {_attempt + 1})")
                # After WS signals done, Copilot may still be rendering the final DOM.
                # Wait briefly so we capture the complete response text.
                await asyncio.sleep(1.5)
            except asyncio.TimeoutError:
                # Before giving up, check if Copilot is still actively generating.
                # The "Stop generating" button or a spinner = response still in progress.
                still_gen = await page.evaluate("""() => {
                    for (const b of document.querySelectorAll('button,[role="button"]')) {
                        const txt = ((b.textContent || '') +
                                     (b.getAttribute('aria-label') || '')).toLowerCase();
                        if (txt.includes('stop generating') ||
                            txt.includes('stop responding')) return 'stop_btn';
                    }
                    const spinners = document.querySelectorAll(
                        '[data-testid*="typing"], [class*="typing-indicator"], ' +
                        '[class*="TypingIndicator"], [class*="spinner"], ' +
                        '[aria-label*="generating"], [aria-label*="loading"]'
                    );
                    return spinners.length > 0 ? 'spinner' : null;
                }""")
                if still_gen:
                    print(f"[browser_chat] Copilot still generating ({still_gen}) — extending wait 60s")
                    try:
                        got_done = await _wait_for_done_with_auth_watch(page, done_event, 60.0)
                        if not got_done:
                            raise asyncio.TimeoutError
                        await asyncio.sleep(1.5)  # settle after extended wait too
                    except asyncio.TimeoutError:
                        print("[browser_chat] Extended wait expired — proceeding with DOM fallback")
                else:
                    print(f"[browser_chat] WS timeout after {timeout_s:.0f}s — trying DOM fallback")

            # ── Collect what WS delivered ──────────────────────────────────────
            text = "".join(collected_text)

            # ── DOM fallback: extract response if WS gave nothing ─────────────
            # Also used to confirm the final rendered text after WS done.
            if not text:
                await asyncio.sleep(2)  # give React time to finish rendering
                dom_text = await page.evaluate("""() => {
                    // Helper: strip the M365 Copilot sender label ("Copilot") that
                    // appears at the top of each [role="article"] turn as a UI label.
                    // The innerText of the container includes it; we remove it here.
                    function stripSenderLabel(t) {
                        // Removes leading "Copilot", "Microsoft Copilot", "Copilot said:"
                        // and surrounding whitespace/newlines from the raw innerText.
                        return t.replace(/^(Microsoft\\s+)?Copilot(\\s+said)?\\s*[:\\.]?\\s*\\n?/i, '').trim();
                    }

                    // Strategy 1: explicit bot-message content containers (most precise)
                    // These point directly at the message body, not the full turn article.
                    const botSels = [
                        '[data-content="ai-message"]',
                        '[data-is-bot-message="true"]',
                        '.ac-textBlock',
                        '[class*="assistantMessage"]',
                        '[class*="botMessage"]',
                        '[data-testid*="bot"][data-testid*="message"]',
                    ];
                    for (const sel of botSels) {
                        const msgs = document.querySelectorAll(sel);
                        if (msgs.length > 0) {
                            const last = msgs[msgs.length - 1];
                            const t = stripSenderLabel((last.innerText || last.textContent || '').trim());
                            if (t) return t;
                        }
                    }

                    // Strategy 2: role="article" = each chat turn (includes sender label)
                    // Iterate in reverse to find the last Copilot turn.
                    // Skip user messages (heuristic: no multi-line content, very short text).
                    const articles = [...document.querySelectorAll('[role="article"]')];
                    for (let i = articles.length - 1; i >= 0; i--) {
                        const raw = (articles[i].innerText || articles[i].textContent || '').trim();
                        const t = stripSenderLabel(raw);
                        // Must have real content (>15 chars) after stripping the sender label
                        if (t && t.length > 15) return t;
                    }

                    // Strategy 3: last turn container → extract p/li/pre/code children
                    const turns = document.querySelectorAll('[class*="turn"]');
                    if (turns.length > 1) {
                        const last = turns[turns.length - 1];
                        const ps = [...last.querySelectorAll('p, li, pre, code')];
                        const parts = ps.map(p => (p.innerText || '').trim()).filter(Boolean);
                        if (parts.length) return parts.join('\\n');
                    }
                    return '';
                }""")
                if dom_text and dom_text.strip():
                    text = dom_text.strip()
                    print(f"[browser_chat] DOM fallback extracted {len(text)} chars")

            # ── Post-process: strip residual M365 sender label from WS text ───
            # WS frames sometimes include "Copilot said:" or "Copilot\n" as a
            # preamble in the message text field — clean it from both sources.
            if text:
                import re as _re
                text = _re.sub(
                    r'^(Microsoft\s+)?Copilot(\s+said)?\s*[:\.]?\s*\n',
                    '', text, flags=_re.IGNORECASE
                ).strip()

            # ── Service-error detection: "Something went wrong" / high demand ──
            # Copilot M365 shows various service-overload messages.
            # The UI renders a "Try again" button — click it and retry.
            _SERVICE_PHRASES = _M365_SERVICE_ERROR_PHRASES
            _is_service_err = any(p in text.lower() for p in _SERVICE_PHRASES)

            if _is_service_err and _attempt < _MAX_CHAT_RETRIES:
                _clicked_retry = await page.evaluate("""() => {
                    // Find the "Try again" / "Retry" button Copilot shows after errors
                    const allBtns = [...document.querySelectorAll('button,[role="button"]')];
                    for (const b of allBtns) {
                        const t = ((b.textContent || '') +
                                   (b.getAttribute('aria-label') || '')).toLowerCase().trim();
                        if (t === 'try again' || t.startsWith('try again') ||
                            t === 'retry') {
                            b.dispatchEvent(new MouseEvent('click', {
                                bubbles: true, cancelable: true, view: window
                            }));
                            return true;
                        }
                    }
                    return false;
                }""")

                # Clear state BEFORE sleeping so new WS events during backoff are captured
                done_event.clear()
                collected_text.clear()
                text = ""

                _wait_s = (_attempt + 1) * 12  # 12s → 24s → 36s backoff
                if _clicked_retry:
                    print(f"[browser_chat] ⚠️ Service error — clicked 'Try again', "
                          f"waiting {_wait_s}s (attempt {_attempt + 1}/{_MAX_CHAT_RETRIES})")
                else:
                    print(f"[browser_chat] ⚠️ Service error — 'Try again' not found, "
                          f"waiting {_wait_s}s anyway (attempt {_attempt + 1}/{_MAX_CHAT_RETRIES})")
                await asyncio.sleep(_wait_s)
                continue  # back to top of retry loop

            # Good response or out of retries — exit loop
            break

        # ── Final result ──────────────────────────────────────────────────────
        _SERVICE_FINAL_PHRASES = _M365_SERVICE_ERROR_PHRASES
        _service_error_final = any(p in text.lower() for p in _SERVICE_FINAL_PHRASES)
        success = bool(text) and not _service_error_final
        _timings["ws_wait_ms"] = int((time.monotonic() - _t_submit) * 1000)
        _timings["total_ms"] = int((time.monotonic() - _t_start) * 1000)
        _timings["text_len"] = len(text)
        _timings["success"] = success
        _timings["attempts"] = _attempt + 1
        print(f"[browser_chat] PERF: {_timings}")
        print(f"[browser_chat] success={success}, recv={events_recv[:10]}, sent={events_sent[:10]}, "
              f"ws_urls={[u[:60] for u in ws_urls]}, text_len={len(text)}")
        result = {
            "success": success,
            "events": events_recv,
            "events_sent": events_sent,
            "ws_urls": ws_urls,
            "text": text,
            "perf": _timings,
        }
        if _service_error_final:
            result["service_error"] = True
            _progress("reply", "error", "M365 service returned a retry/error response")
        elif success:
            _progress("popup_watch", "done", "No auth popup blocked the turn")
            _progress("reply", "done", f"Reply received ({len(text)} chars)")
        else:
            result["error"] = "No Copilot reply captured before timeout"
            _progress("reply", "error", result["error"])
        return result, page

    except Exception as e:
        _progress("reply", "error", str(e))
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
            # Cookies look valid — also check if the PagePool is functional.
            pool_warning = None
            if _page_pool is not None and _page_pool._initialized and _page_pool.available == 0 and not _page_pool.agents:
                pool_warning = "pool_exhausted_no_tabs"
            return {"session": "active", "profile": profile, "reason": None,
                    "checked_at": now, "pool_warning": pool_warning}
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


async def validate_tab1_with_hello(timeout_ms: int = 45_000) -> dict:
    """Validate Tab 1 (the auth/setup tab) is truly authenticated by sending 'Hello'
    on the M365 Copilot chat interface and waiting for a real reply.

    Tab 1 = the non-pool page opened by warm_browser_for_novnc() at the /setup URL.
    It is NOT in _pool_pages and is never used by agents — it's the dedicated auth tab.

    Strategy:
      1. Get Tab 1 via _get_or_create_page(context)  (returns non-pool page)
      2. If not already on M365 chat, navigate there
      3. Send 'Hello' via _browser_chat_on_page
      4. If a real reply arrives → session confirmed active → pool tabs safe to reload
      5. Tab 1 stays on the chat page after validation (no need to return to /setup)

    Returns:
        {
          "validated": bool,
          "reply": str | None,       # first few chars of Copilot's reply
          "elapsed_ms": int,
          "tab1_url": str,
          "error": str | None
        }
    """
    import datetime
    import time as _time
    _t0 = _time.monotonic()
    checked_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    reset_tab1_auth_progress("validate-auth")
    update_tab1_auth_progress("c3_health", "running", "Starting Tab 1 auth validation")
    try:
        def _fail(step_id: str, error: str, *, reply: str | None = None, tab_url: str = "", follow_up_validated: bool = False) -> dict:
            mark_tab1_auth_progress_error(step_id, error)
            finish_tab1_auth_progress("error", error)
            return {
                "validated": False,
                "reply": reply[:120] if reply else None,
                "elapsed_ms": int((_time.monotonic() - _t0) * 1000),
                "tab1_url": tab_url,
                "error": error,
                "checked_at": checked_at,
                "follow_up_validated": follow_up_validated,
            }

        mark_tab1_auth_progress_done("c3_health", "C3 validator running")
        context = await _get_context()
        # _get_or_create_page returns a non-pool page = Tab 1
        tab1 = await _get_or_create_page(context)
        tab1_url = tab1.url or ""
        print(f"[validate_tab1] Tab 1 URL before validation: {tab1_url[:80]}")
        update_tab1_auth_progress("tab1_setup", "running", f"Tab 1 current URL: {tab1_url or 'about:blank'}")
        if "/setup" in tab1_url or "127.0.0.1:8001/setup" in tab1_url:
            mark_tab1_auth_progress_done("tab1_setup", "Tab 1 opened on /setup")
        elif tab1_url:
            mark_tab1_auth_progress_done("tab1_setup", f"Tab 1 already past setup: {tab1_url[:120]}")

        # Navigate to M365 chat if not already there
        if not _is_m365_chat_url(tab1_url):
            update_tab1_auth_progress("work_mode", "running", "Applying Work mode validation profile")
            mark_tab1_auth_progress_done("work_mode", "Work mode selected for validation")
            update_tab1_auth_progress("portal_connect", "running", "Connecting Tab 1 to the selected M365 portal")
            print(f"[validate_tab1] Navigating Tab 1 to {_M365_CHAT_URL}")
            try:
                await _goto_m365_chat_page(tab1, timeout_ms=25_000)
                await asyncio.sleep(2)
                tab1_url = tab1.url or ""
                print(f"[validate_tab1] Tab 1 now at: {tab1_url[:80]}")
                mark_tab1_auth_progress_done("portal_connect", f"Portal connect completed: {tab1_url[:120]}")
            except Exception as nav_exc:
                return _fail("m365_nav", f"navigation failed: {nav_exc}", tab_url=tab1_url)
        else:
            mark_tab1_auth_progress_done("work_mode", "Work mode already active on Tab 1")
            mark_tab1_auth_progress_done("portal_connect", "Tab 1 already connected to the M365 portal")
        update_tab1_auth_progress("m365_nav", "running", "Waiting for Tab 1 to settle on M365 chat")
        if not _is_m365_chat_url(tab1.url or ""):
            return _fail("m365_nav", "Tab 1 did not reach the M365 chat URL", tab_url=tab1.url or "")
        mark_tab1_auth_progress_done("m365_nav", f"Tab 1 navigated to {tab1.url or ''}")

        chat_mode = (os.getenv("M365_CHAT_MODE", "work") or "work").strip().lower()
        prompts = [
            (
                "hello",
                True,
                "hello_1",
                {
                    "prepare": "hello_prepare",
                    "type": "hello_type",
                    "submit": "hello_submit",
                    "popup_watch": "hello_popup_watch",
                    "reply": "hello_reply",
                },
            ),
            (
                "follow up 2",
                False,
                "follow_up_2",
                {
                    "prepare": "follow_prepare",
                    "type": "follow_type",
                    "submit": "follow_submit",
                    "popup_watch": "follow_popup_watch",
                    "reply": "follow_reply",
                },
            ),
        ]
        replies: list[str] = []

        update_tab1_auth_progress("session_auth", "running", "Checking M365 session state on Tab 1")
        update_tab1_auth_progress("popup_check_1", "running", "Checking for initial authentication popup")
        auth_ok, auth_error = await _clear_auth_dialog_if_present(tab1, settle_seconds=8.0)
        mark_tab1_auth_progress_done("popup_check_1", "Initial auth popup check completed")
        initial_popup_detail = (
            auth_error
            if auth_error
            else "No popup shown on initial check"
        )
        if not auth_ok:
            invalidate_tab1_ready_state("tab1_initial_auth_gate")
            return _fail("popup_continue_1", auth_error or "Initial authentication gate blocked", tab_url=tab1.url or "")
        mark_tab1_auth_progress_done("popup_continue_1", initial_popup_detail)
        mark_tab1_auth_progress_done("session_auth", "Tab 1 M365 session looks authenticated")
        update_tab1_auth_progress("stabilize_1", "running", "Allowing Tab 1 to settle after authentication")
        ready_ok, ready_error = await _wait_for_m365_chat_ready(tab1, timeout_s=30.0, stable_s=5.0)
        if not ready_ok:
            invalidate_tab1_ready_state("tab1_not_stable_after_auth")
            return _fail("stabilize_1", ready_error or "Tab 1 did not stabilize after authentication", tab_url=tab1.url or "")
        mark_tab1_auth_progress_done("stabilize_1", "Tab 1 stabilized after authentication")

        for prompt_text, fresh_chat, step_label, progress_steps in prompts:
            max_prompt_attempts = 2 if step_label == "hello_1" else 1
            attempt = 0
            prompt_succeeded = False
            last_result: dict = {}
            reply_text = ""
            while attempt < max_prompt_attempts:
                ready_ok, ready_error = await _wait_for_m365_chat_ready(
                    tab1,
                    timeout_s=20.0 if fresh_chat else 12.0,
                    stable_s=3.0 if fresh_chat else 2.0,
                )
                if not ready_ok:
                    invalidate_tab1_ready_state(f"{step_label}_not_ready")
                    return _fail(
                        progress_steps["prepare"],
                        ready_error or f"Tab 1 was not ready for {step_label}",
                        tab_url=tab1.url or "",
                    )
                if attempt == 0:
                    update_tab1_auth_progress(progress_steps["prepare"], "running", f"Preparing prompt '{prompt_text}'")
                else:
                    update_tab1_auth_progress(
                        progress_steps["prepare"],
                        "running",
                        f"Retrying prompt '{prompt_text}' after first-turn recovery",
                    )

                print(f"[validate_tab1] Sending '{prompt_text}' on Tab 1 ({step_label})… attempt {attempt + 1}/{max_prompt_attempts}")
                result, tab1 = await _browser_chat_on_page(
                    tab1,
                    context,
                    prompt_text,
                    mode=chat_mode,
                    timeout_ms=timeout_ms,
                    fresh_chat=fresh_chat,
                    progress_steps=progress_steps,
                )
                last_result = result
                reply_text = (result.get("text") or "").strip()
                success = result.get("success", False) and bool(reply_text)
                _SERVICE_ERR = ("something went wrong", "please try again", "experiencing high demand", "we're experiencing")
                if success and any(p in reply_text.lower() for p in _SERVICE_ERR):
                    success = False
                if success:
                    prompt_succeeded = True
                    break

                retryable_first_turn = (
                    step_label == "hello_1"
                    and attempt == 0
                    and not reply_text
                    and not result.get("service_error")
                )
                if retryable_first_turn:
                    recovery_reason = result.get("error") or "No Copilot reply captured before timeout"
                    update_tab1_auth_progress(
                        progress_steps["reply"],
                        "running",
                        f"First turn stalled after restart; refreshing Tab 1 and retrying once ({recovery_reason})",
                    )
                    tab1, recovery_error = await _recover_tab1_after_turn_failure(
                        tab1,
                        context,
                        reason=recovery_reason,
                    )
                    if recovery_error:
                        invalidate_tab1_ready_state(f"{step_label}_recovery_failed")
                        return _fail(
                            progress_steps["reply"],
                            recovery_error,
                            reply=reply_text,
                            tab_url=tab1.url or "",
                        )
                    attempt += 1
                    continue
                break

            if not prompt_succeeded:
                popup_step = _auth_progress_step(progress_steps["popup_watch"])
                if popup_step and popup_step.get("status") in ("pending", "running"):
                    mark_tab1_auth_progress_done(
                        progress_steps["popup_watch"],
                        popup_step.get("detail") or "Popup watch completed before reply failure",
                    )
                invalidate_tab1_ready_state(f"{step_label}_failed")
                return _fail(
                    progress_steps["reply"],
                    last_result.get("error") or f"{step_label} failed",
                    reply=reply_text,
                    tab_url=tab1.url or "",
                )
            mark_tab1_auth_progress_done(progress_steps["prepare"], f"Prompt surface ready for '{prompt_text}'")
            mark_tab1_auth_progress_done(progress_steps["type"], f"Typed '{prompt_text}'")
            mark_tab1_auth_progress_done(progress_steps["submit"], f"Submitted '{prompt_text}'")
            mark_tab1_auth_progress_done(progress_steps["popup_watch"], "No popup blocked this turn")
            mark_tab1_auth_progress_done(progress_steps["reply"], f"Reply received ({len(reply_text)} chars)")
            replies.append(reply_text)

            auth_ok, auth_error = await _clear_auth_dialog_if_present(tab1, settle_seconds=5.0)
            if not auth_ok or await _page_has_auth_dialog(tab1):
                invalidate_tab1_ready_state(f"{step_label}_popup_persisted")
                return _fail(
                    progress_steps["popup_watch"],
                    auth_error or f"Authentication dialog persisted after {step_label}",
                    reply=reply_text,
                    tab_url=tab1.url or "",
                )

        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        success = len(replies) == 2 and bool(replies[-1].strip())
        if success:
            _mark_tab1_ready({
                "reply": replies[-1][:120],
                "tab1_url": tab1.url or "",
                "checked_at": checked_at,
                "follow_up_validated": True,
            })
            update_tab1_auth_progress("pool_ready", "running", "Tab 1 validated; waiting for pool initialization")

        print(
            f"[validate_tab1] Result: validated={success}, "
            f"reply_len={len(replies[-1]) if replies else 0}, elapsed={elapsed_ms}ms"
        )
        return {
            "validated": success,
            "reply": replies[-1][:120] if replies else None,
            "elapsed_ms": elapsed_ms,
            "tab1_url": tab1.url or "",
            "error": None if success else "Tab 1 follow-up validation failed",
            "checked_at": checked_at,
            "follow_up_validated": success,
        }
    except Exception as exc:
        invalidate_tab1_ready_state("validate_tab1_exception")
        return _fail("c3_health", str(exc), tab_url="")


async def ensure_tab1_ready_for_pool(timeout_ms: int = 60_000, force_revalidate: bool = False) -> dict:
    """Ensure Tab 1 has completed auth + a two-step chat before pool tabs exist."""
    if _tab1_session_ready and not force_revalidate:
        result = dict(_tab1_session_meta)
        result.setdefault("validated", True)
        result["validated"] = True
        return result

    async with _tab1_ready_lock:
        if _tab1_session_ready and not force_revalidate:
            result = dict(_tab1_session_meta)
            result.setdefault("validated", True)
            result["validated"] = True
            return result

        result = await validate_tab1_with_hello(timeout_ms=timeout_ms)
        if not result.get("validated"):
            invalidate_tab1_ready_state(result.get("error") or "tab1_validation_failed")
        return result


async def prepare_pool_from_tab1(
    context: BrowserContext | None = None,
    reload_existing: bool = True,
    target_size: int | None = None,
    source: str = "pool-prepare",
) -> dict:
    """Initialize or refresh pool tabs only after Tab 1 has been validated."""
    global _page_pool
    context = context or await _get_context()
    pool_size = max(1, int(os.getenv("C3_CHAT_TAB_POOL_SIZE", "4")))
    requested_target = max(1, int(target_size or pool_size))
    effective_target = max(pool_size, requested_target)
    if not _tab1_auth_progress.get("active"):
        _resume_tab1_auth_progress(source)
    _sync_pool_monitor(
        _page_pool,
        phase="planning",
        source=source,
        detail=f"Pool target selected: {effective_target} tab(s)",
        requested_target=requested_target,
        target_size=effective_target,
        last_added=0,
        last_reloaded=0,
    )
    update_tab1_auth_progress("pool_target", "running", f"Selecting pool target {effective_target}")
    mark_tab1_auth_progress_done(
        "pool_target",
        (
            f"Pool target selected: {effective_target} tab(s)"
            if effective_target == pool_size
            else f"Pool target selected: {effective_target} tab(s) for burst mode"
        ),
    )
    update_tab1_auth_progress("pool_expand_request", "running", f"Preparing worker tab pool to {effective_target}")
    if _page_pool is None:
        _page_pool = PagePool(pool_size)
    pool = _page_pool
    reloaded = 0
    added = 0
    mark_tab1_auth_progress_done("pool_expand_request", f"Pool request accepted for target {effective_target}")
    update_tab1_auth_progress("pool_expand_progress", "running", f"Preparing pool tabs for target {effective_target}")
    if not pool._initialized:
        await pool.initialize(context, progress_step_id="pool_expand_progress")
    elif reload_existing and pool.available == 0:
        reloaded = await pool.reload_all_tabs()
        update_tab1_auth_progress(
            "pool_expand_progress",
            "running",
            f"Reloaded existing pool tabs; target remains {effective_target}",
        )
    else:
        print(f"[prepare_pool] Pool already healthy ({pool.available}/{pool.size} free) — skipping reload")
    current_total = pool.available + len(pool.agents)
    if effective_target > pool.size:
        added = await pool.expand_to(context, effective_target, progress_step_id="pool_expand_progress")
    elif effective_target > current_total:
        print(
            f"[prepare_pool] Pool at size={pool.size} (target={effective_target}); "
            f"{current_total} tabs tracked ({pool.available} free + {len(pool.agents)} agent); skipping expand"
        )
    mark_tab1_auth_progress_done(
        "pool_expand_progress",
        f"Pool prep complete: size={pool.size}, free={pool.available}, added={added}, reloaded={reloaded}",
    )
    update_tab1_auth_progress("pool_expand_verify", "running", "Verifying worker tabs inherit Tab 1 session")
    tab1_front = await _restore_tab1_focus(context)
    _sync_pool_monitor(
        pool,
        phase="verifying",
        detail="Verifying worker tabs inherit Tab 1 session",
        requested_target=requested_target,
        target_size=effective_target,
        last_added=added,
        last_reloaded=reloaded,
    )
    mark_tab1_auth_progress_done(
        "pool_expand_verify",
        f"Worker tabs verified ({pool.available}/{pool.size} free; tab1_front={tab1_front})",
    )
    mark_tab1_auth_progress_done(
        "pool_expand_done",
        f"Pool steady: target={effective_target}, size={pool.size}, free={pool.available}",
    )
    _sync_pool_monitor(
        pool,
        phase="ready",
        detail=f"Pool steady at {pool.size} tabs with {pool.available} free",
        requested_target=requested_target,
        target_size=effective_target,
        last_added=added,
        last_reloaded=reloaded,
    )
    return {
        "pool_initialized": pool._initialized,
        "pool_size": pool.size,
        "pool_available": pool.available,
        "pool_tabs_reloaded": reloaded,
        "pool_tabs_added": added,
        "target_size": effective_target,
        "tab1_front": tab1_front,
    }
