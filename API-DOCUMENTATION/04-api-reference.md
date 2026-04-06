# 04 — API Reference

> **Last updated: 2026-03-27**
> Complete endpoint reference for C1 (APP1 gateway), C3 (browser auth), and C9 (APP2 validation console).

---

## Table of Contents

- [C1: copilot-api — APP1 Gateway](#c1-copilot-api--app1-gateway)
- [C3: browser-auth — Cookie & Browser Proxy](#c3-browser-auth--cookie--browser-proxy)
- [C9: c9-jokes — APP2 Validation Console](#c9-c9-jokes--app2-validation-console)
- [Custom Request Headers Reference](#custom-request-headers-reference)
- [Environment Variables Reference](#environment-variables-reference)

---

## C1: copilot-api — APP1 Gateway

**Base URL:** `http://localhost:8000`
**Internal URL:** `http://app:8000`
**Swagger UI:** `http://localhost:8000/docs`

---

### `GET /health`

Liveness check.

```bash
curl http://localhost:8000/health
```
```json
{"status": "ok", "service": "copilot-openai-wrapper"}
```

---

### `GET /v1/models`

List available model names.

```bash
curl http://localhost:8000/v1/models
```
```json
{
  "object": "list",
  "data": [
    {"id": "copilot",          "object": "model", "owned_by": "microsoft"},
    {"id": "gpt-4",            "object": "model", "owned_by": "microsoft"},
    {"id": "gpt-4o",           "object": "model", "owned_by": "microsoft"},
    {"id": "copilot-balanced", "object": "model", "owned_by": "microsoft"},
    {"id": "copilot-creative", "object": "model", "owned_by": "microsoft"},
    {"id": "copilot-precise",  "object": "model", "owned_by": "microsoft"}
  ]
}
```

---

### `POST /v1/chat/completions` — OpenAI Format

**Used by:** C2, C6, C7, C8, C9, external clients.

**Request body:**
```json
{
  "model": "copilot",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user",   "content": "Hello!"}
  ],
  "stream": false,
  "temperature": 0.7
}
```

**With file attachment** (use after `POST /v1/files`):
```json
{
  "model": "copilot",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "Summarise this document"},
      {"type": "file_ref", "file_id": "abc123", "filename": "report.pdf"}
    ]
  }]
}
```

**With image** (base64):
```json
{
  "model": "copilot",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "What do you see in this image?"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
    ]
  }]
}
```

**Custom headers:**
```
X-Agent-ID: <string>         Isolate to named session (optional)
X-Chat-Mode: auto|quick|deep Thinking depth (optional; default: auto)
X-Work-Mode: work|web        M365 scope (optional; M365 profile only)
```

**Non-streaming response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1711574400,
  "model": "copilot",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hello! How can I help?"},
    "finish_reason": "stop",
    "suggested_responses": ["Tell me more", "What else?"]
  }],
  "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}
}
```

**Streaming (SSE):**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```
```
data: {"id":"chatcmpl-xyz","choices":[{"delta":{"content":"Hello"},"index":0}]}
data: {"id":"chatcmpl-xyz","choices":[{"delta":{"content":"!"},"index":0}]}
data: [DONE]
```

---

### `POST /v1/messages` — Anthropic Format

**Used by:** C5 (Claude Code), external Anthropic SDK clients.

**Required headers:**
```
x-api-key: not-needed
anthropic-version: 2023-06-01
```

**Request body:**
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "system": "You are a helpful assistant.",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

**Response:**
```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Hello! How can I help?"}],
  "model": "claude-sonnet-4-6",
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 15, "output_tokens": 10}
}
```

---

### `POST /v1/files` — File Upload

Upload a file to be referenced in a subsequent chat message.

**Request:** `multipart/form-data`

```bash
# Upload a PDF
curl -X POST http://localhost:8000/v1/files \
  -F "file=@report.pdf;type=application/pdf"

# Upload an image
curl -X POST http://localhost:8000/v1/files \
  -F "file=@screenshot.png;type=image/png"
```

**Supported types:**
| MIME type | Extension | Processing |
|---|---|---|
| `image/png` | .png | Saved as image, sent as base64 to Copilot |
| `image/jpeg` | .jpg | Saved as image |
| `image/gif` | .gif | Saved as image |
| `image/webp` | .webp | Saved as image |
| `application/pdf` | .pdf | Text extracted via pypdf |
| `text/plain` | .txt | Read as-is |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | .docx | Text extracted via python-docx |
| `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | .xlsx | Rows extracted via openpyxl |
| `application/vnd.ms-excel` | .xls | Rows extracted via openpyxl |
| `application/vnd.openxmlformats-officedocument.presentationml.presentation` | .pptx | Slide text extracted via python-pptx |

**Limits:** Max file size: 10 MB (`MAX_FILE_BYTES` in config.py)

**Response:**
```json
{
  "file_id": "abc123def456",
  "type": "text",
  "filename": "report.pdf",
  "size": 102400,
  "preview": "Executive Summary\nThis report covers..."
}
```

For images:
```json
{
  "file_id": "xyz789",
  "type": "image",
  "filename": "screenshot.png",
  "size": 45678,
  "preview": null
}
```

Use `file_id` in subsequent chat messages as a `file_ref` content block.

---

### `GET /v1/sessions`

List all active per-agent sessions.

```bash
curl http://localhost:8000/v1/sessions
```
```json
{
  "sessions": [
    {"agent_id": "c2-aider",       "last_used": "2026-03-27T10:30:00Z", "age_seconds": 120},
    {"agent_id": "c5-claude-code", "last_used": "2026-03-27T10:28:00Z", "age_seconds": 240},
    {"agent_id": "c8-hermes",      "last_used": "2026-03-27T10:25:00Z", "age_seconds": 420}
  ],
  "pool_size": 2,
  "ttl_seconds": 1800
}
```

---

### `POST /v1/reload-config`

Reload `.env` configuration (cookies, style, persona) without restarting C1.

```bash
curl -X POST http://localhost:8000/v1/reload-config
```
```json
{"status": "ok", "cookies_loaded": true, "provider": "m365"}
```

---

### `POST /v1/cookies/extract`

Trigger cookie extraction from C3 and reload into C1 (combines `/extract` on C3 + reload).

```bash
curl -X POST http://localhost:8000/v1/cookies/extract
```

---

### `GET /v1/debug/cookie`

Show masked cookie state (for debugging auth issues — values are truncated).

```bash
curl http://localhost:8000/v1/debug/cookie
```
```json
{"copilot_cookies_set": true, "bing_cookies_set": false, "cookie_preview": "_U=ABC1...XYZ9"}
```

---

### `GET /v1/cache/stats`

View response cache statistics.

```bash
curl http://localhost:8000/v1/cache/stats
```
```json
{"hits": 42, "misses": 18, "size": 15, "maxsize": 1000, "ttl": 300}
```

---

### Agent session endpoints

```bash
# Stateful agent session (remembers context across tasks)
POST /v1/agent/start        {"system_prompt": "You are a Python expert."}
POST /v1/agent/task         {"task": "What is 1+1?", "stream": false}
POST /v1/agent/pause
POST /v1/agent/resume
POST /v1/agent/stop
GET  /v1/agent/status
GET  /v1/agent/history
GET  /v1/agent/history/{task_id}
DELETE /v1/agent/history
```

---

## C3: browser-auth — Cookie & Browser Proxy

**Base URL:** `http://localhost:8001`
**noVNC UI:** `http://localhost:6080`
**Internal URL:** `http://browser-auth:8001`

---

### `GET /health`

```bash
curl http://localhost:8001/health
```
```json
{"status": "ok"}
```

---

### `GET /status`

Browser and pool state.

```bash
curl http://localhost:8001/status
```
```json
{
  "browser_running": true,
  "pool_size": 6,
  "tabs_open": 6,
  "tabs_busy": 1,
  "agent_tab_map": {
    "c2-aider": 0,
    "c5-claude-code": 1
  }
}
```

---

### `GET /session-health`

M365 session validity and pool health (queried by C9 dashboard).

```bash
curl http://localhost:8001/session-health
```
```json
{
  "browser_ok": true,
  "pool_size": 6,
  "tabs_available": 5,
  "tabs_busy": 1,
  "auth_ok": true,
  "m365_session_valid": true,
  "warnings": []
}
```

---

### `POST /chat` — M365 Chat Proxy

**Internal — called by C1 only.**

```bash
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello!", "agent_id": "c2-aider", "mode": "work", "timeout": 90000}'
```
```json
{"success": true, "text": "Hello! How can I help you today?", "elapsed_ms": 3421}
```

On failure:
```json
{"success": false, "error": "timeout after 90000ms"}
```

Parameters:
| Field | Type | Description |
|---|---|---|
| `prompt` | string | The prompt text |
| `agent_id` | string | Agent ID for sticky tab assignment |
| `mode` | string | `work`, `web`, or `""` |
| `timeout` | int | Milliseconds before timeout (default 90000) |

---

### `POST /extract`

Extract cookies from the active Chromium session and write to `.env`.

```bash
curl -X POST http://localhost:8001/extract
```
```json
{"status": "ok", "cookies_saved": true, "domains_walked": ["m365.cloud.microsoft", "bing.com", "copilot.microsoft.com"]}
```

---

### `POST /navigate`

Navigate the C3 browser to a URL.

```bash
curl -X POST http://localhost:8001/navigate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://copilot.microsoft.com"}'
```

---

### `POST /pool-reset`

Reinitialize all PagePool tabs. Use after a Playwright crash or network failure.

```bash
curl -X POST http://localhost:8001/pool-reset
```
```json
{"status": "ok", "tabs_reinitialized": 6}
```

---

### `GET /setup` / `POST /setup`

HTML form for configuring portal profile and URL overrides.

```bash
open http://localhost:8001/setup   # macOS — opens browser
```

POST body (form fields):
```
portal_profile=m365_hub
copilot_portal_base_url=   (optional override)
copilot_portal_api_base_url=  (optional override)
```

On POST: writes to `.env` and triggers `POST /v1/reload-config` on C1.

---

## C9: c9-jokes — APP2 Validation Console

**Base URL:** `http://localhost:6090`
**Internal URL:** `http://c9-jokes:6090`

---

### Page Routes (HTML)

| Route | Page | Description |
|---|---|---|
| `GET /` | Dashboard | Health card grid for all containers |
| `GET /chat` | Chat | Single-agent chat with live streaming, thinking mode, and file upload |
| `GET /pairs` | Pairs | Batch multi-agent validation |
| `GET /logs` | Logs | Full audit trail (chat + validate history) |
| `GET /health` | Health | Container health snapshots |
| `GET /sessions` | Sessions | Live proxy of C1 `/v1/sessions` |
| `GET /api` | API reference | Server-rendered endpoint reference (primary) |
| `GET /api/docs` | API reference (alias) | **307 → `/api`** — bookmark compatibility |

---

### `GET /api/status`

Probe all containers and return health dict. Also writes a snapshot to `health_snapshots` table.

```bash
curl http://localhost:6090/api/status
```
```json
{
  "c1": {"ok": true,  "status": 200, "label": "C1 copilot-api",    "url": "http://app:8000/health"},
  "c2": {"ok": true,  "status": 200, "label": "C2 agent-terminal", "url": "http://agent-terminal:8080/health"},
  "c3": {"ok": true,  "status": 200, "label": "C3 browser-auth",   "url": "http://browser-auth:8001/health"},
  "c5": {"ok": false, "status": 0,   "label": "C5 claude-code",    "url": "http://claude-code-terminal:8080/health"},
  "c6": {"ok": true,  "status": 200, "label": "C6 kilocode",       "url": "http://kilocode-terminal:8080/health"},
  "c7a":{"ok": true,  "status": 200, "label": "C7a openclaw-gw",   "url": "http://openclaw-gateway:18789/healthz"},
  "c7b":{"ok": true,  "status": 200, "label": "C7b openclaw-cli",  "url": "http://openclaw-cli:8080/health"},
  "c8": {"ok": true,  "status": 200, "label": "C8 hermes-agent",   "url": "http://hermes-agent:8080/health"},
  "ts": "2026-03-27T10:30:00Z"
}
```

---

### `POST /api/chat`

Send a single chat message to one agent via C1. By default this returns JSON. If the request body includes `"stream": true`, C9 returns `text/event-stream` with C9-specific SSE events while preserving the same `messages[]`, attachments, and session handling.

```bash
curl -X POST http://localhost:6090/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "c8-hermes",
    "prompt": "What is 2+2?",
    "chat_mode": "auto",
    "work_mode": "web",
    "attachments": []
  }'
```
```json
{
  "ok": true,
  "response": "2+2 equals 4.",
  "elapsed_ms": 2341,
  "agent_id": "c8-hermes",
  "http_status": 200
}
```

Streaming example:

```bash
curl -N -X POST http://localhost:6090/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "c8-hermes",
    "messages": [{"role":"user","content":"What is 2+2?"}],
    "stream": true
  }'
```

Example SSE events:

```text
data: {"type":"token","text":"2"}
data: {"type":"token","text":"+2 equals 4."}
data: {"type":"done","text":"2+2 equals 4.","session_id":"cs_ab12cd34","token_estimate":18,"http_status":200}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `agent_id` | string | yes | Agent to send the message to |
| `prompt` | string | yes* | The user message text |
| `chat_mode` | string | no | `auto`, `quick`, or `deep` |
| `work_mode` | string | no | `work` or `web` |
| `attachments` | array | no | List of `{file_id, filename}` objects |
| `messages` | array | no | Full multi-turn message history; used when continuing a C9 chat session |
| `session_id` | string | no | Existing C9 chat session identifier; generated if omitted |
| `stream` | boolean | no | When `true`, returns SSE with `token`, `done`, and `error` events |

`prompt` or `messages` is required. Successful JSON chats are logged with `source='chat'`; successful streaming chats are logged with `source='chat-stream'`.

---

### `POST /api/validate`

Run a prompt against multiple agents (batch validation).

```bash
curl -X POST http://localhost:6090/api/validate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Tell me a short joke",
    "agent_ids": ["c2-aider", "c8-hermes"],
    "chat_mode": "quick",
    "work_mode": "",
    "parallel": true,
    "attachments": []
  }'
```
```json
{
  "run_id": 42,
  "mode": "parallel",
  "prompt_excerpt": "Tell me a short joke",
  "results": [
    {"agent_id": "c2-aider",  "ok": true,  "response": "Why did the...", "elapsed_ms": 2100},
    {"agent_id": "c8-hermes", "ok": true,  "response": "What do you...", "elapsed_ms": 2800}
  ],
  "passed": 2,
  "failed": 0,
  "total_ms": 2800
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | yes | The prompt to send to all agents |
| `agent_ids` | array | no | Specific agents; omit to use all registered agents |
| `chat_mode` | string | no | `auto`, `quick`, or `deep` |
| `work_mode` | string | no | `work` or `web` |
| `parallel` | boolean | no | `true` = asyncio.gather, `false` = sequential |
| `attachments` | array | no | Shared attachments for all agents |

Logged to `chat_logs` (source='validate') and `pair_results` tables.

---

### `POST /api/upload`

Upload a file to C1 `/v1/files`. Returns `file_id` for use in subsequent chat or validate calls.

```bash
curl -X POST http://localhost:6090/api/upload \
  -F "file=@document.pdf;type=application/pdf"
```
```json
{
  "ok": true,
  "file_id": "abc123def456",
  "filename": "document.pdf",
  "type": "text",
  "size": 102400,
  "preview": "Executive Summary\nThis report covers..."
}
```

---

### `GET /api/logs`

Return paginated chat + validation log history.

```bash
curl "http://localhost:6090/api/logs?limit=20&offset=0&agent_id=c8-hermes"
```
```json
{
  "logs": [
    {
      "id": 101,
      "created_at": "2026-03-27T10:30:00Z",
      "agent_id": "c8-hermes",
      "prompt_excerpt": "What is 2+2?",
      "response_excerpt": "2+2 equals 4.",
      "http_status": 200,
      "elapsed_ms": 2341,
      "source": "chat"
    }
  ],
  "total": 101,
  "limit": 20,
  "offset": 0
}
```

Query parameters:
| Param | Type | Description |
|---|---|---|
| `limit` | int | Max records to return (default 50) |
| `offset` | int | Pagination offset |
| `agent_id` | string | Filter by agent ID |
| `source` | string | Filter by `chat` or `validate` |

---

### `GET /api/health-history`

Return recent health snapshots.

```bash
curl "http://localhost:6090/api/health-history?n=10"
```
```json
[
  {
    "captured_at": "2026-03-27T10:30:00Z",
    "target": "c1",
    "http_status": 200,
    "body_json": {"status": "ok"}
  }
]
```

---

### `GET /api/validation-runs`

Return recent validation run summaries.

```bash
curl "http://localhost:6090/api/validation-runs?limit=10"
```
```json
[
  {
    "id": 42,
    "started_at": "2026-03-27T10:30:00Z",
    "finished_at": "2026-03-27T10:30:05Z",
    "mode": "parallel",
    "passed": 5,
    "failed": 1,
    "pair_results": [
      {"pair_name": "c2-aider",  "ok": true,  "duration_ms": 2100, "detail": "Why did the..."},
      {"pair_name": "c8-hermes", "ok": false, "duration_ms": 0,    "detail": "timeout"}
    ]
  }
]
```

---

### `GET /api/session-health`

Proxy C3 `/session-health` (used by C9 dashboard LED indicator).

```bash
curl http://localhost:6090/api/session-health
```
Returns the same JSON as `GET http://localhost:8001/session-health`.

---

## Custom Request Headers Reference

| Header | Applies to | Values | Description |
|---|---|---|---|
| `X-Agent-ID` | C1 `/v1/chat/completions`, `/v1/messages` | Any string | Route to isolated named session |
| `X-Chat-Mode` | C1 `/v1/chat/completions`, `/v1/messages` | `auto`, `quick`, `deep` | Thinking depth (maps to Copilot style) |
| `X-Work-Mode` | C1 `/v1/chat/completions`, `/v1/messages` | `work`, `web` | M365 data scope (forwarded to C3) |
| `x-api-key` | C1 `/v1/messages` | `not-needed` | Required by Anthropic SDK (value ignored) |
| `anthropic-version` | C1 `/v1/messages` | `2023-06-01` | Required by Anthropic SDK |
| `Authorization` | C1 (all) | `Bearer <API_KEY>` | Only if `API_KEY` is set in `.env` |

---

## Environment Variables Reference

### C1 (`app` service)

| Variable | Default | Description |
|---|---|---|
| `COPILOT_COOKIES` | — | Full browser cookie string |
| `BING_COOKIES` | — | Fallback `_U` cookie value |
| `COPILOT_PORTAL_PROFILE` | `consumer` | `consumer` or `m365_hub` |
| `COPILOT_PROVIDER` | `auto` | `copilot`, `m365`, or `auto` |
| `COPILOT_STYLE` | `balanced` | Default thinking style |
| `REQUEST_TIMEOUT` | `180` | Seconds before Copilot call times out |
| `CONNECT_TIMEOUT` | `15` | WebSocket connect timeout |
| `RATE_LIMIT` | `100/minute` | Rate limit for all C1 endpoints |
| `API_KEY` | — | Optional auth key for C1 |
| `AGENT_SESSION_TTL` | `1800` | Per-agent session idle timeout (seconds) |
| `POOL_WARM_COUNT` | `2` | Pre-warm N shared pool backends at startup |
| `MAX_FILE_BYTES` | `10485760` | Max upload file size (10 MB) |

### C3 (`browser-auth` service)

| Variable | Default | Description |
|---|---|---|
| `API1_URL` | `http://app:8000` | C1 URL for triggering reload |
| `VNC_RESOLUTION` | `1280x1024x24` | noVNC display resolution |
| `C3_CHAT_TAB_POOL_SIZE` | `6` | Number of Playwright tabs in PagePool |

### C9 (`c9-jokes` service)

| Variable | Default | Description |
|---|---|---|
| `C1_URL` | `http://app:8000` | C1 FastAPI URL |
| `C2_URL` | `http://agent-terminal:8080` | C2 health probe URL |
| `C3_URL` | `http://browser-auth:8001` | C3 health probe URL |
| `C5_URL` | `http://claude-code-terminal:8080` | C5 health probe URL |
| `C6_URL` | `http://kilocode-terminal:8080` | C6 health probe URL |
| `C7A_URL` | `http://openclaw-gateway:18789` | C7a health probe URL |
| `C7B_URL` | `http://openclaw-cli:8080` | C7b health probe URL |
| `C8_URL` | `http://hermes-agent:8080` | C8 health probe URL |
| `DATABASE_PATH` | `/app/data/c9.db` | SQLite database path |
