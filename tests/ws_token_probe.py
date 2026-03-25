#!/usr/bin/env python3
"""Test: accessToken in WS URL with curl_cffi (no Auth header on /start)."""
import asyncio
import json
import uuid
from pathlib import Path
from urllib.parse import quote

from curl_cffi.requests import AsyncSession
from curl_cffi import CurlWsFlag

env_path = Path(__file__).resolve().parent.parent / ".env"
cookies = {}
for line in env_path.read_text().splitlines():
    for prefix in ("COPILOT_COOKIES=", "BING_COOKIES="):
        if line.startswith(prefix):
            for pair in line.split("=", 1)[1].strip().split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    cookies[k.strip()] = v.strip()

# Extract __Host-copilot-anon token
anon_token = cookies.get("__Host-copilot-anon", "")
print(f"Cookies: {len(cookies)}, anon_token length: {len(anon_token)}")

async def test(label, use_token_in_url, use_start_endpoint):
    sid = str(uuid.uuid4())
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}"
    if use_token_in_url and anon_token:
        ws_url += f"&accessToken={quote(anon_token)}"

    headers = {
        "origin": "https://copilot.microsoft.com",
        "referer": "https://copilot.microsoft.com/",
    }

    async with AsyncSession(timeout=30, impersonate="chrome", headers=headers, cookies=cookies) as session:
        if use_start_endpoint:
            resp = await session.post(
                "https://copilot.microsoft.com/c/api/start",
                json={
                    "timeZone": "America/Los_Angeles",
                    "startNewConversation": True,
                    "teenSupportEnabled": True,
                    "correctPersonalizationSetting": True,
                    "performUserMerge": True,
                    "deferredDataUseCapable": True,
                },
                headers={"content-type": "application/json", **headers},
            )
            if resp.status_code != 200:
                print(f"  [{label}] /start failed: {resp.status_code} {resp.text[:200]}")
                return False
            conv_id = resp.json().get("currentConversationId")
            cookies.update({k: v for k, v in resp.cookies.items()})
        else:
            resp = await session.post(
                "https://copilot.microsoft.com/c/api/conversations",
                json={},
                headers={"content-type": "application/json", **headers},
            )
            if resp.status_code != 200:
                print(f"  [{label}] /conversations failed: {resp.status_code}")
                return False
            conv_id = resp.json().get("id")

        print(f"  [{label}] Conv: {conv_id}")
        wss = await session.ws_connect(ws_url, timeout=3)

        # Send immediately
        await wss.send(json.dumps({
            "event": "send",
            "conversationId": conv_id,
            "content": [{"type": "text", "text": "say hi"}],
            "mode": "chat",
        }).encode(), CurlWsFlag.TEXT)
        print(f"  [{label}] Message sent")

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
                full_text.append(msg.get("text", ""))
                print(f"  [{label}] RECV: appendText '{msg.get('text', '')[:80]}'")
            elif ev in ("done", "error", "partCompleted"):
                print(f"  [{label}] RECV: {ev} {json.dumps(msg)[:150]}")
                break
            elif ev == "challenge":
                print(f"  [{label}] RECV: challenge {json.dumps(msg)[:200]}")
            else:
                print(f"  [{label}] RECV: {ev}")

        success = "appendText" in events
        print(f"  [{label}] Events: {events}")
        if full_text:
            print(f"  [{label}] Text: {''.join(full_text)[:200]}")
        print(f"  [{label}] SUCCESS: {success}")
        return success

async def main():
    tests = [
        ("A_start_with_token", True, True),
        ("B_start_no_token", False, True),
        ("C_conv_with_token", True, False),
        ("D_conv_no_token", False, False),
    ]
    for label, use_token, use_start in tests:
        print(f"\n=== {label} ===")
        try:
            await test(label, use_token, use_start)
        except Exception as e:
            print(f"  [{label}] ERROR: {e}")
        await asyncio.sleep(1)

asyncio.run(main())
