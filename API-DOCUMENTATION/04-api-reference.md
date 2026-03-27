# 04 — API Reference

## C1: copilot-api  `http://localhost:8000`

---

### POST /v1/chat/completions

OpenAI-compatible chat completion. Used by all agents except C5 (Claude Code).

**Request headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | yes | `application/json` |
| `X-Agent-ID` | recommended | Agent session tag (e.g. `c10-myagent`). Creates isolated backend. |
| `X-Chat-Mode` | optional | `work` (default) or `web`. Controls M365 Copilot tab mode. |

**Request body:**
```json
{
  "model": "copilot",
  "messages": [
    {"role": "system", "content": "Optional system prompt"},
    {"role": "user",   "content": "Tell me a joke"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": null
}
```

**Accepted model IDs:**

| Model ID | Copilot style |
|----------|--------------|
| `copilot` | smart (default) |
| `gpt-4` | smart |
| `gpt-4o` | smart |
| `copilot-balanced` | balanced |
| `copilot-creative` | creative |
| `copilot-precise` | precise |

**Non-streaming response:**
```json
{
  "id": "chatcmpl-1774553566",
  "object": "chat.completion",
  "created": 1774553566,
  "model": "copilot",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Sure! Here's a joke..."},
    "finish_reason": "stop",
    "suggested_responses": []
  }],
  "usage": {"prompt_tokens": 4, "completion_tokens": 52, "total_tokens": 56},
  "system_fingerprint": null
}
```

**Streaming response (SSE):**
```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant","content":""},...}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{"content":"Sure!"},...}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop","suggested_responses":[]}]}

data: [DONE]
```

**Error response (HTTP 500):**
```json
{"detail": "C3 /chat failed: Authentication required on m365.cloud.microsoft..."}
```

**curl example:**
```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c10-myagent" \
  -H "X-Chat-Mode: work" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello"}],"stream":false}' \
  --max-time 360
```

---

### POST /v1/messages

Anthropic-compatible chat completion. Used by C5 (Claude Code).

**Request body:**
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Hello"}],
  "system": "Optional system prompt (truncated to 500 chars internally)",
  "max_tokens": 4096,
  "stream": false
}
```

**Response:**
```json
{
  "id": "msg_1774553566",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Hello! How can I help?"}],
  "model": "claude-3-5-sonnet-20241022",
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 4, "output_tokens": 12}
}
```

---

### GET /v1/models

Returns the list of supported model IDs.

```bash
curl http://localhost:8000/v1/models
```

---

### GET /health

Liveness check.

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "copilot-openai-wrapper"}
```

---

### GET /v1/sessions

List all active per-agent backend sessions.

```bash
curl http://localhost:8000/v1/sessions
```
```json
{
  "sessions": {
    "c2-aider": {"connected": true, "idle_seconds": 42},
    "c10-myagent": {"connected": true, "idle_seconds": 5}
  },
  "total": 2,
  "ttl_seconds": 1800
}
```

---

### GET /v1/cache/stats

Response cache hit/miss counters.

```bash
curl http://localhost:8000/v1/cache/stats
```
```json
{"hits": 12, "misses": 48, "size": 6, "maxsize": 1000, "ttl_seconds": 300}
```

---

## C3: browser-auth  `http://localhost:8001`

---

### GET /health

```bash
curl http://localhost:8001/health
# {"status": "ok", "service": "browser-auth"}
```

---

### GET /status

Pool and browser state.

```bash
curl http://localhost:8001/status
```
```json
{
  "status": "ok",
  "browser": "running",
  "open_pages": 7,
  "pool_size": 6,
  "pool_available": 6,
  "pool_initialized": true
}
```

---

### GET /session-health

M365 session validity + pool warnings.

```bash
curl http://localhost:8001/session-health
```
```json
{
  "session": "active",
  "profile": "m365_hub",
  "reason": null,
  "checked_at": "2026-03-27T09:00:00Z",
  "pool_warning": null,
  "chat_mode": "work"
}
```

`session` values: `active` | `expired` | `unknown`  
`pool_warning` values: `null` | `"pool_exhausted"`

---

### POST /chat

Send a prompt through a PagePool tab. Called by C1 internally.

```bash
curl -s -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me a joke", "agent_id": "c10-myagent", "mode": "work"}'
```
```json
{"success": true, "text": "Sure! Here's a joke..."}
```

---

### POST /extract

Trigger cookie extraction from the browser session.

```bash
curl -X POST http://localhost:8001/extract
```

---

### POST /navigate

Navigate the browser to a URL (for manual login flows).

```bash
curl -X POST http://localhost:8001/navigate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://m365.cloud.microsoft/chat"}'
```

---

### POST /pool-reset

Reinitialize the PagePool. Use after DNS failures at startup.

```bash
curl -X POST http://localhost:8001/pool-reset
```

---

### GET /setup  /  POST /setup

HTML form to configure `COPILOT_PORTAL_PROFILE` and URL overrides. Available at `http://localhost:8001/setup`.

---

## C9: Validation Console  `http://localhost:6090`

---

### GET /

Dashboard home page (HTML).

---

### GET /pairs

Pairs validation table — all agents, status, response preview (HTML).

---

### POST /api/validate

Run all agents in parallel with a test prompt.

```bash
curl -s -X POST http://localhost:6090/api/validate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me a joke", "chat_mode": "work"}' \
  --max-time 600
```
```json
{
  "run_id": 112,
  "mode": "web-parallel",
  "started_at": "2026-03-27T09:00:00Z",
  "finished_at": "2026-03-27T09:00:32Z",
  "wall_ms": 31500,
  "passed": 6,
  "failed": 0,
  "total": 6,
  "prompt": "Tell me a joke",
  "results": [
    {
      "agent_id": "c2-aider",
      "label": "C2 Aider (OpenAI)",
      "ok": true,
      "http_status": 200,
      "text": "Sure! Here's a joke...",
      "elapsed_ms": 31499,
      "error": null
    }
  ]
}
```

---

### GET /api/status

Container health status for all known containers.

```bash
curl http://localhost:6090/api/status
```

---

## Environment Variables Reference (C1)

| Variable | Default | Purpose |
|----------|---------|---------|
| `COPILOT_PROVIDER` | `auto` | `auto` / `m365` / `copilot` |
| `COPILOT_PORTAL_PROFILE` | `m365_hub` | `m365_hub` / `consumer` |
| `COPILOT_COOKIES` | — | Session cookies (extracted by C3) |
| `REQUEST_TIMEOUT` | `180` | Seconds before C1 times out upstream calls |
| `AGENT_SESSION_TTL` | `1800` | Seconds before idle agent session is reaped |
| `POOL_WARM_COUNT` | `2` | Backends pre-warmed in shared pool at startup |
| `CIRCUIT_BREAKER_THRESHOLD` | `50` | Failures before circuit opens |
| `RATE_LIMIT` | `100/minute` | slowapi rate limit per IP |
| `C3_URL` | `http://browser-auth:8001` | C3 internal URL (used by C1 for M365 proxy) |

## Environment Variables Reference (C3)

| Variable | Default | Purpose |
|----------|---------|---------|
| `C3_CHAT_TAB_POOL_SIZE` | `6` | Number of pre-warmed PagePool tabs |
| `VNC_RESOLUTION` | `1280x1024x24` | Xvfb display resolution |
| `API1_URL` | `http://app:8000` | C1 URL (for config reload calls) |
