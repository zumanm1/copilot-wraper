#!/usr/bin/env python3
"""Inspect Copilot web UI DOM to find textarea, submit button, and response selectors."""
import asyncio
import json
import sys
sys.path.insert(0, "/browser-auth")

from cookie_extractor import _get_context, _get_or_create_page

async def main():
    ctx = await _get_context()
    page = await _get_or_create_page(ctx)
    url = page.url
    print(f"Current URL: {url}")

    if "copilot.microsoft.com" not in url:
        await page.goto("https://copilot.microsoft.com", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)
        print(f"Navigated to: {page.url}")

    selectors = await page.evaluate("""() => {
        const r = [];
        document.querySelectorAll("textarea").forEach(el => {
            r.push({tag:"textarea", id:el.id, ph:el.placeholder.substring(0,80), cls:el.className.substring(0,80), testId:el.getAttribute("data-testid")});
        });
        document.querySelectorAll("[contenteditable]").forEach(el => {
            r.push({tag:el.tagName, ce:true, id:el.id, cls:el.className.substring(0,80), testId:el.getAttribute("data-testid")});
        });
        document.querySelectorAll("button").forEach(el => {
            const tid = el.getAttribute("data-testid") || "";
            const al = el.getAttribute("aria-label") || "";
            const txt = el.textContent.substring(0,40);
            if (tid || al.toLowerCase().includes("send") || al.toLowerCase().includes("submit") || txt.toLowerCase().includes("send")) {
                r.push({tag:"button", testId:tid, ariaLabel:al, text:txt, cls:el.className.substring(0,60)});
            }
        });
        document.querySelectorAll("[data-testid*='submit'], [data-testid*='send'], [aria-label*='Send'], [aria-label*='submit']").forEach(el => {
            r.push({tag:el.tagName, testId:el.getAttribute("data-testid"), ariaLabel:el.getAttribute("aria-label"), cls:el.className.substring(0,60)});
        });
        // Check for response/message containers
        document.querySelectorAll("[data-testid*='message'], [data-testid*='response'], [data-testid*='answer'], [class*='response'], [class*='message-content']").forEach(el => {
            r.push({tag:el.tagName, testId:el.getAttribute("data-testid"), cls:el.className.substring(0,80), textLen:el.textContent.length});
        });
        return r;
    }""")

    print(f"Found {len(selectors)} elements:")
    for s in selectors:
        print(f"  {json.dumps(s)}")

    title = await page.title()
    print(f"Title: {title}")

asyncio.run(main())
