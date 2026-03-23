# Bounty Hunter Phase 1: Deep Forensic Analysis & Systemic Gaps

## 1. Core Architecture: The C1+C3 Foundation

The entire ecosystem relies on the **C1 (API) + C3 (Auth)** synergy. Any friction here causes a "Cascade of Failure" in C2 (Agent), C5 (Claude), C6 (Kilo), and C7 (OpenClaw).

### The "30s Timeout Paradox" Mystery
- **Investigation:** `test_playwright.py` explicitly sets `TIMEOUT_MS = 90_000` (Line 37) and applies it to the `APIRequestContext`.
- **Finding:** Playwright reports `Request timed out after 30000ms`.
- **Bounty Hunter Verdict:** This is a **Proxy/WebSocket Stall**. When C1 receives a request, it opens a WebSocket to Copilot. If the WebSocket takes >30s to respond (common for images), the FastAPI `uvicorn` layer or a hidden Docker network-layer proxy is closing the socket at the default 30s mark, even if Playwright wants to wait longer.

## 2. Bounty Hunter Skills Required (Phase 2 Instruction Prep)

I have identified the following skill gaps for the "Bounty Hunters":
- **Hunter 1 (Log Forensic):** Must bridge the gap between Uvicorn and Docker log buffering.
- **Hunter 2 (WebSocket Sniffer):** Must trace the raw sync/async state of the `SydneyClient` during multimodal uploads.
- **Hunter 3 (SSE Specialist):** Must ensure Server-Sent Events are truly unbuffered (current gap: `transfer-encoding: chunked` is present but frames are not arriving in real-time).
- **Hunter 4 (Auth Guardian):** Must verify why C3 cookies aren't instantly refreshing in C1's memory without a restart.

## 3. Gaps in Current Workflow

- **Gap 1: Async/Sync Friction.** C1 uses FastAPI (Async) but Playwright tests are running Sync.
- **Gap 2: Attachment Persistence.** `/tmp` in Docker is ephemeral. If C1 restarts between extraction and upload, the image is lost.
- **Gap 3: Deployment Order.** C2, C5, C6, C7 are often started before C1+C3 are "Strictly Proven" (Auth validated).

## 4. Operational Status

- **C1+C3:** Operational but "Stubborn" (stalling on heavy loads).
- **C2, C5, C6, C7:** Started but "Blind" (they rely on C1, which is stalling).
- **CT (Tests):** 16/45 Passed. 5 Critical Failures (Timeout).

---
**Plan for Phase 2:** Instruction of the 10 Bounty Hunters to dig into these specific code-level gaps. approval requested.
