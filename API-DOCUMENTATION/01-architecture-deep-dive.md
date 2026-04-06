# 01 — Architecture Deep-Dive

> **Last updated: 2026-03-27**
> Complete system architecture for the Copilot OpenAI-Compatible API Wrapper — all 9 containers, communication flows, protocols, and layer descriptions.

---

## Table of Contents

- [System Overview](#system-overview)
- [APP1 — The API & Agent Layer (C1–C8)](#app1--the-api--agent-layer-c1c8)
  - [C1: copilot-api — The Gateway](#c1-copilot-api--the-gateway)
  - [C2: agent-terminal — Aider + OpenCode](#c2-agent-terminal--aider--opencode)
  - [C3: browser-auth — M365 Browser Proxy](#c3-browser-auth--m365-browser-proxy)
  - [C5: claude-code-terminal — Claude Code CLI](#c5-claude-code-terminal--claude-code-cli)
  - [C6: kilocode-terminal — KiloCode CLI](#c6-kilocode-terminal--kilocode-cli)
  - [C7a: openclaw-gateway — WebSocket Hub](#c7a-openclaw-gateway--websocket-hub)
  - [C7b: openclaw-cli — OpenClaw TUI](#c7b-openclaw-cli--openclaw-tui)
  - [C8: hermes-agent — Persistent Memory Agent](#c8-hermes-agent--persistent-memory-agent)
- [APP2 — The Validation Console (C9)](#app2--the-validation-console-c9)
- [Communication Flows](#communication-flows)
- [Protocol Reference](#protocol-reference)
- [Internal Docker Networking](#internal-docker-networking)
- [Named Volumes](#named-volumes)

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  APP1 — API & Agent Layer                                               │
│                                                                         │
│  External Client (curl / OpenAI SDK / Anthropic SDK / any agent)       │
│     │  POST /v1/chat/completions  (OpenAI format)                       │
│     │  POST /v1/messages          (Anthropic format)                    │
│     ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  C1: copilot-api  :8000                                         │   │
│  │  FastAPI — session registry — response cache — circuit breaker  │   │
│  │  X-Agent-ID routing → per-agent CopilotBackend instances        │   │
│  │  X-Chat-Mode → thinking depth (auto/quick/deep)                 │   │
│  │  X-Work-Mode → M365 scope (work/web)                            │   │
│  └────────────────────┬────────────────────────────────────────────┘   │
│                        │                                                │
│          ┌─────────────┴──────────────┐                                │
│          │ consumer profile           │ m365_hub profile               │
│          ▼                            ▼                                │
│  Direct WebSocket              C3: browser-auth  :8001                 │
│  copilot.microsoft.com         Playwright + PagePool(6)                │
│  (TLS + Cookie header)         → m365.cloud.microsoft/chat             │
│                                → SignalR WS (substrate.office.com)     │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────┐      │
│  │  AI Agent Containers — all route through C1                   │      │
│  │                                                               │      │
│  │  C2  agent-terminal      OpenAI /v1/chat/completions          │      │
│  │  C5  claude-code         Anthropic /v1/messages               │      │
│  │  C6  kilocode-terminal   OpenAI /v1/chat/completions          │      │
│  │  C7a openclaw-gateway    OpenAI /v1/chat/completions :18789   │      │
│  │  C7b openclaw-cli        via C7a gateway                      │      │
│  │  C8  hermes-agent        OpenAI /v1/chat/completions          │      │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  APP2 — Validation Console                                              │
│                                                                         │
│  C9: c9-jokes  :6090                                                    │
│  FastAPI + Jinja2 + SQLite                                              │
│  ├─ Dashboard    /            health cards (C1–C8, C10, C11)              │
│  ├─ Chat         /chat        single-agent chat UI                      │
│  ├─ Pairs        /pairs       batch multi-agent validation              │
│  ├─ Logs         /logs        full audit trail (source + elapsed_ms)    │
│  ├─ Health       /health      container health snapshots                │
│  ├─ Agent / Multi / multi-Agento  IDE flows → C10 / C11 sandboxes      │
│  └─ API reference /api       api_reference.html (/api/docs → redirect)  │
│                                                                         │
│  Connects to C1 (/v1/chat/completions, /v1/files)                       │
│  Probes C2–C8, C10, C11 health endpoints                                │
│  Writes all calls to SQLite c9.db                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## APP1 — The API & Agent Layer (C1–C8)

### Container Map

| Container | Service Name | Port(s) | Base Image | Role |
|---|---|---|---|---|
| C1 | `app` | **8000** (host+internal) | python:3.11-slim | FastAPI gateway — OpenAI + Anthropic API |
| C2 | `agent-terminal` | 8080 (internal health) | python:3.11-slim | Aider + OpenCode coding agents |
| C3 | `browser-auth` | **6080** noVNC, **8001** API | ubuntu:22.04 | Headless Chrome + Playwright + cookie extraction |
| C5 | `claude-code-terminal` | 8080 (internal health) | node:20-alpine | Claude Code CLI |
| C6 | `kilocode-terminal` | 8080 (internal health) | node:20-alpine | KiloCode CLI |
| C7a | `openclaw-gateway` | **18789** (host+internal) | node:22-alpine | OpenClaw WebSocket gateway |
| C7b | `openclaw-cli` | 8080 (internal health) | node:22-alpine | OpenClaw CLI / TUI |
| C8 | `hermes-agent` | 8080 (internal health) | python:3.11-slim | Hermes persistent-memory agent |
| CT | `test` | — | playwright/python:jammy | Automated test runner (ephemeral) |

---

### C1: copilot-api — The Gateway

**Files:** `server.py`, `copilot_backend.py`, `config.py`, `models.py`, `circuit_breaker.py`

C1 is the **single inference gateway** for the entire stack. Every AI call from every agent goes through C1. It translates standard OpenAI and Anthropic API formats into Microsoft Copilot protocol.

#### What C1 Provides

| Surface | Endpoint | Format | Used by |
|---|---|---|---|
| OpenAI chat | `POST /v1/chat/completions` | OpenAI | C2, C6, C7, C8, C9, external clients |
| Anthropic messages | `POST /v1/messages` | Anthropic | C5 (Claude Code) |
| File upload | `POST /v1/files` | multipart/form-data | C9 (proxied), direct callers |
| Agent sessions | `GET /v1/sessions` | JSON | C9, monitoring |
| Config reload | `POST /v1/reload-config` | JSON | C3 (post-cookie-extract) |
| Health | `GET /health` | JSON | All containers, monitoring |
| Models list | `GET /v1/models` | OpenAI | clients |
| Swagger | `GET /docs` | HTML | developers |

#### Routing: Two Copilot Providers

C1 selects a backend provider based on `COPILOT_PORTAL_PROFILE` (or `COPILOT_PROVIDER`):

```
COPILOT_PORTAL_PROFILE=consumer  →  direct WebSocket to copilot.microsoft.com
COPILOT_PORTAL_PROFILE=m365_hub  →  C3 browser proxy → M365 Copilot
```

| Provider | Path | Auth mechanism |
|---|---|---|
| `consumer` | C1 → WSS → `copilot.microsoft.com` | `COPILOT_COOKIES` in Cookie header |
| `m365` | C1 → POST C3 `/chat` → Playwright → M365 | Browser session in C3 |

#### Per-Agent Session Registry

Every request may include `X-Agent-ID`. C1 uses this to route to a **dedicated `CopilotBackend` instance**:

```
X-Agent-ID: c2-aider       →  _agent_sessions["c2-aider"]      → CopilotBackend A
X-Agent-ID: c5-claude-code →  _agent_sessions["c5-claude-code"] → CopilotBackend B
X-Agent-ID: c8-hermes      →  _agent_sessions["c8-hermes"]      → CopilotBackend C
(no header)                →  shared connection pool
```

- Each agent gets **isolated conversation history** — they never see each other's context
- Sessions expire after `AGENT_SESSION_TTL` seconds idle (default: 1800s / 30 min)
- A background `_session_reaper()` task cleans up expired sessions
- Two-level lock (registry lock → per-ID lock) ensures concurrent agents never block each other

#### Thinking Mode (X-Chat-Mode)

The `X-Chat-Mode` header controls **thinking depth** — how deeply Copilot reasons before responding:

| X-Chat-Mode value | Copilot style | Description |
|---|---|---|
| `auto` (or absent) | `smart` | Default — balanced reasoning |
| `quick` | `balanced` | Fast response, minimal reasoning |
| `deep` | `reasoning` | Deep thinking mode (o1-style step-by-step) |

C9 sets this via its thinking mode dropdown. Direct callers set it as a request header.

#### Work Mode (X-Work-Mode)

The `X-Work-Mode` header scopes the **conversation context** for M365 users:

| X-Work-Mode value | Meaning |
|---|---|
| `work` | Ground response in M365 enterprise data (SharePoint, Teams, email) |
| `web` | Ground response in public web search |
| (absent) | Default Copilot behavior |

This header is forwarded to C3 as the `mode` field in the `/chat` request body.

#### Model Mapping

| Model name in request | Copilot style |
|---|---|
| `copilot`, `gpt-4`, `gpt-4o`, `gpt-4-turbo` | `smart` |
| `copilot-balanced` | `balanced` |
| `copilot-creative` | `creative` |
| `copilot-precise` | `precise` |
| `o1`, `o1-mini` | `reasoning` |

When both model name and `X-Chat-Mode` are provided, `X-Chat-Mode` takes precedence.

#### File Upload (`POST /v1/files`)

C1 accepts file uploads for use in subsequent chat messages:

1. Client POSTs `multipart/form-data` to `/v1/files`
2. C1 validates MIME type (images, PDF, TXT, DOCX, XLSX, PPTX) and size (≤ 10 MB)
3. **Images:** saved to `/tmp`, stored in `_file_store[file_id]` as `{type: "image", image_path}`
4. **Documents:** text extracted via `extract_document_text()`, stored as `{type: "text", text}`
5. Returns `{file_id, type, filename, size, preview}`

The `file_id` is then included in a subsequent chat message as a `file_ref` content block:
```json
{"type": "file_ref", "file_id": "abc123", "filename": "report.pdf"}
```

C1 resolves `file_ref` → text/image at request time before forwarding to Copilot.

**Supported file types:**
- Images: `image/png`, `image/jpeg`, `image/gif`, `image/webp`
- Documents: `application/pdf`, `text/plain`, `.docx`, `.xlsx`, `.xls`, `.pptx`

#### Response Cache + In-flight Deduplication

```
TTLCache(maxsize=1000, ttl=300s)
Cache key: sha256(style + ":" + agent_id + ":" + prompt)

In-flight dedup: if two requests with identical key arrive concurrently,
the second waits for the first's asyncio.Future — only one Copilot call is made.
```

#### Circuit Breaker

`circuit_breaker.py` wraps every `_raw_copilot_call`. After `CIRCUIT_BREAKER_THRESHOLD` consecutive failures the circuit opens and all calls fail-fast with 503. After `CIRCUIT_BREAKER_TIMEOUT` seconds it half-opens and allows one probe call.

---

### C2: agent-terminal — Aider + OpenCode

**Files:** `Dockerfile.agent`, `agent-terminal/start.sh`, `agent-terminal/.aider.conf.yml`, `agent-terminal/opencode.json`

**Base image:** `python:3.11-slim`
**Agent ID:** `c2-aider`
**API format:** OpenAI `POST /v1/chat/completions`
**Connects to:** `http://app:8000/v1`

#### What C2 Does

C2 provides two AI coding agents in one container:

| Tool | Purpose | Interaction |
|---|---|---|
| **Aider** | Autonomous code editing — reads files, applies diffs | `docker compose exec agent-terminal aider` |
| **OpenCode** | Interactive coding TUI | `docker compose exec agent-terminal opencode` |

Both tools are pre-configured to point to C1 via `OPENAI_API_BASE=http://app:8000/v1` and use model `openai/copilot`.

#### How to Interact with C2

```bash
# Interactive Aider session (edits files in /workspace)
docker compose exec agent-terminal aider

# Interactive OpenCode TUI
docker compose exec agent-terminal opencode

# One-shot question via ask_helper
docker compose exec agent-terminal ask "Refactor this function to use list comprehensions"

# Or via C1 directly (OpenAI format)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c2-aider" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello from C2"}]}'
```

#### How C2 Communicates with C1

```
C2 agent-terminal
  └── aider / opencode / ask_helper.py
        └── OPENAI_API_BASE=http://app:8000/v1
              └── POST /v1/chat/completions
                    Header: X-Agent-ID: c2-aider
                    Body: {model, messages, stream}
                    └── C1 → CopilotBackend["c2-aider"] → Copilot
```

The shared `/workspace` volume means Aider can read and write files that are also accessible from C5, C6, C7b, and C8.

---

### C3: browser-auth — M365 Browser Proxy

**Files:** `Dockerfile.browser`, `browser_auth/server.py`, `browser_auth/cookie_extractor.py`, `browser_auth/entrypoint.sh`

**Base image:** `ubuntu:22.04`
**Ports:** 6080 (noVNC), 8001 (FastAPI API)
**Connects to:** `http://app:8000` (to trigger reload)

#### What C3 Does

C3 has two distinct roles:

| Role | How | When |
|---|---|---|
| **Cookie extractor** | Playwright logs into Copilot, extracts session cookies → writes to `.env` | One-time setup; re-run when cookies expire |
| **M365 browser proxy** | Playwright types prompts into M365 Copilot web UI, intercepts SignalR WS response | Every M365-profile chat request |

#### The PagePool

C3 maintains a **pool of 6 pre-authenticated Playwright browser tabs**, each navigated to `https://m365.cloud.microsoft/chat`. Tabs are **sticky-assigned** to agent IDs on first use:

```
PagePool (size=6, configured by C3_CHAT_TAB_POOL_SIZE)
  Tab 1 → assigned "c2-aider"       on first request from C2
  Tab 2 → assigned "c5-claude-code" on first request from C5
  Tab 3 → assigned "c6-kilocode"
  Tab 4 → assigned "c7-openclaw"
  Tab 5 → assigned "c8-hermes"
  Tab 6 → assigned "c9-jokes" / generic
```

Each agent always gets the same tab → separate M365 conversation threads.

#### C3 `/chat` Request Flow

```
POST http://browser-auth:8001/chat
Body: { "prompt": "...", "agent_id": "c2-aider", "mode": "work" }
  │
  ├─ PagePool.acquire("c2-aider") → get sticky Tab 1
  ├─ Health check: is tab still on M365 URL?
  ├─ Auth dialog check: click "Continue" if M365 sign-in prompt appears
  ├─ Fast reset: click "New chat" button
  │     fallback: full page.goto() if fast reset fails
  ├─ Attach WebSocket interceptor (BEFORE typing)
  │     → captures SignalR frames from substrate.office.com
  ├─ Set Work/Web mode toggle (if mode=work or mode=web)
  ├─ Type prompt into [role="textbox"] composer
  ├─ Dispatch Enter key (React synthetic event)
  ├─ Wait for SignalR type=2 frame (bot response completion)
  │     fallback: DOM polling for response text
  └─ Return { "success": true, "text": "..." }
```

#### M365 SignalR Protocol Detail

```
WebSocket: wss://substrate.office.com/m365Copilot/Chathub/
Delimiter: \x1e  (ASCII record separator)
Frame types:
  type=1  → keep-alive / ping
  type=2  → completion — contains bot response text
  type=6  → typing indicator
  type=7  → error

C3 splits on \x1e, JSON.parse each segment, extracts
  frame.arguments[0].messages[0].text  (or similar path)
from type=2 frames.
```

#### Cookie Extraction Flow

```
1. User opens http://localhost:6080 (noVNC)
2. Logs into copilot.microsoft.com (or m365.cloud.microsoft) inside the browser
3. curl -X POST http://localhost:8001/extract
4. C3 Playwright walks: m365.cloud.microsoft → bing.com → copilot.microsoft.com
5. Extracts cookies from all domains → merges into COPILOT_COOKIES string
6. Writes to mounted .env file
7. POSTs http://app:8000/v1/reload-config → C1 picks up new cookies
```

---

### C5: claude-code-terminal — Claude Code CLI

**Files:** `Dockerfile.claude-code`, `claude-code-terminal/start.sh`

**Base image:** `node:20-alpine`
**Agent ID:** `c5-claude-code`
**API format:** Anthropic `POST /v1/messages`
**Connects to:** `http://app:8000` (as `ANTHROPIC_BASE_URL`)

#### What C5 Does

C5 runs the official Anthropic **Claude Code CLI** (`@anthropic-ai/claude-code`), pre-configured to route all calls to C1 instead of Anthropic's real API. This gives you Claude Code's powerful agentic coding capabilities powered by Copilot.

Pre-seeded credentials bypass Anthropic auth entirely (`~/.claude/credentials.json`, `~/.claude/settings.json`). The `ANTHROPIC_API_KEY` env var is set to a placeholder (`sk-ant-not-needed-...`).

#### How to Interact with C5

```bash
# Start interactive Claude Code session
docker compose exec claude-code-terminal claude

# One-shot ask
docker compose exec claude-code-terminal ask "Explain this Python file"

# Or directly via C1 Anthropic endpoint
curl -X POST http://localhost:8000/v1/messages \
  -H "x-api-key: not-needed" \
  -H "anthropic-version: 2023-06-01" \
  -H "X-Agent-ID: c5-claude-code" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":1024,"messages":[{"role":"user","content":"Hello"}]}'
```

#### How C5 Communicates with C1

```
C5 claude-code-terminal
  └── claude CLI
        └── ANTHROPIC_BASE_URL=http://app:8000
              └── POST /v1/messages
                    Header: x-api-key: not-needed
                    Header: anthropic-version: 2023-06-01
                    Header: X-Agent-ID: c5-claude-code
                    Body: {model, max_tokens, messages, system}
                    └── C1 → _anthropic_messages_to_prompt() → CopilotBackend["c5-claude-code"] → Copilot
```

**Note:** C1 truncates the Claude Code system prompt to 500 characters to avoid C3 page timeouts. The first 500 chars of the system prompt are sent; the rest is dropped.

---

### C6: kilocode-terminal — KiloCode CLI

**Files:** `Dockerfile.kilocode`, `kilocode-server/entrypoint.sh`

**Base image:** `node:20-alpine`
**Agent ID:** `c6-kilocode`
**API format:** OpenAI `POST /v1/chat/completions`
**Connects to:** `http://app:8000/v1` (as `OPENAI_API_BASE`)

#### What C6 Does

C6 runs **KiloCode** (`@kilocode/cli`), a terminal-first AI coding assistant. Like C2 (Aider), it edits files autonomously in `/workspace`. KiloCode uses the OpenAI-compatible endpoint on C1.

#### How to Interact with C6

```bash
# Start interactive KiloCode session
docker compose exec kilocode-terminal kilocode

# One-shot ask
docker compose exec kilocode-terminal ask "Write unit tests for calculator.py"

# Or directly via C1
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c6-kilocode" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello from C6"}]}'
```

#### How C6 Communicates with C1

```
C6 kilocode-terminal
  └── kilocode / ask_helper.py
        └── OPENAI_API_BASE=http://app:8000/v1
              └── POST /v1/chat/completions
                    Header: X-Agent-ID: c6-kilocode
                    └── C1 → CopilotBackend["c6-kilocode"] → Copilot
```

---

### C7a: openclaw-gateway — WebSocket Hub

**Files:** `Dockerfile.openclaw-gw`, `openclaw-gateway/entrypoint.sh`

**Base image:** `node:22-alpine`
**Agent ID:** `c7-openclaw`
**Port:** 18789 (WebSocket + HTTP)
**Connects to:** `http://app:8000/v1` (as provider)

#### What C7a Does

C7a is the **OpenClaw gateway** — a WebSocket hub that multiplexes AI requests from multiple OpenClaw CLI clients (C7b instances) through a single connection to C1. It maintains a persistent connection pool to C1 and handles authentication via `OPENCLAW_GATEWAY_TOKEN`.

```
C7b (CLI) ──WS──► C7a :18789 (gateway) ──HTTP──► C1 :8000 (Copilot API)
```

#### How to Interact with C7a

```bash
# Check gateway health
curl http://localhost:18789/healthz

# C7a is mainly accessed via C7b or OpenClaw SDK clients
# Direct WebSocket connection:
wscat -c ws://localhost:18789 -H "Authorization: Bearer copilot-local-gateway-token"
```

---

### C7b: openclaw-cli — OpenClaw TUI

**Files:** `Dockerfile.openclaw-cli`, `openclaw-cli/start.sh`

**Base image:** `node:22-alpine`
**Agent ID:** `c7-openclaw`
**Connects to:** `ws://openclaw-gateway:18789` (C7a)

#### What C7b Does

C7b is the **OpenClaw CLI / TUI** — an interactive terminal interface for the OpenClaw AI agent framework. It connects to C7a (the gateway), which proxies all requests to C1.

#### How to Interact with C7b

```bash
# Start interactive OpenClaw TUI
docker compose exec openclaw-cli openclaw

# One-shot ask
docker compose exec openclaw-cli ask "Summarize this codebase"
```

#### C7b → C7a → C1 Flow

```
C7b openclaw-cli
  └── openclaw CLI
        └── OPENCLAW_GATEWAY_URL=ws://openclaw-gateway:18789
              └── WS connection + OPENCLAW_GATEWAY_TOKEN
                    └── C7a gateway
                          └── POST http://app:8000/v1/chat/completions
                                Header: X-Agent-ID: c7-openclaw
```

---

### C8: hermes-agent — Persistent Memory Agent

**Files:** `Dockerfile.hermes`, `hermes-agent/start.sh`, `hermes-agent/hermes-config.yaml`

**Base image:** `python:3.11-slim`
**Agent ID:** `c8-hermes`
**API format:** OpenAI `POST /v1/chat/completions`
**Connects to:** `http://app:8000/v1`

#### What C8 Does

C8 runs **Hermes Agent** (NousResearch, v2026.3.17) — a sophisticated AI agent with:

| Feature | Description |
|---|---|
| **Persistent memory** | Remembers facts, context, and past conversations across restarts |
| **Skills** | Custom reusable workflows saved as YAML |
| **Cron scheduling** | Schedule recurring tasks |
| **MCP support** | Model Context Protocol tool integration |
| **Terminal execution** | Runs shell commands via mini-swe-agent |

Persistent state is stored in the `hermes-config` Docker named volume at `/root/.hermes`.

#### How to Interact with C8

```bash
# Interactive Hermes TUI
docker compose exec hermes-agent hermes

# One-shot question
docker compose exec hermes-agent hermes ask "What files have been modified today?"

# Status check
docker compose exec hermes-agent hermes status

# Start a new session
docker compose exec hermes-agent hermes new

# Via ask_helper (also persists to conversation history)
docker compose exec hermes-agent ask "Remember: the project deadline is Friday"
```

#### How C8 Communicates with C1

```
C8 hermes-agent
  └── hermes CLI (Python + uv venv)
        └── OPENAI_BASE_URL=http://app:8000/v1
              └── POST /v1/chat/completions
                    Header: X-Agent-ID: c8-hermes
                    Body: {model: "copilot", messages, ...}
                    └── C1 → CopilotBackend["c8-hermes"] → Copilot
```

---

## APP2 — The Validation Console (C9)

**Files:** `Dockerfile.c9-jokes`, `c9_jokes/app.py`, `c9_jokes/schema.sql`, `c9_jokes/templates/`, `c9_jokes/static/`

**Base image:** `python:3.11-slim`
**Port:** 6090
**Service name:** `c9-jokes`

> See **[`docs/C9-Validation-Console-Guide.md`](../docs/C9-Validation-Console-Guide.md)** for the full APP2 guide covering UI, UX, backend, database, and API.

### What C9 Does

C9 is the **observation and validation layer** for the entire stack. It:
- Connects to **every other container** (C1–C8) over the internal Docker network
- Provides a **full web UI** for interacting with all agents without needing a terminal
- Stores **every AI call** (chat + validation) in SQLite with timing and source metadata
- Never modifies C1–C8 — reads and proxies only

### C9 Architecture Summary

```
Browser → http://localhost:6090
  │
  ▼
C9 FastAPI (c9_jokes/app.py)
  │
  ├─ Page routes (Jinja2 HTML)
  │    /              → dashboard.html  (health cards)
  │    /chat          → chat.html       (single-agent chat)
  │    /pairs         → pairs.html      (batch multi-agent)
  │    /logs          → logs.html       (audit trail)
  │    /health        → health.html     (health snapshots)
  │    /sessions      → sessions.html   (C1 session proxy)
  │    /api           → api_reference.html  (GET /api/docs → 307 /api)
  │
  ├─ JSON + SSE API routes
  │    POST /api/chat        → proxies to C1 /v1/chat/completions; JSON by default, SSE when stream:true
  │    POST /api/validate    → runs prompt against N agents (parallel/sequential)
  │    POST /api/upload      → proxies to C1 /v1/files
  │    GET  /api/status      → probes all containers, writes health_snapshots
  │    GET  /api/logs        → returns chat_logs from SQLite
  │    GET  /api/health-history → returns health_snapshots from SQLite
  │    GET  /api/validation-runs → returns validation_runs + pair_results
  │    GET  /api/session-health  → proxies C3 /session-health
  │
  └─ SQLite database: /app/data/c9.db (persisted in c9-data named volume)
       Tables: chat_logs, validation_runs, pair_results, health_snapshots
```

### C9 Features

| Feature | Description |
|---|---|
| **Thinking mode** | Dropdown: Auto / Quick Response / Think Deeper → sends `X-Chat-Mode` to C1 |
| **Work / Web toggle** | Scopes M365 context → sends `X-Work-Mode` to C1 |
| **File upload** | "+" button → uploads to C1 `/v1/files` → attaches `file_id` to message |
| **Live token streaming** | `/chat` reads `POST /api/chat` as SSE and renders assistant text incrementally |
| **Batch validation** | Run one prompt against multiple agents simultaneously |
| **Parallel mode** | All agents run concurrently via `asyncio.gather` |
| **Source tracking** | Logs distinguish `chat` vs `validate` calls |
| **Elapsed time** | Every log entry records `elapsed_ms` |

---

## Communication Flows

### Flow 1: Simple Chat (Consumer Profile)

```
User (curl / SDK)
  └── POST http://localhost:8000/v1/chat/completions
        Header: X-Agent-ID: c2-aider
        Header: X-Chat-Mode: deep
        Body: {model:"copilot", messages:[...]}
              │
              ▼
        C1 server.py
          ├─ extract_user_prompt(messages) → flat prompt
          ├─ resolve_chat_style_with_mode(model, temp, "deep") → style="reasoning"
          ├─ _get_or_create_agent_session("c2-aider") → CopilotBackend A
          └─ backend.chat_completion(prompt, style="reasoning")
               ├─ Check TTLCache (miss)
               ├─ Check in-flight dict (not present)
               └─ _raw_copilot_call()  → provider="copilot"
                    └─ WSS copilot.microsoft.com
                         Cookie: {COPILOT_COOKIES}
                         Send: {"text": prompt, "style": "reasoning"}
                         Receive: appendText events → full response
                         │
                         ▼
                    Return response text
              │
              ▼
        C1 builds ChatCompletionResponse
          {choices[0].message.content = "..."}
              │
              ▼
        HTTP 200 JSON  ← User receives response
```

### Flow 2: M365 Chat via C3

```
C1 _raw_copilot_call()  → provider="m365"
  └─ _c3_proxy_call()
       └─ POST http://browser-auth:8001/chat
            Body: {prompt, agent_id, mode: "work"}
              │
              ▼
       C3 browser_auth/server.py
         └─ PagePool.acquire("c2-aider") → Tab 1
              └─ _browser_chat_on_page(page, prompt, mode="work")
                   ├─ Set Work toggle active
                   ├─ Attach WS interceptor (SignalR listener)
                   ├─ Type prompt into [role="textbox"]
                   ├─ Press Enter
                   └─ Wait for SignalR type=2 frame
                        └─ Extract text from frame.arguments[0].messages[0].text
              │
              ▼
       Return {success: true, text: "..."}  → C1 → client
```

### Flow 3: C9 Batch Validation (Pairs)

```
User browser → POST http://localhost:6090/api/validate
  Body: {prompt, agent_ids:["c2-aider","c5-claude-code","c8-hermes"],
         chat_mode:"deep", work_mode:"web", parallel:true}
  │
  ▼
C9 app.py api_validate()
  ├─ Create validation_runs record in SQLite
  └─ asyncio.gather(_run_one for each agent)  [parallel mode]
       │
       ├─ _run_one("c2-aider")
       │    └─ _chat_one("c2-aider", prompt, chat_mode="deep", work_mode="web")
       │         └─ POST http://app:8000/v1/chat/completions
       │              Header: X-Agent-ID: c2-aider
       │              Header: X-Chat-Mode: deep
       │              Header: X-Work-Mode: web
       │              Body: {model:"copilot", messages:[{role:"user",content:prompt}]}
       │              → C1 → Copilot → response
       │         └─ Write to chat_logs (source='validate', elapsed_ms=N)
       │         └─ Write to pair_results
       │
       ├─ _run_one("c5-claude-code")  [concurrent]
       └─ _run_one("c8-hermes")       [concurrent]
  │
  ▼
Return {run_id, results: [{agent_id, response, elapsed_ms, ok}, ...]}
```

### Flow 4: File Upload + Chat

```
User browser → POST http://localhost:6090/api/upload
  Body: multipart/form-data  (file: report.pdf)
  │
  ▼
C9 app.py api_upload()
  └─ Forward to POST http://app:8000/v1/files
       └─ C1 validates MIME + size
            └─ extract_document_text(report.pdf) → text string
            └─ _file_store["abc123"] = {type:"text", text:"...", filename:"report.pdf"}
            └─ Return {file_id:"abc123", type:"text", preview:"..."}
  │
  ▼
C9 returns {ok:true, file_id:"abc123", filename:"report.pdf"}

User browser → POST http://localhost:6090/api/chat
  Body: {agent_id:"c8-hermes", prompt:"Summarise this document",
         attachments:[{file_id:"abc123", filename:"report.pdf"}],
         stream:true}
  │
  ▼
C9 api_chat(stream=true)
  └─ POST http://app:8000/v1/chat/completions
       Body: {model:"copilot", stream:true, messages:[{
         role:"user",
         content:[
           {type:"text", text:"Summarise this document"},
           {type:"file_ref", file_id:"abc123", filename:"report.pdf"}
         ]
       }]}
       └─ C1 extract_user_prompt():
            for part in content:
              if part.type == "file_ref":
                entry = _file_store["abc123"]
                inject "[Attached file: report.pdf]\n{entry.text}" into prompt
       └─ → Copilot receives full prompt + document text
  └─ C9 streams `token` events to the browser as chunks arrive
  └─ On completion, C9 persists the full reply + session metadata and emits `done`
```

---

## Protocol Reference

### OpenAI-Compatible Endpoint (`POST /v1/chat/completions`)

**Request:**
```json
{
  "model": "copilot",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 0.7
}
```

**Custom headers:**
```
X-Agent-ID: <agent-id>     Session isolation (optional; uses pool if absent)
X-Chat-Mode: auto|quick|deep   Thinking depth (optional; default: auto)
X-Work-Mode: work|web          M365 scope (optional; M365 profile only)
```

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1711574400,
  "model": "copilot",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hello! How can I help?"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}
}
```

### Anthropic-Compatible Endpoint (`POST /v1/messages`)

**Request:**
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "system": "You are a helpful assistant.",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

**Required headers:**
```
x-api-key: not-needed
anthropic-version: 2023-06-01
X-Agent-ID: c5-claude-code   (optional)
X-Chat-Mode: deep            (optional)
```

**Response:** Standard Anthropic `Message` object with `content[0].text`.

### C3 Chat Protocol (`POST /chat`)

Internal — called by C1 only:
```json
Request:  { "prompt": "...", "agent_id": "c2-aider", "mode": "work", "timeout": 90000 }
Response: { "success": true, "text": "...", "elapsed_ms": 4321 }
          { "success": false, "error": "timeout after 90s" }
```

---

## Internal Docker Networking

All services join **`copilot-net`** (Docker bridge). Internal DNS uses service names:

| Service name | Container | Port | Used by |
|---|---|---|---|
| `app` | C1 copilot-api | 8000 | C2, C5, C6, C7a, C8, C9 |
| `browser-auth` | C3 browser-auth | 8001 | C1 (M365 proxy) |
| `agent-terminal` | C2 | 8080 | C9 (health probe) |
| `claude-code-terminal` | C5 | 8080 | C9 (health probe) |
| `kilocode-terminal` | C6 | 8080 | C9 (health probe) |
| `openclaw-gateway` | C7a | 18789 | C7b, C9 (health probe) |
| `openclaw-cli` | C7b | 8080 | C9 (health probe) |
| `hermes-agent` | C8 | 8080 | C9 (health probe) |
| `c9-jokes` | C9 | 6090 | — (external only) |

**Host-accessible ports** (mapped in docker-compose.yml):
```
localhost:8000  → C1 API
localhost:6080  → C3 noVNC
localhost:8001  → C3 cookie API
localhost:18789 → C7a gateway
localhost:6090  → C9 console
```

---

## Named Volumes

| Volume | Container | Mount | Purpose |
|---|---|---|---|
| `copilot-browser-profile` | C3 | `/browser-profile` | Persistent browser session (login state) |
| `openclaw-config` | C7a | `/root/.openclaw` | Gateway config + token storage |
| `hermes-config` | C8 | `/root/.hermes` | Memories, skills, sessions, cron jobs |
| `c9-data` | C9 | `/app/data` | SQLite database (all chat/validation logs) |

Bind mounts (host → container):
- `.env` → C1 `/app/.env`, C3 `/app/.env` (shared config)
- `./workspace` → C2/C5/C6/C7b/C8 `/workspace` (shared file area)
- `${HOME}/Library/Application Support/Google/Chrome` → C1 `/chrome-data:ro` (macOS; Linux path: `~/.config/google-chrome`)
