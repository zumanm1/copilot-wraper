# 06 — Testing & Validation

> **Last updated: 2026-03-31**
> How to verify every layer of the stack is working — from a single `curl` to full parallel batch validation using the C9 console.

---

## Table of Contents

- [Prerequisites Checklist](#prerequisites-checklist)
- [Test 1 — C1 Health Smoke Test](#test-1--c1-health-smoke-test)
- [Test 2 — C3 Browser Auth Smoke Test](#test-2--c3-browser-auth-smoke-test)
- [Test 3 — First Chat Request (OpenAI Format)](#test-3--first-chat-request-openai-format)
- [Test 4 — Anthropic Format (C5 Path)](#test-4--anthropic-format-c5-path)
- [Test 5 — Thinking Mode (X-Chat-Mode)](#test-5--thinking-mode-x-chat-mode)
- [Test 6 — Work/Web Toggle (X-Work-Mode)](#test-6--workweb-toggle-x-work-mode)
- [Test 7 — File Upload + Chat](#test-7--file-upload--chat)
- [Test 8 — Per-Agent Session Isolation](#test-8--per-agent-session-isolation)
- [Test 9 — All Agent Containers Health](#test-9--all-agent-containers-health)
- [Test 10 — C9 Validation Console (Browser UI)](#test-10--c9-validation-console-browser-ui)
- [Test 11 — Batch Validation via C9 API](#test-11--batch-validation-via-c9-api)
- [Test 12 — Streaming SSE](#test-12--streaming-sse)
- [Test 13 — Unit Tests (pytest)](#test-13--unit-tests-pytest)
- [Test 14 — End-to-End Playwright Tests](#test-14--end-to-end-playwright-tests)
- [Timeout Configuration Reference](#timeout-configuration-reference)
- [Log Viewing](#log-viewing)
- [State Reset Commands](#state-reset-commands)
- [Failure Modes & Fixes](#failure-modes--fixes)

---

## Prerequisites Checklist

Before running any test, confirm all of these:

```bash
# 1. Docker containers running
docker compose ps
# Expected: all services show "running" or "healthy"

# 2. C1 healthy
curl -sf http://localhost:8000/health && echo "C1 OK"

# 3. C3 healthy
curl -sf http://localhost:8001/health && echo "C3 OK"

# 4. C9 healthy
curl -sf http://localhost:6090/api/status && echo "C9 OK"

# 5. Cookies loaded in C1
curl http://localhost:8000/v1/debug/cookie
# Should show: "copilot_cookies_set": true
```

If step 5 fails, run the cookie extraction flow first:
```bash
open http://localhost:6080   # noVNC — log into copilot.microsoft.com
curl -X POST http://localhost:8001/extract
curl -X POST http://localhost:8000/v1/reload-config
```

---

## Test 1 — C1 Health Smoke Test

```bash
curl http://localhost:8000/health
```

✅ **Pass:**
```json
{"status": "ok", "service": "copilot-openai-wrapper"}
```

❌ **Fail:** Connection refused → C1 is not running. Run `docker compose up app -d`.

---

## Test 2 — C3 Browser Auth Smoke Test

```bash
# API health
curl http://localhost:8001/health

# Pool status
curl http://localhost:8001/status

# Session health (M365 profile only)
curl http://localhost:8001/session-health
```

✅ **Pass:**
```json
{"status": "ok"}
{"browser_running": true, "pool_size": 6, "tabs_open": 6}
{"auth_ok": true, "m365_session_valid": true}
```

❌ **Fail `auth_ok: false`:** Cookies expired. Re-run extraction flow.
❌ **Fail `m365_session_valid: false`:** M365 session needs re-login in noVNC.

---

## Test 3 — First Chat Request (OpenAI Format)

```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Reply with exactly: hello world"}]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
```

✅ **Pass:** Prints `hello world` (or similar)
❌ **Fail HTTP 401/403:** Cookies expired → re-run extraction.
❌ **Fail empty content:** Copilot responded but returned no text — check C3 logs.

---

## Test 4 — Anthropic Format (C5 Path)

```bash
curl -s -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: not-needed" \
  -H "anthropic-version: 2023-06-01" \
  -H "X-Agent-ID: c5-claude-code" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":64,"messages":[{"role":"user","content":"Say: test ok"}]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['content'][0]['text'])"
```

✅ **Pass:** Prints `test ok` (or similar)
❌ **Fail `400 Bad Request`:** Check `anthropic-version` header is present.

---

## Test 5 — Thinking Mode (X-Chat-Mode)

```bash
# Test all three thinking modes
for mode in auto quick deep; do
  echo "=== Mode: $mode ==="
  curl -s -X POST http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "X-Chat-Mode: $mode" \
    -d '{"model":"copilot","messages":[{"role":"user","content":"What is 1+1?"}]}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:80])"
done
```

✅ **Pass:** All three return "2" or similar — each mode should work without error.

**Via C9 UI:**
1. Open `http://localhost:6090/chat`
2. Select each thinking mode from the dropdown (Auto / Quick Response / Think Deeper)
3. Send "What is 1+1?" — should receive a response for each mode

---

## Test 6 — Work/Web Toggle (X-Work-Mode)

*Only meaningful in M365 profile mode.*

```bash
# Work mode — M365 enterprise data
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Work-Mode: work" \
  -H "X-Agent-ID: c2-aider" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"What mode are you in?"}]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:100])"

# Web mode — public internet
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Work-Mode: web" \
  -H "X-Agent-ID: c2-aider" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"What mode are you in?"}]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:100])"
```

✅ **Pass:** Both return a response without error.

**Via C9 UI:**
1. Open `http://localhost:6090/chat`
2. Toggle Work ↔ Web button
3. Send a message — the toggle state is sent as `X-Work-Mode` to C1

---

## Test 7 — File Upload + Chat

```bash
# Step 1: Upload a file
echo "The answer to the universe is 42." > /tmp/test_doc.txt

FILE_ID=$(curl -s -X POST http://localhost:8000/v1/files \
  -F "file=@/tmp/test_doc.txt;type=text/plain" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['file_id'])")

echo "Uploaded file_id: $FILE_ID"

# Step 2: Reference the file in a chat message
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"copilot\",
    \"messages\": [{
      \"role\": \"user\",
      \"content\": [
        {\"type\": \"text\", \"text\": \"What number is mentioned in the attached file?\"},
        {\"type\": \"file_ref\", \"file_id\": \"$FILE_ID\", \"filename\": \"test_doc.txt\"}
      ]
    }]
  }" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'][:100])"
```

✅ **Pass:** Response mentions "42".

**Via C9 UI:**
1. Open `http://localhost:6090/chat`
2. Click the "+" button → "⬆ Upload files"
3. Upload any text file or PDF
4. Type a question about the file content and send
5. Verify the response references the file content

---

## Test 8 — Per-Agent Session Isolation

Send the same prompt from two different agent IDs and verify they get independent sessions:

```bash
# Seed a fact in c2-aider's session
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c2-aider" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Remember: my secret number is 777"}]}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'][:80])"

# Ask c8-hermes — it should NOT know the secret number
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c8-hermes" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"What is my secret number?"}]}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'][:100])"
```

✅ **Pass:** `c8-hermes` says it doesn't know any secret number.

---

## Test 9 — All Agent Containers Health

```bash
# Check health of all agent containers
docker compose exec agent-terminal       curl -sf http://localhost:8080/health && echo "C2 OK"
docker compose exec claude-code-terminal curl -sf http://localhost:8080/health && echo "C5 OK"
docker compose exec kilocode-terminal    curl -sf http://localhost:8080/health && echo "C6 OK"
docker compose exec openclaw-cli         curl -sf http://localhost:8080/health && echo "C7b OK"
docker compose exec hermes-agent         curl -sf http://localhost:8080/health && echo "C8 OK"
curl -sf http://localhost:18789/healthz && echo "C7a OK"
```

Or use the validate script:
```bash
./API-DOCUMENTATION/stubs/validate_new_agent.sh c2-aider
./API-DOCUMENTATION/stubs/validate_new_agent.sh c8-hermes
```

---

## Test 10 — C9 Validation Console (Browser UI)

Open each page and verify it loads without errors:

```bash
open http://localhost:6090/         # Dashboard — health cards for all containers
open http://localhost:6090/chat     # Chat page
open http://localhost:6090/pairs    # Pairs validation
open http://localhost:6090/logs     # Logs (may be empty on first run)
open http://localhost:6090/health   # Health snapshots
open http://localhost:6090/sessions # Sessions (proxy of C1 /v1/sessions)
open http://localhost:6090/api      # API reference (/api/docs redirects)
```

**Dashboard check:**
- All container cards should show green status (✅)
- Any red card means that container is down

**Chat page check:**
1. Select any agent from the dropdown
2. Select thinking mode from the dropdown (Auto / Quick Response / Think Deeper)
3. Click Work or Web toggle
4. Type "Hello" and press Send
5. Verify a response appears below

**Pairs page check:**
1. Type a prompt in the text box
2. Select "Parallel" mode
3. Click "Run All"
4. Verify responses appear for each agent card

**Logs page check:**
After the chat/pairs tests above:
1. Open `/logs`
2. Verify entries appear with `source` column showing `chat` or `validate`
3. Verify `elapsed_ms` column shows timing values

---

## Test 11 — Batch Validation via C9 API

```bash
# Run all registered agents in parallel
curl -s -X POST http://localhost:6090/api/validate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Reply with exactly: validation ok",
    "parallel": true,
    "chat_mode": "quick"
  }' | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"Run ID: {d['run_id']} | Passed: {d['passed']}/{d['passed']+d['failed']}\")
for r in d['results']:
    status = '✅' if r['ok'] else '❌'
    print(f\"  {status} {r['agent_id']}: {str(r.get('response',''))[:60]}\")"
```

✅ **Pass:** All agents return a response. `passed` == total agents.

```bash
# Verify the run appears in logs
curl -s "http://localhost:6090/api/logs?source=validate&limit=5" \
  | python3 -c "import sys,json; [print(r['agent_id'], r['elapsed_ms'], 'ms') for r in json.load(sys.stdin)['logs']]"
```

---

## Test 12 — Streaming SSE

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Count to 5 slowly"}],"stream":true}'
```

✅ **Pass:** Data events stream in, ending with `data: [DONE]`

```
data: {"id":"chatcmpl-xyz","choices":[{"delta":{"role":"assistant"},"index":0}]}
data: {"id":"chatcmpl-xyz","choices":[{"delta":{"content":"1"},"index":0}]}
data: {"id":"chatcmpl-xyz","choices":[{"delta":{"content":", 2"},"index":0}]}
...
data: [DONE]
```

**C9 proxy path:**

```bash
curl -N -X POST http://localhost:6090/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id":"c8-hermes",
    "messages":[{"role":"user","content":"Count to 5 slowly"}],
    "stream":true
  }'
```

✅ **Pass:** C9 emits incremental `token` events followed by one `done` event.

```text
data: {"type":"token","text":"1"}
data: {"type":"token","text":", 2"}
...
data: {"type":"done","text":"1, 2, 3, 4, 5","session_id":"cs_ab12cd34","token_estimate":23,"http_status":200}
```

---

## Test 13 — Unit Tests (pytest)

```bash
cd /path/to/copilot-openai-wrapper

# Run all unit tests
python3 -m pytest tests/test_unit_c9.py tests/test_unit_server.py tests/test_unit_models.py \
  -v --tb=short

# Run only C9 tests
python3 -m pytest tests/test_unit_c9.py -v

# Run only C1 server tests
python3 -m pytest tests/test_unit_server.py -v

# Run with coverage
python3 -m pytest tests/ -v --cov=. --cov-report=term-missing
```

Expected: **43+ tests pass**. Key test classes:

| Test class | What it validates |
|---|---|
| `TestC9PageRoutes` | All HTML pages return 200, contain expected elements |
| `TestC9ChatAPI` | `/api/chat` proxies correctly, logs to DB |
| `TestC9ValidateAPI` | `/api/validate` runs agents, logs source='validate' |
| `TestC9UploadAPI` | `/api/upload` forwards to C1, returns file_id |
| `TestC9Logging` | elapsed_ms, source column, error text in chat_logs |
| `TestThinkingMode` | X-Chat-Mode maps auto→smart, quick→balanced, deep→reasoning |
| `TestFileUploadEndpoint` | C1 `/v1/files` validates MIME, size, extracts text |
| `TestExtractUserPromptFileRef` | file_ref content parts resolved to text before Copilot call |

---

## Test 14 — End-to-End Playwright Tests

```bash
# Run full E2E suite against live stack
docker compose run --rm test

# View HTML report
open tests/reports/report.html   # macOS
xdg-open tests/reports/report.html  # Linux
```

The CT container runs:
- `test_playwright.py` — 45 tests covering C1, C3, agent endpoints
- `test_new_containers.py` — C2/C5/C6/C7/C8 container health + roundtrip

Set environment variables to enable full E2E:
```bash
RUN_CONTAINER_E2E=1     # enables container-to-container tests
BASE_URL=http://app:8000
C3_URL=http://browser-auth:8001
```

---

## Timeout Configuration Reference

| Scenario | Config variable | Default | Recommendation |
|---|---|---|---|
| C1 waiting for Copilot | `REQUEST_TIMEOUT` | 180s | Keep at 180s for deep thinking mode |
| C1 WebSocket connect | `CONNECT_TIMEOUT` | 15s | 15–30s on slow networks |
| C3 Playwright page timeout | `C3_CHAT_TAB_POOL_SIZE` | 90000ms | Increase for complex M365 queries |
| Circuit breaker threshold | `CIRCUIT_BREAKER_THRESHOLD` | 5 failures | Reduce to 3 in production |
| Agent session TTL | `AGENT_SESSION_TTL` | 1800s | Increase to 3600s for long workflows |

---

## Log Viewing

```bash
# Live logs for all containers
docker compose logs -f

# Specific container logs
docker compose logs app --tail 50          # C1
docker compose logs browser-auth --tail 50 # C3
docker compose logs c9-jokes --tail 50     # C9

# C9 SQLite database — direct query
docker compose exec c9-jokes \
  python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/c9.db')
rows = conn.execute('SELECT agent_id, prompt_excerpt, elapsed_ms, source FROM chat_logs ORDER BY id DESC LIMIT 10').fetchall()
for r in rows: print(r)
conn.close()"

# View C9 logs via web UI
open http://localhost:6090/logs
```

---

## State Reset Commands

```bash
# Reset C3 PagePool (recover from Playwright crash or tab hang)
curl -X POST http://localhost:8001/pool-reset

# Clear C1 response cache
curl -X POST http://localhost:8000/v1/cache/clear   # if endpoint exists

# Restart a specific container
docker compose restart app
docker compose restart browser-auth
docker compose restart c9-jokes
docker compose restart hermes-agent

# Full reset (keeps volumes)
docker compose down && docker compose up -d

# Nuclear reset (destroys all data including SQLite + browser session)
docker compose down -v && docker compose up -d
```

---

## Failure Modes & Fixes

| Symptom | Most likely cause | Fix |
|---|---|---|
| C1 returns `401` / `403` | Cookies expired | Re-run C3 extraction flow |
| C1 returns empty `content` | Copilot returned no text | Check C3 logs; try pool-reset |
| C3 `auth_ok: false` | Browser not logged in | Login via noVNC, re-extract |
| C3 `m365_session_valid: false` | M365 session expired | Re-login in noVNC |
| C9 `/logs` shows no entries | SQLite empty | Run a chat or validate call first |
| C9 `/logs` `elapsed_ms` is null | Old schema (pre-migration) | C9 auto-migrates on startup; restart c9-jokes |
| C9 validate shows source=null | Old data before source column added | New entries will be correct; old rows have null |
| Agent container `exec` fails | Container not running | `docker compose up -d <service>` |
| C7a `curl healthz` returns 404 | OpenClaw version mismatch | Rebuild: `docker compose build openclaw-gateway` |
| C8 `hermes ask` hangs | Hermes git clone incomplete | Rebuild: `docker compose build hermes-agent` |
| Thinking mode has no effect | X-Chat-Mode not reaching C1 | Check header spelling; verify C1 logs show `chat_mode` field |
| Work/Web toggle has no effect | Consumer profile (not M365) | Only works with `COPILOT_PORTAL_PROFILE=m365_hub` |
| File upload returns 415 | Unsupported MIME type | Check `SUPPORTED_UPLOAD_MIMES` in config.py |
| File upload returns 413 | File too large | Keep under `MAX_FILE_BYTES` (default 10 MB) |
| Port conflict on startup | Another process on 8000/6090 | `lsof -i :8000` (macOS) or `ss -tlnp \| grep 8000` (Linux) |
