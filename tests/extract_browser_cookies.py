#!/usr/bin/env python3
"""Extract cookies from C3 browser for all relevant domains via /extract endpoint,
and also dump raw browser cookies per-domain for debugging."""
import asyncio
import sys
sys.path.insert(0, "/browser-auth")

from cookie_extractor import _get_context, _get_or_create_page

DOMAINS = [
    "https://copilot.microsoft.com",
    "https://www.bing.com",
    "https://bing.com",
    "https://m365.cloud.microsoft.com",
    "https://login.microsoftonline.com",
    "https://login.live.com",
]

async def main():
    ctx = await _get_context()
    page = await _get_or_create_page(ctx)
    print(f"Current page URL: {page.url}")
    print(f"Pages open: {len(ctx.pages)}")
    for i, p in enumerate(ctx.pages):
        print(f"  Page {i}: {p.url}")
    print()

    for domain in DOMAINS:
        cookies = await ctx.cookies(domain)
        names = [c["name"] for c in cookies]
        print(f"{domain}: {len(cookies)} cookies")
        for c in cookies:
            val_preview = c["value"][:40] + "..." if len(c["value"]) > 40 else c["value"]
            print(f"  {c['name']}={val_preview}  (domain={c.get('domain','?')}, httpOnly={c.get('httpOnly',False)})")
        print()

    # Check if user appears signed in on copilot.microsoft.com
    print("--- Sign-in check ---")
    title = await page.title()
    print(f"Page title: {title}")
    print(f"Page URL: {page.url}")

    # Check localStorage for access tokens
    if "copilot.microsoft.com" in page.url:
        ls_count = await page.evaluate("() => localStorage.length")
        print(f"localStorage items: {ls_count}")
        ls_keys = await page.evaluate("() => { const k=[]; for(let i=0;i<localStorage.length;i++) k.push(localStorage.key(i)); return k; }")
        for key in ls_keys:
            print(f"  localStorage key: {key}")

asyncio.run(main())
