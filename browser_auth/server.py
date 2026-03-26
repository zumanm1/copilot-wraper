"""
browser_auth/server.py
=======================
FastAPI server for Container 3.

Endpoints:
  GET  /health         — liveness check
  GET  /status         — browser + cookie status
  GET  /setup          — HTML form: portal profile + optional URL overrides
  POST /setup          — persist settings to mounted .env, reload C1 config
  POST /extract        — trigger cookie extraction (blocks until done)
  POST /navigate       — navigate browser to a URL (for manual login flows)
"""
from __future__ import annotations
import asyncio
import html
import json
import httpx
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from portal_urls import normalize_copilot_portal_url

from cookie_extractor import (
    browser_chat,
    check_session_health,
    extract_and_save,
    extract_access_token,
    get_context,
    patch_env_variable,
    portal_settings_from_env_file,
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

    async def _pre_init_page_pool() -> None:
        """Pre-create chat tabs in the background so they're ready for requests."""
        await asyncio.sleep(8)
        try:
            pool_size = max(1, int(os.getenv("C3_CHAT_TAB_POOL_SIZE", "10")))
            context = await get_context()
            if _ce._page_pool is None:
                _ce._page_pool = _ce.PagePool(pool_size)
            await _ce._page_pool.initialize(context)
            print(f"[browser-auth] PagePool pre-initialized ({pool_size} tabs)")
        except Exception as e:
            print(f"[browser-auth] PagePool pre-init skipped: {e}")

    if not skip_warm:
        asyncio.create_task(_delayed_warm_novnc())
    asyncio.create_task(_pre_init_page_pool())
    yield


app = FastAPI(
    title="Browser Auth — Cookie Extractor",
    version="1.0.0",
    lifespan=lifespan,
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
        status_code = 200
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:
        import datetime
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return JSONResponse(
            {"session": "unknown", "profile": "unknown", "reason": str(exc), "checked_at": now},
            status_code=503,
        )


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

    Body: {"prompt": "...", "mode": "chat|smart|reasoning", "timeout": 30000}
    Returns: {"success": bool, "text": "...", "events": [...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    mode = body.get("mode", "chat")
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
        chk_m365="checked" if profile == "m365_hub" else "",
        chk_consumer="checked" if profile == "consumer" else "",
        portal_base=html.escape(portal_base, quote=True),
        api_base=html.escape(api_base, quote=True),
    )


@app.post("/setup")
async def setup_post(
    profile: str = Form(...),
    portal_base: str = Form(""),
    api_base: str = Form(""),
):
    p = (profile or "").strip().lower()
    if p not in ("consumer", "m365_hub"):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "profile must be consumer or m365_hub"},
        )
    patch_env_variable(ENV_PATH, "COPILOT_PORTAL_PROFILE", p)
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
        pool_info = (
            {
                "pool_size": pool.size,
                "pool_available": pool.available,
                "pool_initialized": pool._initialized,
            }
            if pool is not None
            else {"pool_size": 0, "pool_available": 0, "pool_initialized": False}
        )
        return {"status": "ok", "browser": "running", "open_pages": pages, **pool_info}
    except Exception as e:
        return {"status": "error", "browser": str(e)}


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
