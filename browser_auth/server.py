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
import httpx
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from portal_urls import normalize_copilot_portal_url

from cookie_extractor import (
    extract_and_save,
    get_context,
    patch_env_variable,
    portal_settings_from_env_file,
    warm_browser_for_novnc,
)


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

ENV_PATH = os.getenv("ENV_PATH", "/app/.env")
API1_URL = os.getenv("API1_URL", "http://app:8000")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "browser-auth"}


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
</style></head><body>
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
<p class="note" id="navMsg" style="display:none;margin-top:.75rem;"></p>
<button type="button" id="openPortalBtn">Open selected portal in VNC browser</button>
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
        return {"status": "ok", "browser": "running", "open_pages": pages}
    except Exception as e:
        return {"status": "error", "browser": str(e)}


@app.post("/extract")
async def extract():
    """
    Trigger cookie extraction from the headless browser session.
    The browser navigates to copilot.microsoft.com.
    If the user is not logged in, they must authenticate via the noVNC UI
    at http://localhost:6080 within 5 minutes.
    After extraction, signals Container 1 to hot-reload.
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
async def navigate(request: Request, url: str | None = Form(default=None)):
    """Force the browser to navigate to a URL (form field or query ?url= for curl)."""
    u = (url or request.query_params.get("url") or "").strip() or "https://copilot.microsoft.com"
    u = normalize_copilot_portal_url(u)
    try:
        context = await get_context()
        if not context.pages:
            page = await context.new_page()
        else:
            page = context.pages[0]
        await page.goto(u, wait_until="domcontentloaded")
        return {"status": "ok", "url": page.url}
    except Exception as e:
        return {"status": "error", "message": str(e)}
