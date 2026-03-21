"""
browser_auth/server.py
=======================
FastAPI server for Container 3.

Endpoints:
  GET  /health         — liveness check
  GET  /status         — browser + cookie status
  POST /extract        — trigger cookie extraction (blocks until done)
  POST /navigate       — navigate browser to a URL (for manual login flows)
"""
from __future__ import annotations
import asyncio
import httpx
import os
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse

from cookie_extractor import extract_and_save, get_context

app = FastAPI(title="Browser Auth — Cookie Extractor", version="1.0.0")

ENV_PATH = os.getenv("ENV_PATH", "/app/.env")
API1_URL = os.getenv("API1_URL", "http://app:8000")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "browser-auth"}


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
async def navigate(url: str = "https://copilot.microsoft.com"):
    """Force the browser to navigate to a URL."""
    try:
        context = await get_context()
        if not context.pages:
            page = await context.new_page()
        else:
            page = context.pages[0]
        await page.goto(url, wait_until="domcontentloaded")
        return {"status": "ok", "url": page.url}
    except Exception as e:
        return {"status": "error", "message": str(e)}
