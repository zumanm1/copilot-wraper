"""
browser_auth/server.py
=======================
FastAPI server for Container 3.

Endpoints:
  GET  /health          — liveness check
  GET  /status          — browser + cookie status
  GET  /setup           — HTML form: portal profile + optional URL overrides
  POST /setup           — persist settings to mounted .env, reload C1 config
  POST /extract         — trigger cookie extraction; validates Tab 1 with 'Hello' before reloading pool tabs
  POST /navigate        — navigate browser to a URL (for manual login flows)
  POST /validate-auth   — validate Tab 1 auth by sending 'Hello' and getting real Copilot reply; reloads pool tabs on success
  POST /pool-expand     — expand pool to target_size tabs without closing existing ones
  POST /pool-reload     — reload all pool tabs (Tab 1 excluded)
  POST /pool-reset      — re-initialize PagePool from scratch
"""
from __future__ import annotations
import asyncio
import html
import json
import httpx
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from portal_urls import normalize_copilot_portal_url

from cookie_extractor import (
    browser_chat,
    ensure_tab1_ready_for_pool,
    _read_env_keys,
    check_session_health,
    extract_and_save,
    extract_access_token,
    finish_tab1_auth_progress,
    get_context,
    get_pool_monitor_snapshot,
    get_tab1_auth_progress_snapshot,
    invalidate_tab1_ready_state,
    patch_env_variable,
    portal_settings_from_env_file,
    prepare_pool_from_tab1,
    validate_tab1_with_hello,
    warm_browser_for_novnc,
)
import cookie_extractor as _ce


@asynccontextmanager
async def lifespan(app: FastAPI):
    skip_warm = os.getenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "").lower() in (
        "1",
        "true",
        "yes",
    )

    async def _delayed_warm_novnc() -> None:
        await asyncio.sleep(3)
        try:
            await warm_browser_for_novnc()
        except Exception as e:
            print(f"[browser-auth] noVNC warm skipped: {e}")

    if not skip_warm:
        asyncio.create_task(_delayed_warm_novnc())
    yield


app = FastAPI(
    title="Browser Auth — Cookie Extractor",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

ENV_PATH = os.getenv("ENV_PATH", "/app/.env")
API1_URL = os.getenv("API1_URL", "http://app:8000")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "browser-auth"}


@app.get("/session-health")
async def session_health():
    """Lightweight M365 session status check — no navigation, no chat."""
    try:
        result = await check_session_health(ENV_PATH)
        result["chat_mode"] = os.getenv("M365_CHAT_MODE", "work")
        status_code = 200
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:
        import datetime
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return JSONResponse(
            {"session": "unknown", "profile": "unknown", "reason": str(exc), "checked_at": now},
            status_code=503,
        )


@app.get("/auth-progress")
async def auth_progress():
    """Return the current in-memory Tab 1 auth progress snapshot."""
    return JSONResponse(get_tab1_auth_progress_snapshot())


@app.post("/token")
async def token():
    """Extract access_token from the browser's localStorage for Copilot WS auth."""
    try:
        result = await extract_access_token()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e), "access_token": None}, status_code=500)


@app.post("/chat")
async def chat(request: Request):
    """Proxy a chat request through the real browser WebSocket.

    The browser's native TLS fingerprint bypasses the null-method challenge
    that blocks programmatic WebSocket connections from aiohttp/httpx.

    Body: {"prompt": "...", "chat_mode": "work|web", "timeout": 30000, "agent_id": ""}
    Returns: {"success": bool, "text": "...", "events": [...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    # chat_mode: "work"|"web" — selects M365 Work/Web toggle before each message
    # Falls back to legacy "mode" field, then env default (M365_CHAT_MODE)
    mode = body.get("chat_mode") or body.get("mode") or ""
    timeout_ms = int(body.get("timeout", 30000))
    agent_id = body.get("agent_id", "")

    try:
        result = await browser_chat(prompt, mode=mode, timeout_ms=timeout_ms, agent_id=agent_id)
        status = 200 if result.get("success") else 502
        return JSONResponse(result, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False}, status_code=500)


_SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Copilot portal setup</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:36rem;margin:2rem auto;padding:0 1rem;}}
label{{display:block;margin-top:1rem;font-weight:600;}}
input[type=text]{{width:100%;box-sizing:border-box;padding:.4rem;}}
button{{margin-top:1.25rem;padding:.5rem 1rem;}}
p.note{{color:#555;font-size:.9rem;}}
p.warn{{color:#b45309;background:#fef3c7;border:1px solid #fbbf24;border-radius:6px;padding:.5rem .75rem;font-size:.88rem;}}
fieldset{{margin-top:1rem;border:1px solid #ccc;padding:1rem;border-radius:8px;}}
legend{{font-weight:700;padding:0 .35rem;}}
.portal-row{{display:flex;gap:.5rem;align-items:flex-start;margin-top:.65rem;font-weight:400;}}
.portal-row input{{margin-top:.2rem;}}
/* M365 Session LED */
#session-led{{
  position:fixed;top:1rem;right:1rem;
  display:inline-flex;align-items:center;gap:.45rem;
  font-size:.78rem;padding:.28rem .7rem;border-radius:20px;
  border:1px solid #ccc;background:#fff;cursor:default;white-space:nowrap;
  box-shadow:0 1px 4px rgba(0,0,0,.15);z-index:9999;
}}
#session-led-dot{{
  width:9px;height:9px;border-radius:50%;flex-shrink:0;
}}
#session-led.active{{border-color:#2da44e;}}
#session-led.active #session-led-dot{{background:#2da44e;}}
#session-led.active #session-led-lbl{{color:#2da44e;}}
#session-led.expired{{border-color:#cf222e;}}
#session-led.expired #session-led-dot{{background:#cf222e;animation:led-pulse 1.2s ease-in-out infinite;}}
#session-led.expired #session-led-lbl{{color:#cf222e;}}
#session-led.unknown{{border-color:#d4a72c;}}
#session-led.unknown #session-led-dot{{background:#d4a72c;}}
#session-led.checking{{border-color:#888;}}
#session-led.checking #session-led-dot{{background:#888;}}
@keyframes led-pulse{{0%,100%{{opacity:1;}}50%{{opacity:.35;}}}}
</style></head><body>
<div id="session-led" class="checking" title="M365 browser session status">
  <span id="session-led-dot"></span>
  <span id="session-led-lbl">Checking…</span>
</div>
<h1>Choose Copilot portal</h1>
{banner}
{mismatch_banner}
<p class="note">LAN-only UI. Pick where you will sign in. Values are saved to the mounted <code>.env</code> (no cookies shown).</p>
<p class="note">If noVNC at <code>:6080</code> stays black for a long time, wait a few seconds after the container starts (browser warms to this page), or run <code>POST /navigate</code> or <code>POST /extract</code>.</p>
<form method="post" action="/setup">
<fieldset>
<legend>Portal URL (pick one)</legend>
<p class="note" style="margin-top:0">Default is Microsoft 365 Copilot (work / M365 web). Alternative is consumer Copilot.</p>
<label class="portal-row">
<input type="radio" name="profile" value="m365_hub" {chk_m365}/>
<span><strong>Microsoft 365 Copilot</strong><br/><span class="note">https://m365.cloud.microsoft/chat/</span></span>
</label>
<label class="portal-row">
<input type="radio" name="profile" value="consumer" {chk_consumer}/>
<span><strong>Consumer Copilot</strong><br/><span class="note">https://copilot.microsoft.com/</span></span>
</label>
</fieldset>
<fieldset style="margin-top:1rem;">
<legend>Default chat mode</legend>
<p class="note" style="margin-top:0">Selects Work (enterprise M365 data) or Web (public internet) grounding before each message. Saved as <code>M365_CHAT_MODE</code> in <code>.env</code>.</p>
<label class="portal-row">
<input type="radio" name="chat_mode" value="work" {{chk_work}}/>
<span><strong>Work</strong><br/><span class="note">Email, Teams, SharePoint, OneDrive</span></span>
</label>
<label class="portal-row">
<input type="radio" name="chat_mode" value="web" {{chk_web}}/>
<span><strong>Web</strong><br/><span class="note">Public internet search results</span></span>
</label>
</fieldset>
<label for="portal_base">Optional override: COPILOT_PORTAL_BASE_URL</label>
<input type="text" id="portal_base" name="portal_base" placeholder="leave empty for default above" value="{portal_base}"/>
<label for="api_base">Optional: COPILOT_PORTAL_API_BASE_URL</label>
<input type="text" id="api_base" name="api_base" placeholder="leave empty = consumer Copilot API host" value="{api_base}"/>
<button type="submit">Save and reload API</button>
</form>
<fieldset style="margin-top:1.25rem;">
<legend>Open portal in VNC</legend>
<p class="note" style="margin-top:0">Opens the URL from your selection or override in Chromium inside this container (noVNC).</p>
<p class="note" id="navMsg" style="display:none;margin-top:.75rem;"></p>
<button type="button" id="openPortalBtn">Connect to selected portal</button>
</fieldset>
<script>
(function(){{
  function portalUrl() {{
    var o = (document.getElementById("portal_base").value || "").trim();
    if (o) {{
      if (!/^https?:\\/\\//i.test(o)) o = "https://" + o.replace(/^\\/+/,"");
      return o;
    }}
    var p = document.querySelector('input[name="profile"]:checked');
    var v = p ? p.value : "m365_hub";
    if (v === "consumer") return "https://copilot.microsoft.com/";
    return "https://m365.cloud.microsoft/chat/";
  }}
  document.getElementById("openPortalBtn").addEventListener("click", async function() {{
    var msg = document.getElementById("navMsg");
    msg.style.display = "block";
    msg.textContent = "Opening…";
    try {{
      var url = portalUrl();
      var r = await fetch("/navigate", {{
        method: "POST",
        headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
        body: new URLSearchParams({{ url: url }}).toString()
      }});
      var j = await r.json();
      if (j.status === "ok") {{
        msg.textContent = "Opened in VNC browser: " + (j.url || url);
      }} else {{
        msg.textContent = "Error: " + (j.message || JSON.stringify(j));
      }}
    }} catch (e) {{
      msg.textContent = "Error: " + e;
    }}
  }});
}})();
</script>
<p><a href="/health">health</a> · <a href="/status">status</a></p>
<script>
(function(){{
  function updateLed(data) {{
    var led = document.getElementById("session-led");
    var dot = document.getElementById("session-led-dot");
    var lbl = document.getElementById("session-led-lbl");
    if (!led) return;
    var s = data.session || "unknown";
    led.className = s;
    if (s === "active") {{
      lbl.textContent = "M365 Session: Active";
      led.title = "Session active — profile: " + (data.profile || "");
    }} else if (s === "expired") {{
      lbl.textContent = "M365 Session: Expired — sign in here";
      led.title = "Session expired: " + (data.reason || "");
    }} else {{
      lbl.textContent = "M365 Session: Unknown";
      led.title = "Could not check: " + (data.reason || "");
    }}
  }}
  function pollLed() {{
    fetch("/session-health")
      .then(function(r){{ return r.json(); }})
      .then(function(d){{ updateLed(d); }})
      .catch(function(){{ updateLed({{session:"unknown",reason:"network error"}}); }});
  }}
  pollLed();
  setInterval(pollLed, 30000);
}})();
</script>
</body></html>"""


@app.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request):
    profile, portal_base, api_base = portal_settings_from_env_file(ENV_PATH)
    _env_data = _read_env_keys(ENV_PATH, ("M365_CHAT_MODE", "COPILOT_PROVIDER"))
    chat_mode = (os.getenv("M365_CHAT_MODE") or _env_data.get("M365_CHAT_MODE") or "work").strip().lower()
    current_provider = (os.getenv("COPILOT_PROVIDER") or _env_data.get("COPILOT_PROVIDER") or "auto").strip().lower()
    expected_provider = "m365" if profile == "m365_hub" else "copilot"
    mismatch_banner = ""
    if current_provider not in ("auto", expected_provider):
        mismatch_banner = (
            f'<p class="warn">&#9888; <strong>Provider mismatch:</strong> '
            f'<code>COPILOT_PROVIDER={html.escape(current_provider)}</code> but profile '
            f'<code>{html.escape(profile)}</code> expects <code>{expected_provider}</code>. '
            f'Save settings below to fix automatically.</p>'
        )
    banner = ""
    ok = request.query_params.get("ok")
    if ok == "1":
        banner = (
            '<p class="note"><strong>Done.</strong> Settings saved and reload sent to the API.</p>'
        )
    elif ok == "0":
        w = request.query_params.get("warn", "")
        banner = (
            "<p class=\"note\"><strong>Note.</strong> Saved to <code>.env</code>; API reload may have failed: "
            f"{html.escape(w)}</p>"
        )
    return _SETUP_HTML.format(
        banner=banner,
        mismatch_banner=mismatch_banner,
        chk_m365="checked" if profile == "m365_hub" else "",
        chk_consumer="checked" if profile == "consumer" else "",
        chk_work="checked" if chat_mode != "web" else "",
        chk_web="checked" if chat_mode == "web" else "",
        portal_base=html.escape(portal_base, quote=True),
        api_base=html.escape(api_base, quote=True),
    )


@app.post("/setup")
async def setup_post(
    profile: str = Form(...),
    portal_base: str = Form(""),
    api_base: str = Form(""),
    chat_mode: str = Form("work"),
):
    invalidate_tab1_ready_state("setup_saved")
    p = (profile or "").strip().lower()
    if p not in ("consumer", "m365_hub"):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "profile must be consumer or m365_hub"},
        )
    cm = (chat_mode or "work").strip().lower()
    if cm not in ("work", "web"):
        cm = "work"
    patch_env_variable(ENV_PATH, "COPILOT_PORTAL_PROFILE", p)
    patch_env_variable(ENV_PATH, "M365_CHAT_MODE", cm)
    # Keep COPILOT_PROVIDER in sync with the profile so an explicit "copilot" value
    # in .env can never silently override an m365_hub profile (or vice-versa).
    patch_env_variable(ENV_PATH, "COPILOT_PROVIDER", "m365" if p == "m365_hub" else "copilot")
    portal_base = normalize_copilot_portal_url((portal_base or "").strip())
    api_base = normalize_copilot_portal_url((api_base or "").strip())
    patch_env_variable(ENV_PATH, "COPILOT_PORTAL_BASE_URL", portal_base)
    patch_env_variable(ENV_PATH, "COPILOT_PORTAL_API_BASE_URL", api_base)

    reload_warning = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{API1_URL}/v1/reload-config")
    except Exception as e:
        reload_warning = str(e)

    # Redirect so refresh does not re-submit; show result via query (no secrets)
    q = "ok=1" if not reload_warning else "ok=0"
    if reload_warning:
        from urllib.parse import quote

        q += "&warn=" + quote(reload_warning[:200], safe="")
    return RedirectResponse(url=f"/setup?{q}", status_code=303)


@app.get("/status")
async def status():
    try:
        context = await get_context()
        pages = len(context.pages)
        pool = _ce._page_pool
        monitor = get_pool_monitor_snapshot()
        pool_info = (
            {
                "pool_size": pool.size,
                "pool_available": pool.available,
                "pool_initialized": pool._initialized,
                "agent_tabs": len(pool.agents),
            }
            if pool is not None
            else {"pool_size": 0, "pool_available": 0, "pool_initialized": False, "agent_tabs": 0}
        )
        return {
            "status": "ok",
            "browser": "running",
            "open_pages": pages,
            **pool_info,
            "pool_phase": monitor.get("phase"),
            "pool_target": monitor.get("target_size"),
            "pool_detail": monitor.get("detail"),
        }
    except Exception as e:
        return {"status": "error", "browser": str(e)}


@app.post("/validate-auth")
async def validate_auth():
    """Validate Tab 1 authentication by sending 'Hello' and getting a real Copilot reply.

    Tab 1 = the auth/setup tab (NOT a pool tab). This confirms:
      - The M365 session cookies are valid
      - Copilot chat is responding (not just 'session active' cookie check)
      - The system is truly online and ready for agent tasks

    On success: automatically reloads all pool tabs so they inherit the validated session.
    On failure: returns validated=false with error — pool tabs are NOT reloaded.

    This is a deeper auth check than /session-health (which only reads cookies).
    Call after /extract to confirm the session is live before running agents.
    """
    try:
        result = await validate_tab1_with_hello(timeout_ms=60_000)
        pool_tabs_reloaded = 0
        pool_tabs_added = 0
        if result.get("validated"):
            pool_result = await prepare_pool_from_tab1(reload_existing=True)
            pool_tabs_reloaded = int(pool_result.get("pool_tabs_reloaded") or 0)
            pool_tabs_added = int(pool_result.get("pool_tabs_added") or 0)
            finish_tab1_auth_progress("ok")
            print(
                "[validate-auth] Pool ready after Tab 1 confirmed OK "
                f"(initialized={pool_result.get('pool_initialized')} "
                f"reloaded={pool_tabs_reloaded} added={pool_tabs_added})"
            )
        result["pool_tabs_reloaded"] = pool_tabs_reloaded
        result["pool_tabs_added"] = pool_tabs_added
        return result
    except Exception as e:
        finish_tab1_auth_progress("error", str(e))
        return JSONResponse({"validated": False, "error": str(e)}, status_code=500)


@app.post("/pool-reload")
async def pool_reload():
    """Reload all pool chat tabs (e.g. after signing in via noVNC).

    Tab 1 (auth/setup tab) is NOT reloaded — it stays for the user to interact with.
    Call this after authenticating in the browser to ensure pool tabs have fresh cookies.
    """
    try:
        pool = _ce._page_pool
        if pool is None or not pool._initialized:
            return JSONResponse({"status": "skipped", "reason": "pool not initialized"})
        reloaded = await pool.reload_all_tabs()
        return {"status": "ok", "reloaded": reloaded}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/pool-expand")
async def pool_expand(target_size: int = 6):
    """Expand pool to target_size tabs without closing existing ones.

    Safe to call while agents are running — only adds new free tabs.
    Does NOT shrink the pool if already larger than target_size.
    Called by C9 before bursty parallel runs to pre-warm extra tabs.
    """
    try:
        context = await get_context()
        ready = await ensure_tab1_ready_for_pool(timeout_ms=60_000)
        if not ready.get("validated"):
            return JSONResponse(
                {
                    "status": "blocked",
                    "message": ready.get("error") or "Tab 1 is not authenticated yet",
                    "tab1_url": ready.get("tab1_url"),
                },
                status_code=409,
            )
        pool_result = await prepare_pool_from_tab1(
            context=context,
            reload_existing=False,
            target_size=target_size,
            source="pool-expand",
        )
        pool = _ce._page_pool
        if pool is None:
            finish_tab1_auth_progress("error", "pool unavailable after Tab 1 validation")
            return JSONResponse({"status": "error", "message": "pool unavailable after Tab 1 validation"}, status_code=500)
        finish_tab1_auth_progress("ok")
        return {
            "status": "ok",
            "added": int(pool_result.get("pool_tabs_added") or 0),
            "target": target_size,
            "pool_size": pool.size,
            "pool_available": pool.available,
            "pool_initialized": pool._initialized,
        }
    except Exception as e:
        finish_tab1_auth_progress("error", str(e))
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/pool-reset")
async def pool_reset():
    """Re-initialize the PagePool back to the default C3_CHAT_TAB_POOL_SIZE tabs.

    Always resets to the base pool size from env — discards any previous expansion.
    Closes all orphaned pool pages from previous expansions to free Chromium memory.
    Use /pool-expand to grow the pool for parallel runs after reset.
    """
    try:
        context = await get_context()
        ready = await ensure_tab1_ready_for_pool(timeout_ms=60_000)
        if not ready.get("validated"):
            return JSONResponse(
                {
                    "status": "blocked",
                    "message": ready.get("error") or "Tab 1 is not authenticated yet",
                    "tab1_url": ready.get("tab1_url"),
                },
                status_code=409,
            )
        # Close all pages currently tracked in _pool_pages (includes any tabs
        # added by a previous /pool-expand) before creating the new pool.
        # This prevents Chromium from accumulating orphaned tabs across resets.
        orphaned = list(_ce._pool_pages)
        if orphaned:
            print(f"[pool-reset] Closing {len(orphaned)} orphaned pool page(s)…")
            for _p in orphaned:
                try:
                    if not _p.is_closed():
                        await _p.close()
                except Exception:
                    pass
            _ce._pool_pages.clear()

        # Always create a fresh PagePool at the default size so /pool-reset
        # correctly collapses a previously expanded pool back to the base size.
        pool_size = max(1, int(os.getenv("C3_CHAT_TAB_POOL_SIZE", "4")))
        _ce._page_pool = _ce.PagePool(pool_size)
        pool = _ce._page_pool
        await pool.reinitialize(context)
        return {
            "status": "ok",
            "pool_size": pool.size,
            "pool_available": pool.available,
            "pool_initialized": pool._initialized,
        }
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/extract")
async def extract():
    """
    Trigger cookie extraction from the headless browser session.
    Visits depend on COPILOT_PORTAL_PROFILE in the mounted .env:
      consumer -> copilot.microsoft.com, then bing.com
      m365_hub -> m365.cloud.microsoft, m365.cloud.microsoft.com, bing.com,
                  then copilot.microsoft.com (merged cookie string for C1 Phase A WSS)
    If the user is not logged in, authenticate via noVNC at :6080, then call again.
    On success, signals C1 (API1_URL) to POST /v1/reload-config.
    """
    result = await extract_and_save(ENV_PATH)

    if result["status"] == "ok":
        # Signal Container 1 to reload config + reset pool
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{API1_URL}/v1/reload-config")
        except Exception as e:
            result["reload_warning"] = f"Could not signal Container 1: {e}"

        # Validate Tab 1 auth by sending 'Hello' and waiting for a real Copilot reply.
        # Tab 1 = the non-pool auth tab (opened by warm_browser_for_novnc at /setup).
        # Only reload pool tabs if Tab 1 confirms the session is truly live.
        # This is deeper than a cookie check — it proves Copilot is responding.
        try:
            tab1_check = await validate_tab1_with_hello(timeout_ms=60_000)
            result["tab1_validated"] = tab1_check.get("validated", False)
            result["tab1_reply_preview"] = tab1_check.get("reply")
            result["tab1_elapsed_ms"] = tab1_check.get("elapsed_ms")
            if tab1_check.get("error"):
                result["tab1_warning"] = tab1_check["error"]

            if tab1_check.get("validated"):
                pool_result = await prepare_pool_from_tab1(reload_existing=True)
                result["pool_tabs_reloaded"] = int(pool_result.get("pool_tabs_reloaded") or 0)
                result["pool_tabs_added"] = int(pool_result.get("pool_tabs_added") or 0)
                result["pool_initialized"] = bool(pool_result.get("pool_initialized"))
                finish_tab1_auth_progress("ok")
                print(
                    "[extract] Tab 1 validated OK — "
                    f"pool initialized={pool_result.get('pool_initialized')} "
                    f"reloaded={result['pool_tabs_reloaded']} "
                    f"added={result['pool_tabs_added']}"
                )
            else:
                result["pool_tabs_reloaded"] = 0
                result["pool_tabs_added"] = 0
                result["pool_reload_skipped"] = (
                    "Tab 1 Hello validation failed — pool tabs NOT reloaded. "
                    "Check auth via noVNC at :6080."
                )
                print(f"[extract] Tab 1 validation FAILED: {tab1_check.get('error')} — pool tabs NOT reloaded")
        except Exception as e:
            result["tab1_warning"] = f"Tab 1 validation error: {e}"
            result["pool_tabs_reloaded"] = 0
            result["pool_tabs_added"] = 0
            result["pool_reload_skipped"] = "Tab 1 validation errored — pool tabs NOT created or reloaded"
            finish_tab1_auth_progress("error", str(e))

    return JSONResponse(content=result)


@app.post("/navigate")
async def navigate(request: Request):
    """Force the browser to navigate to a URL (form field, query ?url=, or JSON body).

    Accepts:
      - Form: url=...
      - Query: /navigate?url=...
      - JSON:  {"url": "..."}
    Supports both copilot.microsoft.com and m365.cloud.microsoft portals.
    """
    u = request.query_params.get("url")
    if not u:
        content_type = (request.headers.get("content-type") or "").lower()
        try:
            if "json" in content_type:
                body = await request.json()
                u = body.get("url", "")
            else:
                form = await request.form()
                u = form.get("url", "")
        except Exception:
            pass
    u = (u or "").strip() or "https://copilot.microsoft.com"
    u = normalize_copilot_portal_url(u)
    invalidate_tab1_ready_state("manual_navigation")
    try:
        context = await get_context()
        if not context.pages:
            page = await context.new_page()
        else:
            page = context.pages[0]
        await page.goto(u, wait_until="domcontentloaded", timeout=30000)
        return {"status": "ok", "url": page.url}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/macro")
async def r_macro(request: Request):
    """Execute a predefined automation macro in the browser.

    Actions:
      auto-login-m365      — navigate Tab 1 to M365 Copilot login page
      auto-login-consumer  — navigate Tab 1 to consumer Copilot login page
      clear-cache          — clear browser cookies + cache via CDP
      screenshot           — capture current VNC frame as base64 PNG
      pool-reload          — reload all pool tabs after manual login
    """
    import base64
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

    action = (body.get("action") or "").strip()
    try:
        context = await get_context()
        page = context.pages[0] if context.pages else await context.new_page()

        if action == "auto-login-m365":
            invalidate_tab1_ready_state("macro_autologin_m365")
            await page.goto("https://m365.cloud.microsoft/chat/",
                            wait_until="domcontentloaded", timeout=30_000)
            return {"status": "ok", "message": "Navigated to M365 Copilot — sign in via noVNC then click Extract Cookies"}

        elif action == "auto-login-consumer":
            invalidate_tab1_ready_state("macro_autologin_consumer")
            await page.goto("https://copilot.microsoft.com/",
                            wait_until="domcontentloaded", timeout=30_000)
            return {"status": "ok", "message": "Navigated to Consumer Copilot — sign in via noVNC then click Extract Cookies"}

        elif action == "clear-cache":
            # Clear cookies via Playwright context + CDP cache clear
            await context.clear_cookies()
            try:
                cdp = await context.new_cdp_session(page)
                await cdp.send("Network.clearBrowserCache")
                await cdp.detach()
            except Exception:
                pass
            invalidate_tab1_ready_state("macro_clear_cache")
            return {"status": "ok", "message": "Browser cookies and cache cleared — sign in again via noVNC"}

        elif action == "screenshot":
            # Capture current page screenshot as base64 PNG
            png_bytes = await page.screenshot(type="png", full_page=False)
            b64 = base64.b64encode(png_bytes).decode()
            return {"status": "ok", "image": f"data:image/png;base64,{b64}",
                    "message": f"Screenshot captured ({len(png_bytes)} bytes)"}

        elif action == "pool-reload":
            # Reload pool tabs after manual login in noVNC
            pool = _ce._page_pool
            if pool is None or not pool._initialized:
                return JSONResponse({"status": "skipped", "reason": "pool not initialized — call /extract first"})
            reloaded = await pool.reload_all_tabs()
            return {"status": "ok", "message": f"Reloaded {reloaded} pool tab(s)"}

        else:
            return JSONResponse(
                {"status": "error", "message": f"Unknown action '{action}'. Valid: auto-login-m365, auto-login-consumer, clear-cache, screenshot, pool-reload"},
                status_code=400,
            )
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/clipboard/push")
async def clipboard_push(request: Request):
    """Push text from the host browser into the VNC session clipboard via xclip.

    Body: {"text": "..."}
    Uses xclip to write to the X11 CLIPBOARD selection inside the container.
    x11vnc -clip both then forwards it as an RFB cut-text event to all VNC clients.
    """
    import subprocess
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)
    text = body.get("text", "")
    if not isinstance(text, str):
        return JSONResponse({"status": "error", "message": "text must be a string"}, status_code=400)
    try:
        display = os.getenv("DISPLAY", ":99")
        env = {**os.environ, "DISPLAY": display}
        result = subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=5,
            env=env,
        )
        if result.returncode != 0:
            return JSONResponse(
                {"status": "error", "message": result.stderr.decode(errors="replace")},
                status_code=500,
            )
        return {"status": "ok", "bytes_written": len(text.encode("utf-8"))}
    except FileNotFoundError:
        return JSONResponse({"status": "error", "message": "xclip not found in container"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/clipboard/pull")
async def clipboard_pull():
    """Pull the current VNC session clipboard text via xclip.

    Reads the X11 CLIPBOARD selection from inside the container.
    Returns: {"status": "ok", "text": "..."}
    """
    import subprocess
    try:
        display = os.getenv("DISPLAY", ":99")
        env = {**os.environ, "DISPLAY": display}
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True,
            timeout=5,
            env=env,
        )
        if result.returncode != 0:
            # Empty clipboard returns exit code 1 on some xclip versions — treat as empty
            return {"status": "ok", "text": ""}
        text = result.stdout.decode("utf-8", errors="replace")
        return {"status": "ok", "text": text}
    except FileNotFoundError:
        return JSONResponse({"status": "error", "message": "xclip not found in container"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)



@app.post("/trace/m365-bootstrap")
async def trace_m365_bootstrap():
    """
    Capture real in-browser request/response metadata for M365 conversations bootstrap.
    This is a diagnostic endpoint for Phase B (no secrets returned).
    """
    target = "https://m365.cloud.microsoft/c/api/conversations"
    context = await get_context()
    page = await context.new_page()

    captured: dict = {
        "request": None,
        "response": None,
        "fetch_result": None,
    }

    def _on_request(req):
        if "/c/api/conversations" not in req.url:
            return
        headers = req.headers or {}
        captured["request"] = {
            "url": req.url,
            "method": req.method,
            "headers_subset": {
                "origin": headers.get("origin"),
                "referer": headers.get("referer"),
                "authorization_present": bool(headers.get("authorization")),
                "cookie_present": bool(headers.get("cookie")),
                "x_ms_client_request_id": headers.get("x-ms-client-request-id"),
            },
        }

    async def _on_response(resp):
        if "/c/api/conversations" not in resp.url:
            return
        headers = await resp.all_headers()
        captured["response"] = {
            "url": resp.url,
            "status": resp.status,
            "headers_subset": {
                "location": headers.get("location"),
                "www-authenticate": headers.get("www-authenticate"),
                "access-control-allow-origin": headers.get("access-control-allow-origin"),
            },
        }

    page.on("request", _on_request)
    page.on("response", lambda r: asyncio.create_task(_on_response(r)))
    try:
        await page.goto("https://m365.cloud.microsoft/chat/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)
        if "m365.cloud.microsoft" not in (page.url or ""):
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Trace page is not on m365.cloud.microsoft; complete interactive sign-in in noVNC first.",
                    "current_url": page.url,
                }
            )

        # Execute same-origin fetch in the authenticated browser context.
        res = await page.evaluate(
            """async (url) => {
                try {
                    const r = await fetch(url, {
                        method: "GET",
                        credentials: "include",
                        redirect: "manual",
                    });
                    const hdr = {};
                    for (const [k, v] of r.headers.entries()) {
                        if (["location", "www-authenticate", "access-control-allow-origin"].includes(k.toLowerCase())) {
                            hdr[k.toLowerCase()] = v;
                        }
                    }
                    return {
                        ok: r.ok,
                        status: r.status,
                        type: r.type,
                        redirected: r.redirected,
                        url: r.url,
                        headers_subset: hdr,
                        body_preview: (await r.text()).slice(0, 300),
                    };
                } catch (e) {
                    return { error: String(e) };
                }
            }""",
            target,
        )
        captured["fetch_result"] = res
        return JSONResponse(
            content={
                "status": "ok",
                "target": target,
                "trace": captured,
            }
        )
    finally:
        try:
            page.remove_listener("request", _on_request)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass


@app.post("/trace/m365-traffic")
async def trace_m365_traffic(request: Request):
    """
    Capture live M365 XHR/fetch/websocket request metadata for a short window.
    Use while interacting in noVNC to discover the real chat bootstrap path.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    duration = int(body.get("duration_seconds", 15))
    duration = max(5, min(duration, 60))

    context = await get_context()
    page = await context.new_page()
    events: list[dict] = []

    def _capture(req):
        rtype = req.resource_type or ""
        if rtype not in ("xhr", "fetch", "websocket"):
            return
        h = req.headers or {}
        events.append(
            {
                "type": rtype,
                "method": req.method,
                "url": req.url,
                "authorization_present": bool(h.get("authorization")),
                "x_ms_client_request_id": h.get("x-ms-client-request-id"),
                "content_type": h.get("content-type"),
                "referer": h.get("referer"),
            }
        )

    page.on("request", _capture)
    try:
        await page.goto("https://m365.cloud.microsoft/chat/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(duration)
        # De-duplicate by method+url+type
        uniq = {}
        for e in events:
            key = (e["type"], e["method"], e["url"])
            uniq[key] = e
        interesting = list(uniq.values())
        interesting.sort(key=lambda x: x["url"])
        return JSONResponse(
            content={
                "status": "ok",
                "duration_seconds": duration,
                "current_url": page.url,
                "captured_count": len(events),
                "unique_count": len(interesting),
                "traffic": interesting[:200],
            }
        )
    finally:
        try:
            page.remove_listener("request", _capture)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass


@app.post("/trace/m365-traffic-live")
async def trace_m365_traffic_live(request: Request):
    """
    Capture traffic across ALL existing browser pages for a short window.
    Use this while interacting manually in noVNC to capture actual send-message calls.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    duration = int(body.get("duration_seconds", 20))
    duration = max(5, min(duration, 90))

    context = await get_context()
    events: list[dict] = []

    def _capture(req):
        rtype = req.resource_type or ""
        if rtype not in ("xhr", "fetch", "websocket"):
            return
        h = req.headers or {}
        events.append(
            {
                "type": rtype,
                "method": req.method,
                "url": req.url,
                "authorization_present": bool(h.get("authorization")),
                "x_ms_client_request_id": h.get("x-ms-client-request-id"),
                "content_type": h.get("content-type"),
                "referer": h.get("referer"),
            }
        )

    context.on("request", _capture)
    try:
        await asyncio.sleep(duration)
        uniq = {}
        for e in events:
            key = (e["type"], e["method"], e["url"])
            uniq[key] = e
        interesting = list(uniq.values())
        interesting.sort(key=lambda x: x["url"])
        return JSONResponse(
            content={
                "status": "ok",
                "duration_seconds": duration,
                "captured_count": len(events),
                "unique_count": len(interesting),
                "traffic": interesting[:300],
            }
        )
    finally:
        try:
            context.remove_listener("request", _capture)
        except Exception:
            pass
