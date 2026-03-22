# Copilot OpenAI-Compatible API Wrapper

> **Use Microsoft Copilot with any OpenAI or Anthropic client — eight containerised services, zero configuration conflicts.**

---

## Table of Contents

1. [Main Goal](#1-main-goal)
2. [How It Works](#2-how-it-works)
3. [Container Reference](#3-container-reference)
4. [Quick Start](#4-quick-start)
5. [Cookie Authentication (C3)](#5-cookie-authentication-c3)
6. [API Reference](#6-api-reference)
7. [Agent Management API](#7-agent-management-api)
8. [AI Agent Containers](#8-ai-agent-containers)
9. [Testing](#9-testing)
10. [Configuration Reference](#10-configuration-reference)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Main Goal

Microsoft Copilot is a powerful AI assistant with no official public API. This project **reverse-engineers the Copilot WebSocket protocol** and wraps it in two fully compatible REST APIs:

- **OpenAI-compatible** — `/v1/chat/completions` works with any OpenAI SDK, LangChain, AutoGen, Open WebUI, Aider, OpenCode, KiloCode, and more
- **Anthropic-compatible** — `/v1/messages` works with Claude Code, the Anthropic SDK, and any client that targets Claude's API

Eight Docker containers make up the full stack:

```
Your App / OpenAI SDK / Claude Code / Hermes / ...
        │
        ▼
┌───────────────────────────────────────────────┐
│  C1: FastAPI Server  (port 8000)              │
│  /v1/chat/completions  (OpenAI format)        │
│  /v1/messages          (Anthropic format)     │
│  /v1/agent/*           (stateful sessions)    │
└──────────────────────┬────────────────────────┘
                       │  WebSocket (WSS)
                       ▼
            copilot.microsoft.com
                       ▲
         C3 browser-auth feeds session cookies
```

---

## 2. How It Works

### Request Flow

1. A client sends a request to C1 (`POST /v1/chat/completions` or `POST /v1/messages`)
2. C1 (`server.py`) validates the request using Pydantic models and extracts cookies from the environment (originally obtained by C3)
3. `CopilotBackend` (`copilot_backend.py`) opens a **WebSocket connection** to `copilot.microsoft.com`, authenticates with the session cookie, and sends the prompt
4. The response streams back token-by-token
5. C1 re-formats it into the requested API schema (OpenAI SSE or Anthropic chunks) and returns it to the caller

### Per-Agent Session Routing

Every request can include an `X-Agent-ID` header. C1 uses this to route the request to a **dedicated backend session** — so C2 (Aider), C5 (Claude Code), C6 (KiloCode), C7b (OpenClaw CLI), and C8 (Hermes) each maintain their own isolated conversation history even when running concurrently.

```
X-Agent-ID: c2-aider      → session pool slot A
X-Agent-ID: c5-claude-code → session pool slot B
X-Agent-ID: c8-hermes      → session pool slot C
```

### Cookie Flow (C3 → C1)

C3 runs a headless Chromium browser with a noVNC remote display. You log into `copilot.microsoft.com` inside that browser, then trigger cookie extraction via the C3 API. The extracted cookies are written to the shared `.env` file which C1 reads.

```
Browser (noVNC :6080) → login → C3 extracts cookies → .env → C1 reloads
```

---

## 3. Container Reference

| Container | Name | Image | Port(s) | Purpose |
|---|---|---|---|---|
| C1 | `C1_copilot-api` | `copilot-api:latest` | `8000` | FastAPI — OpenAI + Anthropic API |
| C3 | `C3_browser-auth` | `copilot-browser-auth:latest` | `6080`, `8001` | Cookie extraction via headless Chrome |
| C2 | `C2_agent-terminal` | `copilot-agent-terminal:latest` | `8080` (health) | Aider + OpenCode AI agent terminal |
| C5 | `C5_claude-code` | `copilot-claude-code-terminal:latest` | `8080` (health) | Claude Code CLI (Anthropic format) |
| C6 | `C6_kilocode` | `copilot-kilocode-terminal:latest` | `8080` (health) | KiloCode CLI terminal |
| C7a | `C7a_openclaw-gateway` | `copilot-openclaw-gateway:latest` | `18789` | OpenClaw gateway (WebSocket hub) |
| C7b | `C7b_openclaw-cli` | `copilot-openclaw-cli:latest` | `8080` (health) | OpenClaw CLI / TUI |
| C8 | `C8_hermes-agent` | `copilot-hermes-agent:latest` | `8080` (health) | Hermes Agent (memory, skills, cron) |
| CT | `CT_tests` | `copilot-openai-wrapper-test:latest` | — | Playwright automated test suite |

### Architecture Diagram

```
Host Machine
└── Browser / OpenAI SDK / curl
        │ REST / SSE
        ▼
┌────────────────────────────────────────────────────────────────────┐
│  Docker Network: copilot-net                                       │
│                                                                    │
│  C3 browser-auth ──cookies──► C1 copilot-api ──WSS──► Copilot    │
│  :6080 (noVNC)               :8000                                 │
│  :8001 (Cookie API)          /v1/chat/completions                  │
│                              /v1/messages                          │
│                              /v1/agent/*                           │
│                                    ▲                               │
│  C2 agent-terminal ────OpenAI /v1──┤                               │
│  C5 claude-code ───Anthropic /v1───┤                               │
│  C6 kilocode ──────OpenAI /v1──────┤                               │
│  C7a openclaw-gateway ─OpenAI /v1──┤  :18789                      │
│  C7b openclaw-cli ────OpenAI /v1───┤                               │
│  C8 hermes-agent ─────OpenAI /v1───┘                               │
│                                                                    │
│  CT tests ─────────HTTP tests──────► C1                            │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. Quick Start

> **Prerequisite:** Docker Desktop installed and running.

### Step 1 — Clone the repository

```bash
git clone https://github.com/zumanm1/copilot-wraper.git
cd copilot-openai-wrapper
```

### Step 2 — Configure

```bash
cp .env.example .env
# .env will be populated automatically by C3 in Step 3
# Or manually: set BING_COOKIES=<your _U cookie value>
```

### Step 3 — Start the core stack (C1 + C3)

```bash
# Start API server and browser-auth together (recommended)
docker compose up app browser-auth -d

# Verify C1 is healthy
curl http://localhost:8000/health
# → {"status":"ok","service":"copilot-openai-wrapper"}
```

### Step 4 — Authenticate via browser (C3)

```bash
# Open the noVNC browser — log in to copilot.microsoft.com inside it
open http://localhost:6080

# Once logged in, extract cookies
curl -X POST http://localhost:8001/extract
# → {"status":"ok","message":"Cookies extracted and saved"}

# Reload C1 config so it picks up the new cookies
curl -X POST http://localhost:8000/v1/reload-config
```

### Step 5 — Send your first request

```bash
# OpenAI format
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello!"}]}'

# Anthropic format
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: not-needed" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":512,"messages":[{"role":"user","content":"Hello!"}]}'
```

### Start all agent containers

```bash
# Start the full stack (C1, C3, C2, C5, C6, C7a, C7b, C8)
docker compose up -d

# Check all containers are healthy
docker compose ps
```

### Stop everything

```bash
docker compose down
```

---

## 5. Cookie Authentication (C3)

C3 (`C3_browser-auth`) runs a **headless Chromium browser inside Docker** with a noVNC remote display, so you can log into Microsoft Copilot interactively and have your session cookies extracted automatically. This avoids the need to manually copy cookies from your host browser.

### How to use C3

```bash
# 1. Start C3 (if not already running)
docker compose up browser-auth -d

# 2. Open the browser UI in your host browser
open http://localhost:6080
# or: http://localhost:6080/vnc_auto.html

# 3. In the noVNC window, navigate to https://copilot.microsoft.com
#    and log in with your Microsoft account

# 4. Extract and save cookies to .env
curl -X POST http://localhost:8001/extract
# → {"status":"ok","cookies_saved":true}

# 5. Reload C1 to pick up the new cookies
curl -X POST http://localhost:8000/v1/reload-config

# 6. Verify C1 has valid cookies
curl http://localhost:8000/v1/debug/cookie
```

### C3 API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | C3 health check |
| `/status` | GET | Browser status (open pages, cookies present) |
| `/extract` | POST | Extract cookies from active Chromium session |
| `/cookies` | GET | View currently extracted cookies |

### Manual cookie fallback

If you prefer to extract cookies manually:

1. Open **https://copilot.microsoft.com** in your host browser and sign in
2. Press F12 → Application → Cookies → `https://copilot.microsoft.com`
3. Copy the `_U` cookie value
4. Add it to `.env`: `BING_COOKIES=<value>`
5. Restart or reload: `curl -X POST http://localhost:8000/v1/reload-config`

> The cookie expires periodically. If you get 401/404 errors from Copilot, re-run the C3 extraction flow.

---

## 6. API Reference

### `GET /health`

```bash
curl http://localhost:8000/health
```
```json
{"status": "ok", "service": "copilot-openai-wrapper"}
```

---

### `GET /v1/models`

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

### `POST /v1/chat/completions` — OpenAI format

**Non-streaming:**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "What is 1 + 1?"}]
  }'
```

**Streaming (SSE):**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "Tell me a joke."}],
    "stream": true
  }'
```

**Python (OpenAI SDK):**
```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed"
)

response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

**With image (multimodal):**
```python
import base64, openai

client = openai.OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

with open("image.png", "rb") as f:
    img = base64.b64encode(f.read()).decode()

response = client.chat.completions.create(
    model="copilot",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "What do you see?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
        ]
    }]
)
print(response.choices[0].message.content)
```

**Per-agent session isolation (`X-Agent-ID`):**
```bash
# Each agent ID gets its own isolated conversation history in C1
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: my-custom-agent" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Remember my name is Alice."}]}'
```

---

### `POST /v1/messages` — Anthropic format

C1 exposes an Anthropic-compatible endpoint so tools like Claude Code can use Copilot as their backend without any code changes.

```bash
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: not-needed" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Explain Docker in one sentence."}]
  }'
```

**Python (Anthropic SDK):**
```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8000",
    api_key="not-needed"
)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
print(message.content[0].text)
```

---

### `GET /v1/sessions`

List all active per-agent sessions and their backend connection status.

```bash
curl http://localhost:8000/v1/sessions
```

---

### `POST /v1/cookies/extract`

Trigger cookie extraction from C3 (browser-auth) and reload them into C1.

```bash
curl -X POST http://localhost:8000/v1/cookies/extract
```

---

### `POST /v1/reload-config`

Reload `.env` configuration (cookies, style, persona) without restarting C1.

```bash
curl -X POST http://localhost:8000/v1/reload-config
```

---

### `GET /v1/cache/stats`

View response cache statistics (hit rate, size).

```bash
curl http://localhost:8000/v1/cache/stats
```

---

### Swagger UI

All endpoints are browsable and testable at:

```
http://localhost:8000/docs
```

---

## 7. Agent Management API

The `/v1/agent/*` endpoints provide a **stateful, persistent conversation session** with Copilot. Unlike `/v1/chat/completions` (which is stateless), the agent remembers all previous tasks in the session.

### Agent State Machine

```
  STOPPED ──► RUNNING ──► PAUSED
                │ ▲           │
                │ └─ resume ──┘
                ▼
              BUSY ──► RUNNING (task complete)
                │
                └──► STOPPED (via /stop)
```

### `POST /v1/agent/start`

```bash
curl -X POST http://localhost:8000/v1/agent/start \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "You are a Python expert."}'
```
```json
{
  "session_id": "agent-efe7f8be5d75",
  "status": "running",
  "started_at": "2026-03-22T12:00:00Z",
  "message": "Agent started successfully."
}
```

### `POST /v1/agent/task`

```bash
# Non-streaming
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "What is 1 + 1?"}'

# Streaming
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "Explain quantum computing.", "stream": true}'
```

### Other agent endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/agent/pause` | POST | Pause — new tasks rejected with 409 |
| `/v1/agent/resume` | POST | Resume a paused agent |
| `/v1/agent/stop` | POST | Stop agent and close Copilot connection |
| `/v1/agent/status` | GET | Current status + task counts |
| `/v1/agent/history` | GET | Full task history for this session |
| `/v1/agent/history/{id}` | GET | Specific task by ID |
| `/v1/agent/history` | DELETE | Clear task history (agent stays running) |

### Python walkthrough

```python
import requests

BASE = "http://localhost:8000"

# Start
requests.post(f"{BASE}/v1/agent/start", json={})

# Give it a task
resp = requests.post(f"{BASE}/v1/agent/task", json={"task": "What is 1+1?"})
print(resp.json()["result"])

# Follow-up — agent remembers context!
resp = requests.post(f"{BASE}/v1/agent/task", json={"task": "Multiply that by 10."})
print(resp.json()["result"])

# Done
requests.post(f"{BASE}/v1/agent/stop")
```

---

## 8. AI Agent Containers

All agent containers (C2, C5, C6, C7a, C7b, C8) run in **standby mode** by default: a lightweight health server listens on port 8080 so Docker can report them as healthy. You attach interactively with `docker compose exec` or run one-shot commands with `docker compose run --rm`.

### C2 — Agent Terminal (Aider + OpenCode)

**Image:** `copilot-agent-terminal:latest`  
**Backend format:** OpenAI `/v1/chat/completions`  
**Tools:** Aider 0.86.2, OpenCode 1.2.27

```bash
# Interactive menu (choose Aider, OpenCode, or bash)
docker compose run --rm agent-terminal

# Launch Aider directly (coding agent, edits files)
docker compose run --rm agent-terminal aider

# Launch OpenCode directly
docker compose run --rm agent-terminal opencode

# One-shot ask
docker compose run --rm agent-terminal ask "How do I reverse a list in Python?"

# Drop into bash shell with ask/status/calc helpers
docker compose run --rm agent-terminal bash

# Health check
curl http://localhost:8080/health   # (from inside the Docker network)
```

Aider is pre-configured with:
```
OPENAI_API_BASE=http://app:8000/v1
AIDER_MODEL=openai/copilot
```

OpenCode reads its config from `/root/.config/opencode/opencode.json` which points to C1.

---

### C5 — Claude Code Terminal

**Image:** `copilot-claude-code-terminal:latest`  
**Backend format:** Anthropic `/v1/messages`  
**Tool:** Claude Code 2.1.81

Claude Code is Anthropic's official CLI. C5 routes it through C1's `/v1/messages` endpoint via `ANTHROPIC_BASE_URL`.

```bash
# Interactive Claude Code CLI
docker compose run --rm claude-code-terminal claude

# One-shot ask (Anthropic format)
docker compose run --rm claude-code-terminal ask "Write a Dockerfile for a FastAPI app"

# Status check
docker compose run --rm claude-code-terminal status

# Bash shell
docker compose run --rm claude-code-terminal bash
```

Configuration:
```
ANTHROPIC_BASE_URL=http://app:8000
ANTHROPIC_API_KEY=sk-ant-not-needed-xxxxxxxxxxxxx
```

---

### C6 — KiloCode Terminal

**Image:** `copilot-kilocode-terminal:latest`  
**Backend format:** OpenAI `/v1/chat/completions`  
**Tool:** KiloCode 7.1.0

```bash
# Interactive KiloCode CLI
docker compose run --rm kilocode-terminal kilo

# One-shot ask
docker compose run --rm kilocode-terminal ask "Explain async/await in Python"

# Bash shell
docker compose run --rm kilocode-terminal bash
```

Configuration:
```
OPENAI_API_BASE=http://app:8000/v1
KILO_MODEL=copilot
```

---

### C7a — OpenClaw Gateway

**Image:** `copilot-openclaw-gateway:latest`  
**Tool:** OpenClaw 2026.3.13  
**Port:** `18789` (WebSocket gateway)

C7a is a self-hosted OpenClaw gateway that bridges the OpenClaw protocol to C1. It starts in **standby mode** because OpenClaw requires interactive onboarding before it can run as a service. The standby health server keeps Docker happy while you complete setup.

```bash
# Interactive onboarding (required once)
docker compose exec openclaw-gateway openclaw onboard

# Start the gateway manually after onboarding
docker compose exec openclaw-gateway openclaw gateway run

# Health check (standby mode)
curl http://localhost:18789/
# → {"status":"standby","openclaw":"2026.3.13","port":18789}
```

---

### C7b — OpenClaw CLI / TUI

**Image:** `copilot-openclaw-cli:latest`  
**Tool:** OpenClaw 2026.3.13

C7b is the companion CLI for C7a, plus a direct-ask mode that talks to C1 without going through the gateway.

```bash
# OpenClaw TUI (connects to C7a gateway)
docker compose run --rm openclaw-cli tui

# One-shot ask (direct to C1)
docker compose run --rm openclaw-cli ask "What is the capital of France?"

# Gateway status
docker compose run --rm openclaw-cli status

# Bash shell
docker compose run --rm openclaw-cli bash
```

---

### C8 — Hermes Agent

**Image:** `copilot-hermes-agent:latest`  
**Tool:** Hermes Agent v0.3.0 (2026.3.17) by Nous Research  
**Backend format:** OpenAI `/v1/chat/completions`

Hermes is a **long-lived personal AI agent** with persistent memory, installable skills, a built-in cron scheduler, and MCP (Model Context Protocol) support. C8 routes all inference through C1 using `HERMES_INFERENCE_PROVIDER=openai` and `OPENAI_BASE_URL=http://app:8000/v1`.

Hermes features available in C8:
- Persistent memory across sessions (stored in the `hermes-config` Docker volume)
- Installable and improvable skills (`/skills`)
- Built-in cron job scheduling (`/cron`)
- MCP tool protocol support
- Context compression for long conversations

```bash
# Interactive Hermes CLI (persistent memory, skills, cron)
docker compose exec C8_hermes-agent hermes

# One-shot ask (fast, via ask_helper → C1)
docker compose run --rm hermes-agent ask "Summarise the Copilot API wrapper project"

# One-shot via Hermes native path
docker compose run --rm hermes-agent hermes-chat "List all my skills"

# Health and diagnostics
docker compose run --rm hermes-agent status

# Bash shell
docker compose run --rm hermes-agent bash
```

Inside the Hermes CLI:
```
/memory list     — list remembered facts
/skills list     — list installed skills
/skills install  — install a skill from the Skills Hub
/cron list       — list scheduled jobs
/tools           — list available tools
/exit            — exit the session
```

Configuration:
```
OPENAI_BASE_URL=http://app:8000/v1
OPENAI_API_KEY=not-needed
HERMES_INFERENCE_PROVIDER=openai
LLM_MODEL=copilot
TERMINAL_ENV=local
HERMES_HOME=/root/.hermes   (persisted via Docker volume)
```

---

## 9. Testing

### Pair Integration Tests (`tests/run_pair_tests.sh`)

Validates all 7 agent-container ↔ C1+C3 pairs, both sequentially and in parallel.

```bash
# Sequential (shows full output per test)
bash tests/run_pair_tests.sh

# Parallel (all 7 tests at once — ~30 seconds)
bash tests/run_pair_tests.sh --parallel
```

| Test | Pair | API Format | Tool |
|---|---|---|---|
| 1 | C2 OpenCode → C1+C3 | OpenAI `/v1/chat/completions` | OpenCode 1.2.27 |
| 2 | C2 Aider → C1+C3 | OpenAI `/v1/chat/completions` | Aider 0.86.2 |
| 3 | C5 Claude Code → C1+C3 | Anthropic `/v1/messages` | Claude Code 2.1.81 |
| 4 | C6 KiloCode → C1+C3 | OpenAI `/v1/chat/completions` | KiloCode 7.1.0 |
| 5 | C7a Gateway → C1+C3 | Health endpoint `:18789` | OpenClaw 2026.3.13 |
| 6 | C7b CLI → C1+C3 | OpenAI `/v1/chat/completions` | OpenClaw 2026.3.13 |
| 7 | C8 Hermes → C1+C3 | OpenAI `/v1/chat/completions` | Hermes v0.3.0 |

Each test validates: tool version inside the container → C1 reachability → C3 reachability → a round-trip ask with an expected response marker.

---

### Playwright Test Suite (CT)

The `CT_tests` container runs 45 automated tests covering API schema compliance, streaming, edge cases, and the Swagger / ReDoc UIs.

```bash
# Run from host against the live C1 container
docker compose run --rm test

# Or run on the host directly
BASE_URL=http://localhost:8000 python -m pytest tests/test_playwright.py -v

# View the HTML report
open tests/reports/report.html
```

| Category | Tests |
|---|---|
| Health endpoint | 3 |
| Models endpoint | 7 |
| Chat completions | 9 |
| Streaming SSE | 2 |
| Edge cases & security | 9 |
| OpenAI schema compliance | 3 |
| Swagger UI | 5 |
| ReDoc | 3 |
| OpenAPI JSON | 4 |

---

### Manual curl validation

```bash
# Health
curl http://localhost:8000/health

# Models
curl http://localhost:8000/v1/models

# OpenAI chat
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello!"}]}'

# Anthropic chat
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: not-needed" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":256,"messages":[{"role":"user","content":"Hello!"}]}'

# Streaming
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Count to 5"}],"stream":true}'
```

---

## 10. Configuration Reference

All settings are read from `.env` (copy from `.env.example`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `BING_COOKIES` | Yes | — | `_U` cookie from `copilot.microsoft.com` (auto-managed by C3) |
| `COPILOT_STYLE` | No | `balanced` | Conversation style: `creative`, `balanced`, `precise` |
| `COPILOT_PERSONA` | No | `copilot` | Persona: `copilot` or `bing` |
| `HOST` | No | `0.0.0.0` | Server bind address |
| `PORT` | No | `8000` | Server port |
| `RELOAD` | No | `false` | Auto-reload on code changes (dev only) |
| `API_KEY` | No | `` | Optional bearer token for C1 authentication |
| `USE_PROXY` | No | `false` | Route traffic through HTTP/HTTPS proxy |
| `REQUEST_TIMEOUT` | No | `90` | Upstream Copilot request timeout (seconds) |
| `CONNECT_TIMEOUT` | No | `15` | WebSocket connect timeout (seconds) |
| `RATE_LIMIT` | No | `100/minute` | Per-IP rate limit |
| `CIRCUIT_BREAKER_THRESHOLD` | No | `50` | Failure % before circuit opens |

### Agent container environment variables

| Variable | Container | Description |
|---|---|---|
| `OPENAI_API_BASE` | C2, C6 | Points to `http://app:8000/v1` |
| `AIDER_MODEL` | C2 | Model name for Aider (`openai/copilot`) |
| `ANTHROPIC_BASE_URL` | C5 | Points to `http://app:8000` |
| `ANTHROPIC_API_KEY` | C5 | Placeholder key (`sk-ant-not-needed-...`) |
| `OPENCLAW_PROVIDER_BASE_URL` | C7a | Points to `http://app:8000/v1` |
| `OPENCLAW_GATEWAY_TOKEN` | C7a, C7b | Shared gateway auth token |
| `OPENAI_BASE_URL` | C8 | Points to `http://app:8000/v1` |
| `HERMES_INFERENCE_PROVIDER` | C8 | `openai` (forces OpenAI-compat path) |
| `LLM_MODEL` | C8 | `copilot` |
| `AGENT_ID` | All | Per-container session routing header |

---

## 11. Troubleshooting

### C1 returns `"Failed to create conversation, status: 404"`
Cookies are missing or expired.
- Run C3 extraction: open `http://localhost:6080`, log in, then `curl -X POST http://localhost:8001/extract`
- Or manually update `BING_COOKIES` in `.env` and run `curl -X POST http://localhost:8000/v1/reload-config`

### C3 browser shows a blank screen
- Give it 15–20 seconds to start; the Chromium + noVNC stack takes time to initialise
- Check logs: `docker logs C3_browser-auth`

### C7a is in standby mode (not a real gateway yet)
This is expected. OpenClaw requires interactive onboarding before it can run as a service:
```bash
docker compose exec openclaw-gateway openclaw onboard
```
After onboarding, restart C7a:
```bash
docker compose restart openclaw-gateway
```

### C8 Hermes `hermes doctor` reports missing API key
The `HERMES_INFERENCE_PROVIDER=openai` env var bypasses the normal provider selection. If `hermes doctor` still complains, verify inside the container:
```bash
docker compose exec C8_hermes-agent bash -c 'echo "PROVIDER=$HERMES_INFERENCE_PROVIDER BASE=$OPENAI_BASE_URL"'
```

### Port 8000 already in use
```bash
lsof -i :8000
# Kill the conflicting process, or change PORT in .env
```

### Container exits immediately
```bash
docker logs C1_copilot-api
# Look for missing BING_COOKIES or Python import errors
```

### "Agent is already running" (HTTP 409)
Call `/v1/agent/stop` first, then `/v1/agent/start`.

### "Agent is paused" (HTTP 409)
Call `/v1/agent/resume` to unblock new tasks.

### Pair tests fail for one container
Run the individual test function to see the full output:
```bash
# Check that all containers are up and healthy first
docker compose ps

# Re-run just the failing pair
bash tests/run_pair_tests.sh   # sequential, shows full per-test output
```

---

## Project Structure

```
copilot-openai-wrapper/
│
├── server.py                Main FastAPI app (C1)
│                            /v1/chat/completions, /v1/messages, /v1/agent/*
│                            /health, /v1/models, /v1/sessions
│                            /v1/cookies/extract, /v1/reload-config
│
├── copilot_backend.py       WebSocket client + connection pool for Copilot
│                            CopilotConnectionPool, cookie caching, reload
│
├── agent_manager.py         Stateful agent lifecycle (STOPPED/RUNNING/PAUSED/BUSY)
│
├── models.py                Pydantic models — OpenAI + Anthropic request/response schemas
│
├── config.py                .env loader and cookie validation
│
├── circuit_breaker.py       Circuit breaker for upstream Copilot reliability
│
├── requirements.txt         Python production dependencies (C1)
│
├── Dockerfile               Multi-stage build for C1 (builder + runtime)
├── Dockerfile.browser       C3: Ubuntu 22.04 + Chromium + noVNC + Playwright
├── Dockerfile.agent         C2: python:3.11-slim + Aider + OpenCode + Node.js 20
├── Dockerfile.claude-code   C5: node:20-alpine + Claude Code CLI
├── Dockerfile.kilocode      C6: node:20-alpine + KiloCode CLI
├── Dockerfile.openclaw-gw   C7a: node:22-alpine + OpenClaw gateway
├── Dockerfile.openclaw-cli  C7b: node:22-alpine + OpenClaw CLI
├── Dockerfile.hermes        C8: python:3.11-slim + uv + Hermes Agent v2026.3.17
├── Dockerfile.test          CT: Playwright test runner
│
├── docker-compose.yml       Full stack orchestration (C1–C8 + CT)
│
├── .env                     Your local secrets (NOT committed)
├── .env.example             Template for all configuration variables
│
├── agent-terminal/          C2 launcher scripts and config
│   ├── start.sh
│   ├── opencode.json
│   └── .aider.conf.yml
│
├── browser_auth/            C3 cookie extraction service
│   ├── server.py            Flask API (/extract, /status, /health)
│   ├── cookie_extractor.py  Playwright Chromium automation
│   ├── entrypoint.sh        Xvfb + x11vnc + noVNC + Flask startup
│   └── requirements.txt
│
├── claude-code-terminal/    C5 launcher scripts
│   └── start.sh
│
├── kilocode-server/         C6 launcher scripts
│   └── entrypoint.sh
│
├── openclaw-gateway/        C7a gateway scripts and config
│   ├── entrypoint.sh        Gateway start with interactive fallback
│   └── openclaw.json        Provider config (points to C1)
│
├── openclaw-cli/            C7b CLI scripts
│   └── start.sh
│
├── hermes-agent/            C8 Hermes Agent scripts and config
│   ├── start.sh             standby/ask/hermes/status/bash modes
│   └── hermes-config.yaml   Pre-seeded config (local backend, compression)
│
├── workspace/               Shared volume mounted by all agent containers
│   ├── ask_helper.py        Universal one-shot ask script (OpenAI + Anthropic)
│   ├── calculator.py        Built-in calculator demo
│   └── professor_prompt.txt System prompt for all agent sessions
│
└── tests/
    ├── run_pair_tests.sh    7-pair integration test runner (sequential + parallel)
    ├── test_playwright.py   45 Playwright tests
    ├── test_unit_*.py       Unit tests for all core modules
    ├── conftest.py          pytest fixtures
    ├── validators.py        Response schema validators
    └── reports/             Generated HTML reports + screenshots
```

---

## License

MIT — see individual reference repository licenses for their implementations.

**Reference implementations:**
- [sydney-py](https://github.com/vsakkas/sydney.py) — WebSocket Copilot client
- [ReEdgeGPT](https://github.com/Integration-Automation/ReEdgeGPT) — Bing Chat reverse-engineering
- [ReCopilot](https://github.com/Integration-Automation/ReCopilot) — Microsoft Copilot API
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — Hermes Agent
