import asyncio, sys, json, os, base64
from copilot_backend import CopilotBackend

async def test_diag():
    print("DIAG: Starting", flush=True)
    backend = CopilotBackend()
    
    # Test 1: Basic Streaming
    print("DIAG: Test 1 - Streaming", flush=True)
    try:
        async for token in backend.chat_completion_stream("Say hello"):
            print(f"TOKEN: {token}", flush=True)
    except Exception as e:
        print(f"ERROR 1: {e}", flush=True)

    # Test 2: Multimodal
    print("DIAG: Test 2 - Multimodal", flush=True)
    img_data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")
    path = "/tmp/diag_test.png"
    with open(path, "wb") as f:
        f.write(img_data)
    
    try:
        async for token in backend.chat_completion_stream("What is in this image?", attachment_path=path):
            print(f"TOKEN: {token}", flush=True)
    except Exception as e:
        print(f"ERROR 2: {e}", flush=True)

if __name__ == "__main__":
    asyncio.run(test_diag())
