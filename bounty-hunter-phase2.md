# Bounty Hunter Phase 2: Specialized Squad Instructions

I have hired 10 "Bounty Hunter" specialists (representing 10 key investigative vectors) to solve the 8-container stabilization and the 30s timeout blocker.

### Hunter 1: The Network Forensic (Cisco CCIE Focus)
- **Task:** Investigate `docker-compose.yml` network bridge settings. Find why Playwright (CT) sees a 30s timeout despite the 90s override. Check `sysctl` for socket timeouts.

### Hunter 2: The WebSocket Sniffer (Python/AIOHTTP Focus)
- **Task:** Trace `copilot_backend.py`'s `_ws_stream`. Identify if `sydney-py` is failing the handshake or if the initial frame is dropped for multimodal uploads.

### Hunter 3: The SSE Architect (FastAPI Focus)
- **Task:** Verify `server.py` `stream_gen`. Ensure `Response` content is NOT buffered by any middleware. Fix the `transfer-encoding: chunked` frame arrival.

### Hunter 4: The Auth Guardian (C3 Browser Focus)
- **Task:** Harden the `start.sh` verification. Prove C1 has valid `BING_COOKIES` before any agent (C2, C5, C6, C7) is permitted to take a task.

### Hunter 5: The Multimodal Engineer (OpenAI Case-Sensitivity)
- **Task:** Double-check the `imageUrl` vs `image_url` payload in `copilot_backend.py`. Ensure we are using the exact reverse-engineered schema for the latest Copilot patch.

### Hunter 6: The Agent Orchestrator (C2, C5, C6, C7 Sync)
- **Task:** Ensure `AGENT_ID` routing in C1 is consistent. Validate that each agent gets a dedicated backend session without cross-talk.

### Hunter 7: The OS Hardener (Mac/Linux Permissions)
- **Task:** Verify `/tmp` mount permissions in the Docker runtime. Ensure attachments are readable by the `appuser` (UID 1000).

### Hunter 8: The Log Aggregator (Uvicorn - unbuffered)
- **Task:** Force `PYTHONUNBUFFERED=1` across the stack. Ensure all `WS_SEND` and `WS_RECV` logs are visible in `docker compose logs`.

### Hunter 9: The Performance Analyst (Circuit Breaker)
- **Task:** Tune `circuit_breaker.py`. Ensure it doesn't trip prematurely during long multimodal uploads (which C1 might misinterpret as a "hang").

### Hunter 10: The QA Automator (Playwright Specialist)
- **Task:** Refactor `tests/test_playwright.py` to use explicit `page.wait_for_request` or a custom timer to bypass the `APIRequestContext` 30s limitation.

---
**Phase 2 Instruction Complete.** Now initiating Step 1: UPFRONT PLANNING based on these hunters' mandates. approval requested.
