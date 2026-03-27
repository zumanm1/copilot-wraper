# 06 — Testing & Validation

## Prerequisites Checklist

Before running any test, verify these are all true:

```bash
# 1. All containers healthy
docker compose ps --format "table {{.Names}}\t{{.Status}}"
# Expected: all show "Up ... (healthy)"

# 2. C1 responds
curl -s http://localhost:8000/health
# {"status":"ok","service":"copilot-openai-wrapper"}

# 3. C3 pool initialized
curl -s http://localhost:8001/status
# pool_initialized: true, pool_available: 6

# 4. M365 session active
curl -s http://localhost:8001/session-health
# session: "active"
```

If session is expired: sign in at `http://localhost:6080` (noVNC).

---

## Test 1 — Direct curl Smoke Test (fastest)

Tests C1 with a specific agent ID. Validates the full C1 → C3 → M365 path.

```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c10-myagent" \
  -H "X-Chat-Mode: work" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Say OK"}],"stream":false}' \
  --max-time 360
```

**Pass criteria:**
- HTTP 200
- `choices[0].message.content` is non-empty

**Quick one-liner with pass/fail output:**
```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c10-myagent" \
  -H "X-Chat-Mode: work" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Say OK"}],"stream":false}' \
  --max-time 360 \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
text = d.get('choices',[{}])[0].get('message',{}).get('content','')
print('PASS:', text[:80]) if text else print('FAIL:', d.get('detail','no detail'))
sys.exit(0 if text else 1)
"
```

---

## Test 2 — validate_new_agent.sh (automated)

Runs all checks in sequence and prints a clean PASS/FAIL summary.

```bash
bash API-DOCUMENTATION/stubs/validate_new_agent.sh c10-myagent
```

Exit code 0 = pass, 1 = fail.

See `stubs/validate_new_agent.sh` for the full script.

---

## Test 3 — Sequential All-Agents curl Loop

Tests all 6 existing agents + your new one in sequence:

```bash
for agent in c2-aider c5-claude-code c6-kilocode c7-openclaw c8-hermes c9-jokes c10-myagent; do
  result=$(curl -s -X POST http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "X-Agent-ID: $agent" \
    -H "X-Chat-Mode: work" \
    -d '{"model":"copilot","messages":[{"role":"user","content":"Say OK"}],"stream":false}' \
    --max-time 360)
  text=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('choices',[{}])[0].get('message',{}).get('content','')[:40])" 2>/dev/null)
  if [ -n "$text" ]; then
    echo "  [PASS] $agent: $text"
  else
    echo "  [FAIL] $agent: $(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail','?'))" 2>/dev/null)"
  fi
done
```

---

## Test 4 — C9 Parallel Validate (via API)

Runs all agents in parallel via C9's `/api/validate` endpoint. Only tests agents in C9's AGENTS list.

```bash
curl -s -X POST http://localhost:6090/api/validate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Say OK","chat_mode":"work"}' \
  --max-time 600 \
  | python3 -m json.tool
```

Pass criteria: `"passed": N, "failed": 0` where N = total agents in the list.

---

## Test 5 — pytest E2E Suite

Runs the full E2E test suite (14 tests) including infra checks, all 6 sequential roundtrips, and parallel validate:

```bash
pytest tests/test_e2e_c9_validation.py -v
```

Expected output:
```
13 passed, 1 xfailed in ~47s
```

The 1 xfail (`test_c3_pool_has_available_tabs`) is expected when all tabs are assigned — not a real failure.

---

## Test 6 — Puppeteer /pairs Browser Test

Automates the C9 dashboard "Run All Parallel" button:

```bash
cd tests/puppeteer_novnc
node validate_pairs.mjs
```

Expected output:
```
[30s] 6/6 complete: PASS | PASS | PASS | PASS | PASS | PASS
=== FINAL RESULTS ===
  [PASS] c2-aider     ...
  [PASS] c5-claude-code ...
  ...
Exit code: 0
```

---

## Test 7 — Streaming Test

Validates SSE streaming works end-to-end:

```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c10-myagent" \
  -H "X-Chat-Mode: work" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Count to 3"}],"stream":true}' \
  --max-time 360 \
  --no-buffer
```

You should see SSE `data:` lines appearing progressively, ending with `data: [DONE]`.

---

## Expected Pass Criteria (All Tests)

| Test | Pass condition |
|------|---------------|
| curl smoke test | HTTP 200, non-empty `content` |
| validate_new_agent.sh | Exit code 0 |
| Sequential loop | All agents: `[PASS]` |
| C9 parallel validate | `passed == total`, `failed == 0` |
| pytest E2E | `13 passed, 1 xfailed` |
| Puppeteer pairs | `6/6 complete: PASS`, exit code 0 |
| Streaming test | Progressive `data:` lines, ends with `[DONE]` |

---

## Common Failure Modes and Fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `HTTP 500` — "Authentication required" | M365 session expired | Sign in at `http://localhost:6080` |
| `HTTP 500` — "C3 /chat failed" | C3 tab error / pool empty | `curl -X POST http://localhost:8001/pool-reset` |
| `HTTP 500` — empty detail | C1 circuit breaker open | Wait 60s, retry; check `docker logs C1_copilot-api` |
| `Connection refused` | C1 or C3 not running | `docker compose up app browser-auth -d` |
| Empty `content` in response | C3 returned empty WS frame | Check `docker logs C3_browser-auth \| tail -30` |
| Timeout at 180s | Tab was doing full teardown (first use after restart) | Retry once — next call uses fast reset |
| `pool_initialized: false` | C3 started before M365 DNS | `curl -X POST http://localhost:8001/pool-reset` |
| pytest: `ModuleNotFoundError: agent_manager` | Conftest fixture conflict | Run `pytest tests/test_e2e_c9_validation.py` (not `pytest tests/`) |

---

## Viewing Logs

```bash
# C1 request trace
docker logs C1_copilot-api --tail 50 --follow

# C3 Playwright activity
docker logs C3_browser-auth --tail 50 --follow

# C3 pool + tab events only
docker logs C3_browser-auth 2>&1 | grep -E "PagePool|browser_chat|Tab|auth"

# C9 validation runs
docker logs C9_jokes --tail 30
```

---

## Resetting State

```bash
# Reset C3 PagePool (no restart needed)
curl -X POST http://localhost:8001/pool-reset

# Force C1 to reload cookies from .env
curl -X POST http://localhost:8000/v1/reload-config   2>/dev/null || true

# Full restart (preserves volumes)
docker compose down && docker compose up -d

# Nuclear: remove all state and rebuild
docker compose down -v && docker compose build && docker compose up -d
```
