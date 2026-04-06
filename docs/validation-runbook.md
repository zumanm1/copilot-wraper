# Validation Runbook — C9 Agent Pipeline

All tests here are repeatable from cold-start. Run them after any code change, container rebuild, or machine reboot.

---

## Prerequisites

1. **All containers running:**
   ```bash
   docker compose up -d
   docker ps --format "table {{.Names}}\t{{.Status}}"
   ```
   Expected: C1, C3, C5, C6, C7a, C7b, C8, C9 all `Up … (healthy)`.

2. **M365 session active in C3:**
   - Open noVNC: http://localhost:6080
   - Sign in to https://m365.cloud.microsoft if not already signed in
   - Verify: `curl -s http://localhost:8001/session-health` → `"session":"active"`

3. **C3 PagePool initialized:**
   ```bash
   curl -s http://localhost:8001/status
   ```
   Expected: `"pool_initialized":true`, `"pool_available":6`.

   If `pool_available: 0` after startup, reset the pool:
   ```bash
   curl -s -X POST http://localhost:8001/pool-reset
   ```

---

## Test Suite Overview

| Test | File | What it validates | Speed |
|------|------|-------------------|-------|
| **Unit tests** | `tests/test_unit_*.py` | Python logic, no containers needed | ~10s |
| **E2E agent pipelines (sequential)** | `tests/test_e2e_c9_validation.py` | Each agent C1→C3→M365 roundtrip | ~3min |
| **E2E parallel (C9 UI equivalent)** | `tests/test_e2e_c9_validation.py` | All 6 agents via `/api/validate` | ~35s |
| **Puppeteer /pairs UI** | `tests/puppeteer_novnc/validate_pairs.mjs` | Browser: Run All Parallel button, badge results | ~35s |
| **C3 noVNC UI** | `tests/puppeteer_novnc/validate_novnc.mjs` | noVNC framebuffer visible | ~10s |

---

## 1. Unit Tests (no containers needed)

```bash
cd /path/to/copilot-openai-wrapper
pytest tests/test_unit_*.py -v
```

Expected: **all pass** (125+). One known pre-existing failure: `test_portal_consumer_explicit_env`.

---

## 2. Infrastructure Health Check

```bash
curl -s http://localhost:8000/health       # C1
curl -s http://localhost:8001/health       # C3
curl -s http://localhost:8001/status       # C3 pool state
curl -s http://localhost:8001/session-health   # M365 session
curl -s http://localhost:6090/health       # C9
curl -s http://localhost:18789/healthz     # C7a gateway
```

All should return `"status":"ok"` / `"session":"active"`.

---

## 3. E2E Agent Pipeline Tests (pytest)

```bash
pytest tests/test_e2e_c9_validation.py -v
```

Runs:
- **Infrastructure checks** (C1/C3/C9 health, pool state, session health)
- **Sequential per-agent** — each of 6 agents called individually via C1
- **Parallel validation** — C9 `/api/validate` endpoint (same as "Run All Parallel" button)
- **Pairs page load** — checks `/pairs` HTML has all 6 agent rows and the run button

Run subsets:
```bash
pytest tests/test_e2e_c9_validation.py -v -k "infrastructure"
pytest tests/test_e2e_c9_validation.py -v -k "sequential"
pytest tests/test_e2e_c9_validation.py -v -k "parallel"
```

Standalone (no pytest):
```bash
python tests/test_e2e_c9_validation.py
```

---

## 4. Puppeteer Pairs Page Test (browser automation)

```bash
cd tests/puppeteer_novnc
npm install          # first time only
node validate_pairs.mjs
```

What it does:
1. Opens http://localhost:6090/pairs in headless Chromium
2. Verifies all 6 agent rows are present
3. Clicks **Run All Parallel ⚡** button
4. Polls `#status-{agent-id}` cells every 5s until all show PASS/FAIL
5. Reports final result per agent

Expected output:
```
1. Navigating to http://localhost:6090/pairs ...
2. Table rows found: [ 'row-c2-aider', 'row-c5-claude-code', ... ]
3. Clicking #run-parallel button...
4. Polling for results (up to 10min)...
   [5s] 0/6 complete: RUNNING | RUNNING | ...
   [30s] 6/6 complete: PASS | PASS | PASS | PASS | PASS | PASS
=== FINAL RESULTS ===
  [PASS] c2-aider       | status="PASS" time="0.13s"
  [PASS] c5-claude-code | status="PASS" time="24.74s"
  ...
SUMMARY: 6/6 passed, 0 failed
```

---

## 5. Direct curl smoke tests

Quick per-agent curl for debugging:

```bash
# C2 Aider (C1 direct, no C3)
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-ID: c2-aider' \
  -H 'X-Chat-Mode: work' \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Tell me a joke"}],"stream":false}' \
  --max-time 360 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:100])"

# C5 Claude Code (C1→C3→M365)
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-ID: c5-claude-code' \
  -H 'X-Chat-Mode: work' \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Tell me a joke"}],"stream":false}' \
  --max-time 360 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:100])"

# C6/C7/C8/C9: same pattern, change X-Agent-ID to:
#   c6-kilocode | c7-openclaw | c8-hermes | c9-jokes

# All agents at once via C9:
curl -s -X POST http://localhost:6090/api/validate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Tell me a joke","chat_mode":"work"}' \
  --max-time 600 | python3 -m json.tool
```

---

## 6. Pool reset (after container restart)

If C3 started with DNS errors and `pool_available: 0`:

```bash
curl -s -X POST http://localhost:8001/pool-reset | python3 -m json.tool
```

Expected: `"pool_initialized": true, "pool_available": 6`.

---

## Timeout Configuration

| Layer | Setting | Value | File |
|-------|---------|-------|------|
| C1 request timeout | `REQUEST_TIMEOUT` | 180s | `docker-compose.yml` |
| C1 connect timeout | `CONNECT_TIMEOUT` | 15s | `docker-compose.yml` |
| C9 httpx read | `read` | 360s | `c9_jokes/app.py` |
| C9 per-request | `timeout=` | 360s | `c9_jokes/app.py` |
| Puppeteer default | `setDefaultTimeout` | 600s | `validate_pairs.mjs` |

---

## Known Issues & Mitigations

| Issue | Symptom | Mitigation |
|-------|---------|------------|
| M365 auth dialog after tab teardown | Agent returns "Authentication required" | Fixed: now waits 8s and retries before failing |
| PagePool empty after DNS failure at startup | `pool_available: 0` | Fixed: on-demand tab creation; use `/pool-reset` for manual recovery |
| Timeout cascade under parallel load | All agents fail at 180s | Fixed: C1 timeout 180s, C9 timeout 360s |
| C9 showing generic "failed" | Error field blank | Fixed: C1 JSON error body parsed and propagated |
| Session health false positive | "active" with broken pool | Fixed: `pool_warning` field added |

---

## CI Quick Run (all non-E2E)

```bash
pytest tests/test_unit_*.py tests/test_e2e_c9_validation.py::TestInfrastructure -v
```

Full E2E (requires live containers + M365 session):
```bash
pytest tests/test_e2e_c9_validation.py -v
node tests/puppeteer_novnc/validate_pairs.mjs
```
