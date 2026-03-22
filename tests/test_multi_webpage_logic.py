import os
import httpx
import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CONTAINER_E2E", "").lower() not in ("1", "true", "yes"),
    reason="Requires Docker stack; set RUN_CONTAINER_E2E=1 (see docker compose `test` service).",
)


@pytest.mark.asyncio
async def test_multi_webpage_logic():
    print("\n--- STARTING BOUNTY HUNTER FINAL SEAL (PYTHON-PLAYWRIGHT) ---")
    
    C1_URL = os.getenv("BASE_URL", "http://app:8000")
    C3_URL = os.getenv("C3_URL", "http://browser-auth:8001")
    
    async with httpx.AsyncClient(timeout=120) as client:
        # 1. Verify C1 Reachability
        print("Step 1: Checking C1 models endpoint...")
        resp = await client.get(f"{C1_URL}/v1/models")
        assert resp.status_code == 200
        print(f"   ✓ C1 is up (models: {len(resp.json()['data'])})")

        # 2. Launch NAMED SESSIONS via API
        print("Step 2: Launching Named Sessions (Alpha & Beta)...")
        session_alpha = "bounty-hunter-alpha"
        session_beta = "bounty-hunter-beta"

        await client.post(f"{C1_URL}/v1/agent/start", json={"session_name": session_alpha})
        await client.post(f"{C1_URL}/v1/agent/start", json={"session_name": session_beta})
        print("   ✓ Sessions Alpha and Beta started.")

        # 3. Verify Isolation & Context
        print("Step 3: Verifying session isolation...")
        
        # Set context for Alpha
        print("   Setting Alpha context...")
        resp_a1 = await client.post(f"{C1_URL}/v1/agent/task", json={
            "session_name": session_alpha,
            "task": "My secret code is BLUE."
        })
        print(f"   Alpha task 1 status: {resp_a1.status_code}")
        
        # Set context for Beta
        print("   Setting Beta context...")
        resp_b1 = await client.post(f"{C1_URL}/v1/agent/task", json={
            "session_name": session_beta,
            "task": "My secret code is RED."
        })
        print(f"   Beta task 1 status: {resp_b1.status_code}")

        # Query Alpha
        print("   Querying Alpha for secret...")
        resp_a2 = await client.post(f"{C1_URL}/v1/agent/task", json={
            "session_name": session_alpha,
            "task": "What was my secret code? Answer with ONLY the color."
        })
        
        print(f"   Alpha query status: {resp_a2.status_code}")
        data = resp_a2.json()
        print(f"   Alpha query raw response: {data}")
        
        result = data.get("result", "")
        if result is None:
            result = ""
            
        print(f"   Alpha final result: {result}")
        
        assert "BLUE" in result.upper()
        assert "RED" not in result.upper()
        print("   ✓ SUCCESS: Session Alpha remains isolated.")

    # 4. Playwright Browser Interaction (C3)
    print("Step 4: Simulating Human interaction in C3 Browser...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{C3_URL}/health")
        content = await page.content()
        assert "ok" in content.lower()
        print("   ✓ C3 Browser (noVNC) is responsive.")
        await browser.close()

    print("--- BOUNTY HUNTER SEAL: 100% VERIFIED ---")
