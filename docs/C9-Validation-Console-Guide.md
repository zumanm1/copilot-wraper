# C9 — Validation Console Guide (APP2)

> **Last updated: 2026-03-31**
> Complete reference for APP2: the C9 validation console — UI, UX, backend, database, and API.

---

## Table of Contents

- [What is C9?](#what-is-c9)
- [Quick Start](#quick-start)
- [UI Pages](#ui-pages)
  - [Dashboard](#dashboard-)
  - [Chat](#chat-)
  - [Pairs](#pairs-)
  - [Logs](#logs-)
  - [Health](#health-)
  - [Sessions](#sessions-)
  - [API reference](#api-reference-)
- [UX Flows](#ux-flows)
- [Backend Architecture](#backend-architecture)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Features In Depth](#features-in-depth)
  - [Thinking Mode](#thinking-mode)
  - [Work / Web Toggle](#work--web-toggle)
  - [File Upload](#file-upload)
  - [Parallel vs Sequential Validation](#parallel-vs-sequential-validation)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

---

## What is C9?

C9 (`c9-jokes`, port **6090**) is the **observation and validation layer** for the Copilot wrapper stack (C1–C8 backbone, plus **C10/C11 sandboxes** used by Agent and multi-Agento pages). It provides:

- A **full web UI** to interact with every AI agent without needing a terminal
- **Batch validation** — run one prompt against all agents simultaneously and compare responses
- A **complete audit log** of every AI call (chat + batch validation) with timing and source metadata
- A **health dashboard** showing live status for **all probed services** (C1–C8, **C10**, **C11**, plus an extra C3 `/status` row on `/api/status`)
- A **REST API** for automated validation pipelines

**C9 does not rewrite C1–C8 environment or C3 cookies.** It reads, proxies, and records. **C10/C11** receive workspace mutations only through C9’s documented `/api/agent/*`, `/api/ma/*`, and streaming agent endpoints.

```
Browser
  └── http://localhost:6090
        └── C9 FastAPI (c9_jokes/app.py)
              ├── Proxies chat calls → C1 :8000
              ├── Proxies file uploads → C1 :8000
              ├── Probes health → C2–C8 :8080, C7a :18789
              └── Writes all results → SQLite /app/data/c9.db
```

---

## Quick Start

```bash
# Start C9 (if not already running)
docker compose up c9-jokes -d

# Open the web UI
open http://localhost:6090          # macOS
xdg-open http://localhost:6090      # Linux

# Verify the API is working
curl http://localhost:6090/api/status
```

---

## UI Pages

### Dashboard `/`

The **landing page** — a real-time health card grid showing the status of every entry in `TARGETS` (C1–C8 plus **C10** and **C11** sandboxes).

**What you see:**
- One card per health target, colour-coded green/red/grey
- Container label, service name, health endpoint URL
- HTTP status code of the last health probe
- "Refresh" button to re-probe all containers immediately
- Overall summary line: "X / Y containers healthy"

**How it works:**
- On page load, JavaScript calls `GET /api/status`
- Each card updates based on the `ok` field in the response
- Green = HTTP 200, Red = any other status or timeout, Grey = not yet probed

---

### Chat `/chat`

**Single-agent chat interface** — send one message to one agent and see the response stream live.

**Controls:**
| Control | Location | What it does |
|---|---|---|
| Agent dropdown | Top-left | Select which agent receives the message (C2–C8, generic) |
| Thinking mode dropdown | Top | Auto / Quick Response / Think Deeper |
| Work / Web toggle | Top-right | Switch M365 data scope (M365 profile only) |
| "+" upload button | Beside input | Upload files to attach to the message |
| Prompt input | Bottom | Type your message here |
| Send button | Bottom-right | Submit the message |

**Response area:**
- Shows the agent's response text as tokens arrive from `POST /api/chat`
- Shows elapsed time in milliseconds when the stream completes
- Shows HTTP status code on completion or on error

**File attachments:**
- Click "+" → choose "📋 Add content" (paste text) or "⬆ Upload files" (file picker)
- Supported: PNG, JPG, GIF, WebP, PDF, TXT, DOCX, XLSX, PPTX
- Uploaded files appear as chips with "×" remove buttons above the input
- Files are uploaded to C1 `/v1/files` immediately; only the `file_id` is stored
- Attachments are included in the message sent to the agent

**Persistence:**
- Thinking mode preference saved to `localStorage['chatThinkingMode']`
- Agent selection not persisted (defaults to first agent on reload)
- Session history is sent back as `messages[]` on subsequent turns; `session_id` is reused when present

---

### Pairs `/pairs`

**Batch multi-agent validation** — run one prompt against multiple agents and compare responses side by side.

**Controls:**
| Control | Location | What it does |
|---|---|---|
| Thinking mode dropdown | Top | Apply one thinking depth to all agents |
| Work / Web toggle | Top-right | Apply one M365 scope to all agents |
| "+" upload button | Beside input | Attach files to all agent calls |
| Prompt input | Centre | The prompt all agents will receive |
| Mode toggle | Below input | Sequential (one at a time) or Parallel (all at once) |
| Run All button | Bottom | Start the batch run |
| Individual ▶ buttons | Each agent card | Run this agent only |

**Agent cards:**
- One card per registered agent (C2 Aider, C5 Claude Code, C6 KiloCode, C7b OpenClaw, C8 Hermes, C9 generic)
- Spinner while waiting for response
- Response text when complete
- Green border = success, Red border = error
- Elapsed time shown per card

**How parallel mode works:**
- JavaScript sends `POST /api/validate` with `parallel: true`
- C9 backend uses `asyncio.gather` to call all agents concurrently
- All responses arrive together; total time = slowest agent

**How sequential mode works:**
- C9 calls each agent one at a time
- Cards update progressively as each completes
- Total time = sum of all agents

**Persistence:**
- Thinking mode saved to `localStorage['pairsThinkingMode']`
- Mode (parallel/sequential) saved to `localStorage['pairsMode']`

---

### Logs `/logs`

**Full audit trail** of every AI call made through C9 — both interactive chat and batch validation.

**Columns:**
| Column | Description |
|---|---|
| Time | `created_at` timestamp |
| Agent | `agent_id` (e.g. `c8-hermes`) |
| Prompt | First 200 characters of the prompt |
| Response | First 500 characters of the response (or error message) |
| Status | HTTP status code from C1 |
| Time (ms) | `elapsed_ms` — round-trip time |
| Source | `chat`, `chat-stream`, or `validate` |

**Filters:**
- Agent ID dropdown — filter by specific agent
- Source filter — show only `chat`, `chat-stream`, or `validate` entries
- Pagination — next/prev buttons

**How to read the Source column:**
- `chat` — non-streaming chat request sent via `POST /api/chat`
- `chat-stream` — streaming chat request sent via `POST /api/chat` with `stream:true`
- `validate` — message was sent from the /pairs page or via `POST /api/validate`

---

### Health `/health`

**Container health snapshot history** — timestamped records of each health probe.

**What you see:**
- Table with columns: Time, Container, HTTP Status, Response body
- Last N snapshots per container
- Auto-refreshes every 15 seconds

**How it works:**
- Each `GET /api/status` call writes one row per container to `health_snapshots` table
- The `/health` page reads from `GET /api/health-history`

---

### Sessions `/sessions`

**Live proxy** of C1's `/v1/sessions` endpoint — shows all active per-agent sessions in C1.

**What you see:**
- List of active `agent_id` values
- Last-used timestamp for each session
- Session age in seconds
- Shared pool size and TTL

Useful for confirming that agent sessions were created and are still alive.

---

### API reference `/api`

**Server-rendered API reference** for C9 endpoints (template `api_reference.html`).

**Bookmark compatibility:** `GET /api/docs` redirects to `/api` (307) for older docs that used the `/api/docs` path.

Lists key `GET` / `POST` routes with curl-oriented examples.

---

## UX Flows

### Flow 1: Chat with an agent

```
User opens /chat
  └── Selects agent: "C8 Hermes Agent"
  └── Sets thinking mode: "Think Deeper"
  └── Clicks Work toggle (for M365 profile)
  └── Types: "Summarise my recent project files"
  └── Clicks Send
        └── JavaScript: POST /api/chat
              Body: {agent_id:"c8-hermes", prompt:"...", chat_mode:"deep", work_mode:"work", stream:true}
              └── C9 api_chat(stream=true) → POST http://app:8000/v1/chat/completions
                    Header: X-Agent-ID: c8-hermes
                    Header: X-Chat-Mode: deep
                    Header: X-Work-Mode: work
                    Body includes stream:true
                    └── C1 → Copilot → streaming response
              └── C9 emits SSE `token` events while text arrives
              └── C9 writes to chat_logs (source='chat-stream', elapsed_ms=N) after completion
              └── Final SSE event: {type:"done", text:"...", session_id:"...", token_estimate:N}
  └── Response text grows in the UI as tokens arrive
  └── Elapsed time shown when the stream finishes
```

### Flow 2: Upload a file and ask about it

```
User opens /chat
  └── Clicks "+" → "⬆ Upload files" → selects report.pdf
        └── JavaScript: POST /api/upload (multipart)
              └── C9 → POST http://app:8000/v1/files
                    └── C1 extracts text from PDF
                    └── Returns {file_id:"abc123", preview:"..."}
        └── Chip "report.pdf ×" appears above input
  └── Types: "What are the key findings?"
  └── Clicks Send
        └── JavaScript: POST /api/chat
              Body: {agent_id:"c8-hermes", prompt:"...", stream:true,
                     attachments:[{file_id:"abc123", filename:"report.pdf"}]}
              └── C9 api_chat(stream=true) → POST http://app:8000/v1/chat/completions
                    Content: [{type:"text", text:"What are the key findings?"},
                               {type:"file_ref", file_id:"abc123", filename:"report.pdf"}]
                    └── C1 resolves file_ref → injects PDF text into prompt → Copilot
```

### Flow 3: Validate all agents at once

```
User opens /pairs
  └── Sets thinking: "Quick Response"
  └── Types: "Tell me a short joke"
  └── Selects "Parallel" mode
  └── Clicks "Run All"
        └── JavaScript: POST /api/validate
              Body: {prompt:"Tell me a short joke", parallel:true, chat_mode:"quick"}
              └── C9 asyncio.gather → _run_one for each agent simultaneously
                    ├── POST C1 /v1/chat/completions (X-Agent-ID: c2-aider)
                    ├── POST C1 /v1/chat/completions (X-Agent-ID: c5-claude-code)
                    ├── POST C1 /v1/chat/completions (X-Agent-ID: c6-kilocode)
                    ├── POST C1 /v1/chat/completions (X-Agent-ID: c7-openclaw)
                    └── POST C1 /v1/chat/completions (X-Agent-ID: c8-hermes)
              └── All results written to chat_logs (source='validate') + pair_results
              └── Returns {run_id, results:[{agent_id, response, elapsed_ms, ok}, ...]}
  └── Each agent card updates with its response
  └── User compares responses side by side
```

---

## Backend Architecture

**File:** `c9_jokes/app.py`
**Framework:** FastAPI + Jinja2 templates
**Database:** SQLite at `/app/data/c9.db`
**HTTP client:** `httpx.AsyncClient`

### Key internal functions

```python
# Single agent non-stream chat call (used by /api/validate and /api/chat when stream=false)
async def _chat_one(agent_id, prompt, c1_url,
                    chat_mode="", attachments=None, work_mode="") -> dict:
    # Builds content blocks (text + file_ref attachments)
    # Sets X-Agent-ID, X-Chat-Mode, X-Work-Mode headers
    # POSTs to C1 /v1/chat/completions
    # Returns {ok, response, elapsed_ms, http_status, error}

# Single validation run for one agent (used by /api/validate)
async def _run_one(agent: dict) -> dict:
    # Calls _chat_one
    # Writes to chat_logs (source='validate')
    # Writes to pair_results
    # Returns result dict

# Health probe for all containers
async def _probe_all() -> dict:
    # Concurrently GETs health endpoint of each container
    # Writes each result to health_snapshots
    # Returns status dict keyed by container ID

# Database initialisation + migration
def _ensure_db(db_path: str):
    # Creates tables from schema.sql if not exist
    # Runs ALTER TABLE migrations (adds elapsed_ms, source if missing)
```

### AGENTS registry

```python
AGENTS = [
    {"id": "c2-aider",       "label": "C2 Aider (OpenAI)"},
    {"id": "c5-claude-code", "label": "C5 Claude Code (Anthropic)"},
    {"id": "c6-kilocode",    "label": "C6 KiloCode (OpenAI)"},
    {"id": "c7-openclaw",    "label": "C7b OpenClaw"},
    {"id": "c8-hermes",      "label": "C8 Hermes Agent"},
    {"id": "c9-jokes",       "label": "C9 (generic session)"},
]
```

To add a new agent: append a dict and restart C9.

### Health targets

In code this map is named **`TARGETS`** (`c9_jokes/app.py`). Each entry supplies `env`, `default`, `label`, and `health` path; `_urls()` resolves the base URL.

```python
TARGETS = {
    "c1":  {...}, "c2": {...}, "c3": {...},
    "c5":  {...}, "c6": {...}, "c7a": {...}, "c7b": {...},
    "c8":  {...},
    "c10": {"env": "C10_URL", "default": "http://c10-sandbox:8100",
            "label": "C10 agent sandbox", "health": "/health"},
    "c11": {"env": "C11_URL", "default": "http://c11-sandbox:8200",
            "label": "C11 multi-agent sandbox", "health": "/health"},
}
```

`GET /api/status` also probes **`c3-status`** (`C3_URL + /status`) and merges that into the JSON (extra row in history), while the dashboard cards follow `TARGETS` keys only.

---

## Database Schema

**File:** `c9_jokes/schema.sql`
**Location inside container:** `/app/data/c9.db`
**Persisted by:** `c9-data` Docker named volume

### `chat_logs` table

Stores every AI call made through C9 (both chat and validate).

```sql
CREATE TABLE chat_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    agent_id         TEXT    NOT NULL,
    prompt_excerpt   TEXT,        -- first 200 chars of prompt
    response_excerpt TEXT,        -- first 500 chars of response (or error message)
    http_status      INTEGER,     -- HTTP status code from C1
    elapsed_ms       INTEGER,     -- round-trip time in milliseconds
    source           TEXT DEFAULT 'chat'  -- 'chat' or 'validate'
);
```

### `validation_runs` table

One row per `/api/validate` call (the batch run metadata).

```sql
CREATE TABLE validation_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT,
    mode         TEXT,    -- 'parallel' or 'sequential'
    passed       INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    raw_summary  TEXT     -- JSON summary blob
);
```

### `pair_results` table

One row per agent per validation run (the individual results).

```sql
CREATE TABLE pair_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES validation_runs(id),
    pair_name   TEXT NOT NULL,   -- agent_id
    ok          INTEGER,         -- 1 = success, 0 = failure
    detail      TEXT,            -- response excerpt or error
    duration_ms INTEGER          -- elapsed_ms for this agent
);
```

### `health_snapshots` table

One row per container per health probe.

```sql
CREATE TABLE health_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT    NOT NULL DEFAULT (datetime('now')),
    target      TEXT    NOT NULL,    -- container ID (c1, c2, ...)
    http_status INTEGER,
    body_json   TEXT                 -- JSON response body
);
```

### Querying the database directly

```bash
# Open SQLite shell inside C9 container
docker compose exec c9-jokes python3 -c "
import sqlite3, json
conn = sqlite3.connect('/app/data/c9.db')
conn.row_factory = sqlite3.Row

# Last 10 chat calls
rows = conn.execute('''
    SELECT agent_id, prompt_excerpt, elapsed_ms, source, created_at
    FROM chat_logs ORDER BY id DESC LIMIT 10
''').fetchall()
for r in rows:
    print(dict(r))

conn.close()"
```

---

## API Reference

Full reference: [`API-DOCUMENTATION/04-api-reference.md`](../API-DOCUMENTATION/04-api-reference.md#c9-c9-jokes--app2-validation-console)

### Quick summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Health probe all containers; writes health_snapshots |
| `POST` | `/api/chat` | Single chat call to one agent; JSON by default, SSE when `stream:true` |
| `POST` | `/api/validate` | Batch run to multiple agents; writes chat_logs + pair_results |
| `POST` | `/api/upload` | Upload file to C1 `/v1/files`; returns file_id |
| `GET` | `/api/logs` | Paginated chat_logs (filterable by agent, source) |
| `GET` | `/api/health-history` | Recent health_snapshots |
| `GET` | `/api/validation-runs` | Recent validation_runs + pair_results |
| `GET` | `/api/session-health` | Proxy C3 `/session-health` |

---

## Features In Depth

### Thinking Mode

The thinking mode dropdown (Auto / Quick Response / Think Deeper) on both /chat and /pairs maps to C1's `X-Chat-Mode` header:

| Dropdown label | Sent value | X-Chat-Mode → Copilot style |
|---|---|---|
| Auto | `auto` | `smart` — balanced reasoning |
| Quick Response | `quick` | `balanced` — fast, direct answer |
| Think Deeper | `deep` | `reasoning` — step-by-step deep thinking |

**Implementation in C9 (`app.py`):**
```python
async def _chat_one(..., chat_mode="", ...):
    if chat_mode:
        headers["X-Chat-Mode"] = chat_mode
```

**Implementation in C9 (`chat.html` / `pairs.html`):**
```javascript
let thinkingMode = localStorage.getItem('chatThinkingMode') || 'auto';
// On send: body.chat_mode = thinkingMode
```

---

### Work / Web Toggle

The Work/Web button controls M365 data scope for M365 profile users:

| Button state | Sent value | X-Work-Mode → C3 action |
|---|---|---|
| Work (active) | `work` | C3 clicks "Work" tab in M365 Copilot UI |
| Web (active) | `web` | C3 clicks "Web" tab in M365 Copilot UI |

**Only has visible effect when `COPILOT_PORTAL_PROFILE=m365_hub`.**

**Implementation in C9 (`app.py`):**
```python
async def _chat_one(..., work_mode="", ...):
    if work_mode in ("work", "web"):
        headers["X-Work-Mode"] = work_mode
```

---

### File Upload

The "+" button opens a popup with two options:

1. **📋 Add content** — paste plain text directly (creates a virtual attachment)
2. **⬆ Upload files** — file picker (PNG/JPG/GIF/WebP/PDF/TXT/DOCX/XLSX/PPTX)

**Upload flow:**
```
User selects file
  └── JavaScript: POST /api/upload (multipart/form-data)
        └── C9 → POST http://app:8000/v1/files
              └── C1: validates MIME + size
                    └── Image: saves to /tmp, stores path in _file_store
                    └── Document: extracts text, stores in _file_store
              └── Returns {file_id, type, filename, preview}
  └── Chip appears: "filename.pdf ×"
  └── pendingAttachments.push({file_id, filename})

User sends message
  └── attachments: [...pendingAttachments] included in POST /api/chat or /api/validate
        └── C9 _chat_one: content = [{type:"text",text:prompt},
                                      {type:"file_ref",file_id:...,filename:...}]
              └── C1 resolves file_ref → injects document text into prompt
```

**After send:** Attachment chips cleared from UI. User must re-upload for next message.

---

### Parallel vs Sequential Validation

**Parallel mode** (default, recommended):
```python
results = await asyncio.gather(*[_run_one(agent) for agent in agents])
# All agents called simultaneously
# Total time = time of slowest agent
```

**Sequential mode:**
```python
results = []
for agent in agents:
    result = await _run_one(agent)
    results.append(result)
# One agent at a time
# Total time = sum of all agents
```

Use **sequential** when:
- Testing rate limits (one request at a time)
- Debugging a specific agent in isolation
- The M365 PagePool pool is nearly exhausted (parallel would exhaust the pool)

---

## Configuration

All environment variables are pre-wired by `docker-compose.yml`:

| Variable | Default | Description |
|---|---|---|
| `C1_URL` | `http://app:8000` | C1 API URL for chat/upload calls |
| `C2_URL` | `http://agent-terminal:8080` | C2 health probe URL |
| `C3_URL` | `http://browser-auth:8001` | C3 health probe URL |
| `C5_URL` | `http://claude-code-terminal:8080` | C5 health probe URL |
| `C6_URL` | `http://kilocode-terminal:8080` | C6 health probe URL |
| `C7A_URL` | `http://openclaw-gateway:18789` | C7a health probe URL |
| `C7B_URL` | `http://openclaw-cli:8080` | C7b health probe URL |
| `C8_URL` | `http://hermes-agent:8080` | C8 health probe URL |
| `C10_URL` | `http://c10-sandbox:8100` | C10 sandbox API (Agent workspace) |
| `C11_URL` | `http://c11-sandbox:8200` | C11 sandbox API (multi-Agento sessions) |
| `DATABASE_PATH` | `/app/data/c9.db` | SQLite database path |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/logs` empty on first visit | No calls made yet | Send a chat or run a batch validation first |
| `elapsed_ms` is null in old rows | Pre-migration schema | Restart C9 (auto-migrates); new rows will have elapsed_ms |
| `source` is null in old rows | Pre-source-column data | New rows will have source; old rows are legacy |
| File upload returns 415 | Unsupported MIME type | Use supported types (see above) |
| File upload returns 413 | File > 10 MB | Keep files under 10 MB |
| Thinking mode seems to have no effect | Consumer profile — Copilot ignores style in some modes | Verify C1 is forwarding X-Chat-Mode (check C1 logs) |
| Work/Web toggle has no effect | Consumer profile | Only works with `COPILOT_PORTAL_PROFILE=m365_hub` |
| Dashboard shows all containers red | C9 cannot reach containers (wrong URLs) | Check C*_URL env vars; verify `docker compose ps` shows containers up |
| `/pairs` spinner never stops | Agent took too long or error | Check C9 logs: `docker compose logs c9-jokes --tail 30` |
| SQLite locked | Multiple C9 instances | Ensure only one `c9-jokes` container is running |
| Database lost after restart | Volume not mounted | Verify `c9-data` named volume in `docker compose ps` |

---

## Related Guides

- [Architecture Deep-Dive](../API-DOCUMENTATION/01-architecture-deep-dive.md) — Full system architecture including C9's role
- [API Reference](../API-DOCUMENTATION/04-api-reference.md) — Complete C9 JSON API documentation
- [Testing & Validation](../API-DOCUMENTATION/06-testing-validation.md) — How to test C9 from CLI and browser
- [INSTALL.md](../INSTALL.md) — How to install and run the full stack from GitHub
