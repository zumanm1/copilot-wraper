#!/usr/bin/env python3
"""Test WebSocket with curl_cffi TLS impersonation (gpt4free approach)."""
import asyncio
import json
import uuid
from pathlib import Path
from urllib.parse import quote

from curl_cffi.requests import AsyncSession
from curl_cffi import CurlWsFlag

env_path = Path(__file__).resolve().parent.parent / ".env"
cookies = {}
cookie_str = ""
for line in env_path.read_text().splitlines():
    if line.startswith("COPILOT_COOKIES=") or line.startswith("BING_COOKIES="):
        cookie_str = line.split("=", 1)[1].strip()
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                cookies[k.strip()] = v.strip()
        break

print(f"Cookies: {len(cookies)} keys: {list(cookies.keys())[:10]}")

async def main():
    sid = str(uuid.uuid4())
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}"

    headers = {
        "origin": "https://copilot.microsoft.com",
        "referer": "https://copilot.microsoft.com/",
    }

    start_payload = {
        "timeZone": "America/Los_Angeles",
        "startNewConversation": True,
        "teenSupportEnabled": True,
        "correctPersonalizationSetting": True,
        "performUserMerge": True,
        "deferredDataUseCapable": True,
    }

    async with AsyncSession(
        timeout=30,
        impersonate="chrome",
        headers=headers,
        cookies=cookies,
    ) as session:
        # /c/api/start
        print("POSTing /c/api/start ...")
        resp = await session.post(
            "https://copilot.microsoft.com/c/api/start",
            json=start_payload,
            headers={"content-type": "application/json", **headers},
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Body: {resp.text[:300]}")
            return
        data = resp.json()
        conv_id = data.get("currentConversationId")
        new_cookies = {k: v for k, v in resp.cookies.items()}
        print(f"Conv: {conv_id}")
        print(f"Response keys: {list(data.keys())}")
        if new_cookies:
            print(f"New cookies: {list(new_cookies.keys())}")
            cookies.update(new_cookies)

        # WebSocket
        print(f"\nConnecting WS: {ws_url[:80]}...")
        wss = await session.ws_connect(ws_url, timeout=3)

        # Send message immediately (gpt4free style — no challenge handling)
        send_payload = {
            "event": "send",
            "conversationId": conv_id,
            "content": [{"type": "text", "text": "say hi briefly"}],
            "mode": "chat",
        }
        await wss.send(json.dumps(send_payload).encode(), CurlWsFlag.TEXT)
        print("Message sent")

        events = []
        full_text = []
        while not wss.closed:
            try:
                msg_bytes, _ = await asyncio.wait_for(wss.recv(), timeout=15)
                msg = json.loads(msg_bytes)
            except Exception:
                break
            ev = msg.get("event", "")
            events.append(ev)
            if ev == "appendText":
                text = msg.get("text", "")
                full_text.append(text)
                print(f"  RECV: appendText '{text[:80]}'")
            elif ev == "challenge":
                print(f"  RECV: challenge {json.dumps(msg)[:200]}")
            elif ev == "error":
                print(f"  RECV: error {json.dumps(msg)[:200]}")
                break
            elif ev in ("done", "partCompleted"):
                print(f"  RECV: {ev}")
                break
            else:
                print(f"  RECV: {ev} {json.dumps(msg)[:120]}")

        success = "appendText" in events
        print(f"\nEvents: {events}")
        print(f"Full text: {''.join(full_text)[:200]}")
        print(f"SUCCESS: {success}")

asyncio.run(main())
