#!/usr/bin/env python3
"""
Minimal WebSocket probe to test different challenge response formats
against copilot.microsoft.com to find the correct one.
"""
import asyncio
import json
import uuid
import sys
import os
from pathlib import Path

# Read cookies from .env
env_path = Path(__file__).resolve().parent.parent / ".env"
cookies = ""
for line in env_path.read_text().splitlines():
    if line.startswith("COPILOT_COOKIES="):
        cookies = line.split("=", 1)[1].strip()
        break
if not cookies:
    for line in env_path.read_text().splitlines():
        if line.startswith("BING_COOKIES="):
            cookies = line.split("=", 1)[1].strip()
            break

if not cookies:
    print("No cookies found in .env")
    sys.exit(1)

print(f"Cookie length: {len(cookies)}")

async def test_challenge_response(response_format_name, make_response):
    """Test a specific challenge response format."""
    import aiohttp

    sid = str(uuid.uuid4())
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}"

    headers = {
        "Origin": "https://copilot.microsoft.com",
        "Referer": "https://copilot.microsoft.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookies,
    }

    # Create conversation
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://copilot.microsoft.com/c/api/conversations",
            json={},
            headers=headers,
        ) as resp:
            if resp.status != 200:
                print(f"  [{response_format_name}] Conv create failed: HTTP {resp.status}")
                return
            data = await resp.json()
            conv_id = data["id"]
            print(f"  [{response_format_name}] Conv: {conv_id}")

        ws_headers = {
            "Origin": "https://copilot.microsoft.com",
            "Referer": "https://copilot.microsoft.com/",
            "User-Agent": headers["User-Agent"],
            "Cookie": cookies,
        }

        async with session.ws_connect(ws_url, headers=ws_headers) as ws:
            # 1. Wait for connected
            hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=10))
            print(f"  [{response_format_name}] Hello: {hello.get('event')}")

            # 2. Wait for challenge
            try:
                pre = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=3))
                print(f"  [{response_format_name}] Pre-send event: {pre.get('event')} data={json.dumps(pre)[:200]}")

                if pre.get("event") == "challenge":
                    # Send the challenge response
                    resp_payload = make_response(pre)
                    print(f"  [{response_format_name}] Sending: {json.dumps(resp_payload)}")
                    await ws.send_str(json.dumps(resp_payload))
            except asyncio.TimeoutError:
                print(f"  [{response_format_name}] No pre-send challenge (timeout)")

            # 3. Send message
            send_payload = {
                "event": "send",
                "conversationId": conv_id,
                "content": [{"type": "text", "text": "hello"}],
                "mode": "chat",
                "context": {},
            }
            print(f"  [{response_format_name}] Sending message...")
            await ws.send_str(json.dumps(send_payload))

            # 4. Read responses
            events = []
            try:
                for _ in range(20):
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=10)
                    data = json.loads(msg)
                    ev = data.get("event", "")
                    events.append(ev)
                    print(f"  [{response_format_name}] RECV: {ev} -> {json.dumps(data)[:150]}")
                    if ev in ("done", "error", "partCompleted"):
                        break
            except asyncio.TimeoutError:
                print(f"  [{response_format_name}] Timeout waiting for response")

            print(f"  [{response_format_name}] Events: {events}")
            success = "appendText" in events
            print(f"  [{response_format_name}] SUCCESS: {success}")
            return success


async def test_send_format(format_name, make_send_payload):
    """Test a specific send event format."""
    import aiohttp

    sid = str(uuid.uuid4())
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}"

    headers = {
        "Origin": "https://copilot.microsoft.com",
        "Referer": "https://copilot.microsoft.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookies,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://copilot.microsoft.com/c/api/conversations",
            json={},
            headers=headers,
        ) as resp:
            if resp.status != 200:
                print(f"  [{format_name}] Conv create failed: HTTP {resp.status}")
                return False
            data = await resp.json()
            conv_id = data["id"]

        ws_headers = {
            "Origin": "https://copilot.microsoft.com",
            "Referer": "https://copilot.microsoft.com/",
            "User-Agent": headers["User-Agent"],
            "Cookie": cookies,
        }

        async with session.ws_connect(ws_url, headers=ws_headers) as ws:
            hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=10))
            print(f"  [{format_name}] Hello: {hello.get('event')}")

            # Handle challenge
            try:
                pre = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=3))
                if pre.get("event") == "challenge":
                    resp_payload = {"event": "challengeResponse", "id": pre.get("id")}
                    await ws.send_str(json.dumps(resp_payload))
                    print(f"  [{format_name}] Challenge handled")
            except asyncio.TimeoutError:
                pass

            # Send with the test format
            send_payload = make_send_payload(conv_id)
            print(f"  [{format_name}] Sending: {json.dumps(send_payload)[:250]}")
            await ws.send_str(json.dumps(send_payload))

            events = []
            try:
                for _ in range(20):
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=10)
                    data = json.loads(msg)
                    ev = data.get("event", "")
                    events.append(ev)
                    text = data.get("text", "")
                    print(f"  [{format_name}] RECV: {ev} {text[:80] if text else json.dumps(data)[:120]}")
                    if ev in ("done", "error", "partCompleted"):
                        break
            except asyncio.TimeoutError:
                print(f"  [{format_name}] Timeout")

            success = "appendText" in events
            print(f"  [{format_name}] SUCCESS: {success}")
            return success


async def test_api_start():
    """Test using /c/api/start instead of /c/api/conversations (gpt4free approach)."""
    import aiohttp

    sid = str(uuid.uuid4())
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}"

    headers = {
        "Origin": "https://copilot.microsoft.com",
        "Referer": "https://copilot.microsoft.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookies,
        "Content-Type": "application/json",
    }

    start_payload = {
        "timeZone": "America/Los_Angeles",
        "startNewConversation": True,
        "teenSupportEnabled": True,
        "correctPersonalizationSetting": True,
        "performUserMerge": True,
        "deferredDataUseCapable": True,
    }

    async with aiohttp.ClientSession() as session:
        # Use /c/api/start instead of /c/api/conversations
        print("  [api_start] POSTing to /c/api/start ...")
        async with session.post(
            "https://copilot.microsoft.com/c/api/start",
            json=start_payload,
            headers=headers,
        ) as resp:
            print(f"  [api_start] Status: {resp.status}")
            # Capture cookies from response
            new_cookies = {k: v.value for k, v in resp.cookies.items()}
            if new_cookies:
                print(f"  [api_start] New cookies from /start: {list(new_cookies.keys())}")
            if resp.status != 200:
                body = await resp.text()
                print(f"  [api_start] Error body: {body[:300]}")
                return
            data = await resp.json()
            conv_id = data.get("currentConversationId") or data.get("id")
            print(f"  [api_start] Conv ID: {conv_id}")
            print(f"  [api_start] Response keys: {list(data.keys())}")

        # Merge new cookies into cookie string
        merged_cookies = cookies
        for k, v in new_cookies.items():
            merged_cookies += f"; {k}={v}"

        ws_headers = {
            "Origin": "https://copilot.microsoft.com",
            "Referer": "https://copilot.microsoft.com/",
            "User-Agent": headers["User-Agent"],
            "Cookie": merged_cookies,
        }

        async with session.ws_connect(ws_url, headers=ws_headers) as ws:
            hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=10))
            print(f"  [api_start] Hello: {hello.get('event')}")

            # Check for pre-send challenge
            try:
                pre = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=3))
                pre_ev = pre.get("event", "")
                print(f"  [api_start] Pre-send: {pre_ev} data={json.dumps(pre)[:200]}")
                if pre_ev == "challenge":
                    # Try responding to challenge
                    resp_payload = {"event": "challengeResponse", "id": pre.get("id")}
                    await ws.send_str(json.dumps(resp_payload))
                    print(f"  [api_start] Challenge response sent")
            except asyncio.TimeoutError:
                print(f"  [api_start] No pre-send challenge")

            # Send message (no context field, matching gpt4free format)
            send_payload = {
                "event": "send",
                "conversationId": conv_id,
                "content": [{"type": "text", "text": "say hi"}],
                "mode": "chat",
            }
            print(f"  [api_start] Sending message...")
            await ws.send_str(json.dumps(send_payload))

            events = []
            try:
                for _ in range(30):
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=15)
                    data = json.loads(msg)
                    ev = data.get("event", "")
                    events.append(ev)
                    text = data.get("text", "")
                    print(f"  [api_start] RECV: {ev} {text[:100] if text else json.dumps(data)[:150]}")
                    if ev in ("done", "error", "partCompleted"):
                        break
            except asyncio.TimeoutError:
                print(f"  [api_start] Timeout")

            success = "appendText" in events
            print(f"  [api_start] Events: {events}")
            print(f"  [api_start] SUCCESS: {success}")
            return success


async def test_send_immediately_no_challenge():
    """Send message IMMEDIATELY after connected — don't wait for challenge at all."""
    import aiohttp

    sid = str(uuid.uuid4())
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}"

    headers = {
        "Origin": "https://copilot.microsoft.com",
        "Referer": "https://copilot.microsoft.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookies,
        "Content-Type": "application/json",
    }

    start_payload = {
        "timeZone": "America/Los_Angeles",
        "startNewConversation": True,
        "teenSupportEnabled": True,
        "correctPersonalizationSetting": True,
        "performUserMerge": True,
        "deferredDataUseCapable": True,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://copilot.microsoft.com/c/api/start",
            json=start_payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                print(f"  [immediate] /start failed: {resp.status}")
                return
            data = await resp.json()
            conv_id = data.get("currentConversationId")
            new_cookies = {k: v.value for k, v in resp.cookies.items()}
            print(f"  [immediate] Conv: {conv_id}")

        merged_cookies = cookies
        for k, v in new_cookies.items():
            merged_cookies += f"; {k}={v}"

        ws_headers = {
            "Origin": "https://copilot.microsoft.com",
            "Referer": "https://copilot.microsoft.com/",
            "User-Agent": headers["User-Agent"],
            "Cookie": merged_cookies,
        }

        async with session.ws_connect(ws_url, headers=ws_headers) as ws:
            hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=10))
            print(f"  [immediate] Hello: {hello.get('event')}")

            # IMMEDIATELY send message — don't wait for challenge
            send_payload = {
                "event": "send",
                "conversationId": conv_id,
                "content": [{"type": "text", "text": "say hello"}],
                "mode": "chat",
            }
            await ws.send_str(json.dumps(send_payload))
            print(f"  [immediate] Message sent immediately after connected")

            # Read all responses including challenges
            events = []
            try:
                for _ in range(30):
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=15)
                    data = json.loads(msg)
                    ev = data.get("event", "")
                    events.append(ev)
                    text = data.get("text", "")
                    print(f"  [immediate] RECV: {ev} {text[:100] if text else json.dumps(data)[:150]}")
                    if ev == "challenge":
                        # Respond to challenge inline
                        resp_payload = {"event": "challengeResponse", "id": data.get("id")}
                        await ws.send_str(json.dumps(resp_payload))
                        print(f"  [immediate] Challenge response sent inline")
                    if ev in ("done", "error", "partCompleted"):
                        break
            except asyncio.TimeoutError:
                print(f"  [immediate] Timeout")

            success = "appendText" in events
            print(f"  [immediate] Events: {events}")
            print(f"  [immediate] SUCCESS: {success}")


async def test_with_access_token():
    """Pass access token in WS URL like gpt4free does."""
    import aiohttp
    from urllib.parse import quote

    # Extract __Host-copilot-anon token
    access_token = None
    for pair in cookies.split(";"):
        pair = pair.strip()
        if pair.startswith("__Host-copilot-anon="):
            access_token = pair.split("=", 1)[1]
            break
    if not access_token:
        print("  [token] No __Host-copilot-anon token found")
        return

    print(f"  [token] Access token length: {len(access_token)}")

    sid = str(uuid.uuid4())
    # Add accessToken to WS URL like gpt4free
    ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={sid}&accessToken={quote(access_token)}"

    headers = {
        "Origin": "https://copilot.microsoft.com",
        "Referer": "https://copilot.microsoft.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookies,
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    start_payload = {
        "timeZone": "America/Los_Angeles",
        "startNewConversation": True,
        "teenSupportEnabled": True,
        "correctPersonalizationSetting": True,
        "performUserMerge": True,
        "deferredDataUseCapable": True,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://copilot.microsoft.com/c/api/start",
            json=start_payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                print(f"  [token] /start failed: {resp.status}")
                body = await resp.text()
                print(f"  [token] Body: {body[:300]}")
                return
            data = await resp.json()
            conv_id = data.get("currentConversationId")
            new_cookies = {k: v.value for k, v in resp.cookies.items()}
            print(f"  [token] Conv: {conv_id}")

        merged_cookies = cookies
        for k, v in new_cookies.items():
            merged_cookies += f"; {k}={v}"

        ws_headers = {
            "Origin": "https://copilot.microsoft.com",
            "Referer": "https://copilot.microsoft.com/",
            "User-Agent": headers["User-Agent"],
            "Cookie": merged_cookies,
        }

        async with session.ws_connect(ws_url, headers=ws_headers) as ws:
            hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=10))
            print(f"  [token] Hello: {hello.get('event')}")

            # Send immediately — token should bypass challenge
            send_payload = {
                "event": "send",
                "conversationId": conv_id,
                "content": [{"type": "text", "text": "say hi briefly"}],
                "mode": "chat",
            }
            await ws.send_str(json.dumps(send_payload))
            print(f"  [token] Message sent")

            events = []
            try:
                for _ in range(30):
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=15)
                    data = json.loads(msg)
                    ev = data.get("event", "")
                    events.append(ev)
                    text = data.get("text", "")
                    print(f"  [token] RECV: {ev} {text[:100] if text else json.dumps(data)[:150]}")
                    if ev == "challenge":
                        resp_payload = {"event": "challengeResponse", "id": data.get("id")}
                        await ws.send_str(json.dumps(resp_payload))
                    if ev in ("done", "error", "partCompleted"):
                        break
            except asyncio.TimeoutError:
                print(f"  [token] Timeout")

            success = "appendText" in events
            print(f"  [token] Events: {events}")
            print(f"  [token] SUCCESS: {success}")


async def main():
    print("\n=== Test: accessToken in WS URL (gpt4free method) ===")
    try:
        await test_with_access_token()
    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
