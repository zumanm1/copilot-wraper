#!/usr/bin/env python3
"""Test: Use Playwright CDP to execute a Copilot chat via the REAL browser.
The browser's TLS fingerprint + session bypasses the null-method challenge."""
import asyncio
import json
import uuid

async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # Connect to C3's browser via CDP
        # C3 exposes Chromium on the default CDP port
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        # Navigate to copilot.microsoft.com if needed
        if "copilot.microsoft.com" not in (page.url or ""):
            await page.goto("https://copilot.microsoft.com", wait_until="domcontentloaded")
            await asyncio.sleep(2)

        print(f"Page URL: {page.url}")

        # Execute WebSocket chat directly in the browser context
        result = await page.evaluate("""
            async () => {
                try {
                    const sid = crypto.randomUUID();
                    const wsUrl = `wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId=${sid}`;

                    // Create conversation
                    const convResp = await fetch('https://copilot.microsoft.com/c/api/conversations', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({}),
                        credentials: 'include'
                    });
                    if (!convResp.ok) return {error: `Conv create failed: ${convResp.status}`};
                    const convData = await convResp.json();
                    const convId = convData.id;

                    // Connect WebSocket
                    return new Promise((resolve) => {
                        const ws = new WebSocket(wsUrl);
                        const events = [];
                        let fullText = '';
                        let timeout;

                        ws.onopen = () => {
                            // Send message immediately after open
                        };

                        ws.onmessage = (e) => {
                            const msg = JSON.parse(e.data);
                            const ev = msg.event || '';
                            events.push(ev);

                            if (ev === 'connected') {
                                // Send the message
                                ws.send(JSON.stringify({
                                    event: 'send',
                                    conversationId: convId,
                                    content: [{type: 'text', text: 'say hello briefly in one sentence'}],
                                    mode: 'chat'
                                }));
                            } else if (ev === 'appendText') {
                                fullText += msg.text || '';
                            } else if (ev === 'challenge') {
                                // Let the browser handle it naturally
                                events.push('challenge_data:' + JSON.stringify(msg));
                            } else if (ev === 'done' || ev === 'error') {
                                clearTimeout(timeout);
                                ws.close();
                                resolve({
                                    success: ev !== 'error' && fullText.length > 0,
                                    events: events,
                                    text: fullText.substring(0, 500),
                                    error: ev === 'error' ? msg : null,
                                    convId: convId
                                });
                            }
                        };

                        ws.onerror = (e) => {
                            resolve({error: 'WebSocket error', events: events});
                        };

                        timeout = setTimeout(() => {
                            ws.close();
                            resolve({error: 'Timeout', events: events, text: fullText});
                        }, 30000);
                    });
                } catch(e) {
                    return {error: e.message};
                }
            }
        """)

        print(f"Result: {json.dumps(result, indent=2)}")

asyncio.run(main())
