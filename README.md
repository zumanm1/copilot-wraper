# Copilot OpenAI-Compatible API Wrapper

> **Use Microsoft Copilot with any OpenAI-compatible client — fully containerised, zero pip conflicts.**

---

## Table of Contents

1. [Main Goal](#1-main-goal)
2. [How It Works](#2-how-it-works)
3. [Project Structure](#3-project-structure)
4. [Quick Start with Docker](#4-quick-start-with-docker)
5. [Getting Your Bing Cookie](#5-getting-your-bing-cookie)
6. [Running Locally (without Docker)](#6-running-locally-without-docker)
7. [API Reference — Chat Completions](#7-api-reference--chat-completions)
8. [API Reference — Agent Management](#8-api-reference--agent-management)
9. [Agent Walkthrough](#9-agent-walkthrough)
10. [Validating & Testing](#10-validating--testing)
11. [Docker Architecture](#11-docker-architecture)
12. [Configuration Reference](#12-configuration-reference)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Main Goal

Microsoft Copilot is a powerful AI assistant, but it has no official public API. This project **reverse-engineers the Copilot WebSocket protocol** and wraps it in a **100% OpenAI-compatible REST API**, so you can:

- Use **any OpenAI SDK** (Python, Node.js, etc.) with Copilot as the backend
- Drop Copilot into **any tool that supports OpenAI** (LangChain, AutoGen, Open WebUI, etc.)
- Run a **persistent AI agent** that maintains conversation context across multiple tasks
- Get **streaming responses** via Server-Sent Events (SSE)
- Send **images** alongside text prompts (multimodal)

```
Your App / OpenAI SDK
        │
        ▼
┌───────────────────────────────┐
│  FastAPI Server (port 8000)   │
│  /v1/chat/completions         │
│  /v1/models                   │
│  /v1/agent/*  (new!)          │
└──────────────┬────────────────┘
               │  WebSocket (sydney.py)
               ▼
    copilot.microsoft.com
```

---

## 2. How It Works

### Request Flow

1. **Client** sends an OpenAI-format request to `POST /v1/chat/completions`
2. **FastAPI** (`server.py`) validates the request using Pydantic models (`models.py`)
3. **CopilotBackend** (`copilot_backend.py`) creates a `SydneyClient` from the `sydney-py` library
4. `SydneyClient` opens a **WebSocket connection** to `copilot.microsoft.com`, authenticates using your `_U` cookie, and sends the prompt
5. The response streams back token-by-token via WebSocket
6. **FastAPI** re-formats the response into OpenAI's JSON schema and returns it

### Agent Flow

The **Agent Manager** (`agent_manager.py`) adds a stateful layer on top:

```
POST /v1/agent/start  →  Creates a persistent CopilotBackend session
POST /v1/agent/task   →  Sends task; SydneyClient maintains conversation history
POST /v1/agent/pause  →  Freezes the agent (rejects new tasks)
POST /v1/agent/resume →  Unfreezes the agent
POST /v1/agent/stop   →  Closes WebSocket, returns session summary
```

The agent maintains **full conversation context** across all tasks in a session — each task builds on the previous ones.

### Agent State Machine

```
  ┌─────────┐
  │ STOPPED │ ◄──────────────────────────────────────┐
  └────┬────┘                                        │
       │ POST /v1/agent/start                        │
       ▼                                             │
  ┌─────────┐  POST /v1/agent/pause  ┌────────┐     │
  │ RUNNING │ ──────────────────────► PAUSED  │     │
  └────┬────┘ ◄────────────────────── └────────┘     │
       │       POST /v1/agent/resume                  │
       │ POST /v1/agent/task                          │
       ▼                                             │
  ┌──────┐  task completes/fails                     │
  │ BUSY │ ──────────────────────────► RUNNING       │
  └──────┘                                           │
       │                                             │
       └─────────── POST /v1/agent/stop ─────────────┘
```

---

## 3. Project Structure

```
copilot-openai-wrapper/
│
├── 📄 server.py              Main FastAPI application
│                             Defines all HTTP endpoints:
│                             /v1/models, /v1/chat/completions,
│                             /v1/agent/*, /health
│
├── 📄 agent_manager.py       AI Agent lifecycle manager (NEW)
│                             State machine: STOPPED→RUNNING→PAUSED→BUSY
│                             Manages persistent Copilot WebSocket sessions
│                             Tracks task history with full metadata
│
├── 📄 copilot_backend.py     Abstraction layer over sydney-py
│                             Wraps SydneyClient with async support
│                             Handles connection pooling and reconnection
│
├── 📄 models.py              Pydantic data models (OpenAI schema)
│                             ChatCompletionRequest/Response
│                             Agent*Request/Response models
│
├── 📄 config.py              Configuration loader
│                             Reads .env file, validates BING_COOKIES
│
├── 📄 requirements.txt       Python dependencies
│
├── 🐳 Dockerfile             Multi-stage Docker build
│                             Stage 1 (builder): installs dependencies
│                             Stage 2 (runtime): lean image, non-root user
│
├── 🐳 Dockerfile.test        Playwright test container
│                             Uses mcr.microsoft.com/playwright/python
│
├── 🐳 docker-compose.yml     Orchestrates app + test services
│                             app: FastAPI server with health check
│                             test: Playwright suite (runs after app is healthy)
│
├── 📄 .dockerignore          Excludes .env, __pycache__, .git from images
│
├── 📄 .env                   Your local secrets (NOT committed to git)
├── 📄 .env.example           Template showing all available variables
│
├── 📁 test_client/           Manual test scripts
│   ├── test_basic.py         OpenAI SDK-based API tests
│   └── copilot_agent.py      Demo agent with tool-use (time, weather, etc.)
│
└── 📁 tests/                 Automated Playwright test suite
    ├── test_playwright.py    45 tests: API + Browser UI + Edge cases
    ├── requirements-test.txt Test dependencies
    └── reports/              Generated HTML report + screenshots
        ├── report.html
        └── screenshots/
```

---

## 4. Quick Start with Docker

> **Prerequisites:** Docker Desktop installed and running.

### Step 1 — Clone / navigate to the project

```bash
cd /Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper
```

### Step 2 — Configure your Bing cookie

```bash
cp .env.example .env
# Edit .env and set BING_COOKIES=<your _U cookie value>
# See Section 5 for how to get the cookie
```

### Step 3 — Build the Docker image

```bash
docker build -t copilot-api:latest --target runtime .
```

### Step 4 — Start the server

```bash
# Option A: docker compose (recommended)
docker compose up app

# Option B: plain docker run
docker run -d \
  --name copilot-api \
  -p 8000:8000 \
  --env-file .env \
  --restart unless-stopped \
  copilot-api:latest
```

### Step 5 — Verify it's running

```bash
# Health check
curl http://localhost:8000/health
# → {"status":"ok","service":"copilot-openai-wrapper"}

# List models
curl http://localhost:8000/v1/models
# → {"object":"list","data":[{"id":"copilot",...},...]}

# Open Swagger UI in browser
open http://localhost:8000/docs
```

### Step 6 — Stop the server

```bash
# docker compose
docker compose down

# plain docker
docker stop copilot-api && docker rm copilot-api
```

---

## 5. Getting Your Bing Cookie

The server authenticates with Microsoft Copilot using your browser's `_U` cookie.

**Steps:**

1. Open **https://copilot.microsoft.com** in your browser
2. Sign in with your **Microsoft account**
3. Press **F12** to open Developer Tools
4. Go to **Application** tab → **Cookies** → `https://copilot.microsoft.com`
5. Find the cookie named **`_U`**
6. Copy its **Value** (a long string of letters and numbers)
7. Paste it into your `.env` file:

```env
BING_COOKIES=1A2B3C4D...your_long_cookie_value_here
```

> ⚠️ **Keep your cookie private.** It grants access to your Microsoft account.
> The cookie expires periodically — if you get 401/404 errors, refresh it.

---

## 6. Running Locally (without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your BING_COOKIES

# Start the server
python server.py

# Or with uvicorn directly
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

---

## 7. API Reference — Chat Completions

### `GET /health`
Liveness probe.

```bash
curl http://localhost:8000/health
```
```json
{"status": "ok", "service": "copilot-openai-wrapper"}
```

---

### `GET /v1/models`
List available model IDs.

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

### `POST /v1/chat/completions`

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

---

## 8. API Reference — Agent Management

The agent provides a **stateful, persistent conversation session** with Microsoft Copilot. Unlike `/v1/chat/completions` (which is stateless), the agent remembers all previous tasks in the session.

All agent endpoints are under `/v1/agent/` and are visible in the Swagger UI at `http://localhost:8000/docs` under the **Agent** tag.

---

### `POST /v1/agent/start`
Start a new agent session.

```bash
curl -X POST http://localhost:8000/v1/agent/start \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Optional — custom system prompt:**
```bash
curl -X POST http://localhost:8000/v1/agent/start \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "You are a Python expert. Always show code examples."}'
```

**Response:**
```json
{
  "session_id": "agent-efe7f8be5d75",
  "status": "running",
  "started_at": "2026-03-20T20:13:05.242643+00:00",
  "message": "Agent started successfully."
}
```

---

### `POST /v1/agent/task`
Give the agent a task or question.

```bash
# Non-streaming
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "What is 1 + 1?"}'
```

**Response:**
```json
{
  "task_id": "task-10afc300ff83",
  "session_id": "agent-efe7f8be5d75",
  "status": "completed",
  "prompt": "What is 1 + 1?",
  "result": "1 + 1 = 2. This is basic arithmetic...",
  "error": null,
  "created_at": "2026-03-20T20:13:39.045954+00:00",
  "completed_at": "2026-03-20T20:13:41.123456+00:00"
}
```

**Streaming task:**
```bash
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "Explain quantum entanglement simply.", "stream": true}'
```

**Python example:**
```python
import requests, json

# Start agent
requests.post("http://localhost:8000/v1/agent/start", json={})

# Give it a task
resp = requests.post("http://localhost:8000/v1/agent/task",
                     json={"task": "What is 1 + 1?"})
print(resp.json()["result"])

# Follow-up (agent remembers context!)
resp = requests.post("http://localhost:8000/v1/agent/task",
                     json={"task": "Now multiply that by 10."})
print(resp.json()["result"])
```

---

### `POST /v1/agent/pause`
Pause the agent. New tasks will be rejected with HTTP 409 until resumed.

```bash
curl -X POST http://localhost:8000/v1/agent/pause
```
```json
{
  "session_id": "agent-efe7f8be5d75",
  "status": "paused",
  "paused_at": "2026-03-20T20:13:23.579049+00:00",
  "message": "Agent paused. Submit /v1/agent/resume to continue."
}
```

---

### `POST /v1/agent/resume`
Resume a paused agent.

```bash
curl -X POST http://localhost:8000/v1/agent/resume
```
```json
{
  "session_id": "agent-efe7f8be5d75",
  "status": "running",
  "resumed_at": "2026-03-20T20:14:00.000000+00:00",
  "message": "Agent resumed successfully."
}
```

---

### `POST /v1/agent/stop`
Stop the agent and close the Copilot connection.

```bash
curl -X POST http://localhost:8000/v1/agent/stop
```
```json
{
  "session_id": "agent-efe7f8be5d75",
  "status": "stopped",
  "tasks_total": 5,
  "tasks_completed": 4,
  "tasks_failed": 1,
  "message": "Agent stopped successfully."
}
```

---

### `GET /v1/agent/status`
Get the current agent status and statistics.

```bash
curl http://localhost:8000/v1/agent/status
```
```json
{
  "status": "running",
  "session_id": "agent-efe7f8be5d75",
  "started_at": "2026-03-20T20:13:05.242643+00:00",
  "paused_at": null,
  "tasks_total": 3,
  "tasks_completed": 3,
  "tasks_failed": 0,
  "tasks_pending_busy": 0
}
```

---

### `GET /v1/agent/history`
Get the full task history for the current session.

```bash
curl http://localhost:8000/v1/agent/history
```

---

### `GET /v1/agent/history/{task_id}`
Get a specific task by its ID.

```bash
curl http://localhost:8000/v1/agent/history/task-10afc300ff83
```

---

### `DELETE /v1/agent/history`
Clear task history (does not stop the agent).

```bash
curl -X DELETE http://localhost:8000/v1/agent/history
```

---

## 9. Agent Walkthrough

A complete example session using `curl`:

```bash
# 1. Start the agent
curl -X POST http://localhost:8000/v1/agent/start -H "Content-Type: application/json" -d '{}'

# 2. Ask a math question
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "What is 1 + 1?"}'

# 3. Ask a follow-up (agent remembers!)
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "Now square that result."}'

# 4. Pause the agent
curl -X POST http://localhost:8000/v1/agent/pause

# 5. Try a task while paused (returns 409)
curl -X POST http://localhost:8000/v1/agent/task \
  -H "Content-Type: application/json" \
  -d '{"task": "This will be rejected."}'

# 6. Resume
curl -X POST http://localhost:8000/v1/agent/resume

# 7. Check status
curl http://localhost:8000/v1/agent/status

# 8. View task history
curl http://localhost:8000/v1/agent/history

# 9. Stop the agent
curl -X POST http://localhost:8000/v1/agent/stop
```

---

## 10. Validating & Testing

### Option A — Playwright Test Suite (45 tests, automated)

Tests run from the host machine against the Docker container:

```bash
# 1. Start the app container
docker compose up app -d

# 2. Run the full test suite
cd /Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper
BASE_URL=http://localhost:8000 python -m pytest tests/test_playwright.py -v

# 3. View the HTML report
open tests/reports/report.html
```

**Test categories:**

| Category | Tests | What it validates |
|---|---|---|
| Health Endpoint | 3 | Status 200, JSON body, content-type |
| Models Endpoint | 7 | OpenAI schema, all model IDs present |
| Chat Completions | 9 | Request validation, 422 errors, all models |
| Streaming SSE | 2 | stream=true accepted, content-type |
| Edge Cases & Security | 9 | Oversized payloads, injection, 405, 404 |
| OpenAI Schema | 3 | Full schema compliance |
| Swagger UI (browser) | 5 | Page loads, endpoints listed, Try it out |
| ReDoc (browser) | 3 | Page loads, content visible |
| OpenAPI JSON | 4 | Valid schema, all routes present |

### Option B — Basic OpenAI SDK Tests

```bash
cd test_client
python test_basic.py
```

### Option C — Manual curl validation

```bash
# Health
curl http://localhost:8000/health

# Models
curl http://localhost:8000/v1/models

# Chat (requires real cookie)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello!"}]}'

# Agent start → task → stop
curl -X POST http://localhost:8000/v1/agent/start -H "Content-Type: application/json" -d '{}'
curl -X POST http://localhost:8000/v1/agent/task -H "Content-Type: application/json" -d '{"task":"What is 1+1?"}'
curl -X POST http://localhost:8000/v1/agent/stop
```

### Option D — Swagger UI (interactive browser testing)

Open **http://localhost:8000/docs** in your browser.
All endpoints are listed with "Try it out" buttons for interactive testing.

---

## 11. Docker Architecture

### Multi-Stage Dockerfile

```dockerfile
# Stage 1: Builder
# - Installs gcc and all Python packages
# - Packages go to /install (not system Python)
FROM python:3.11-slim AS builder

# Stage 2: Runtime (final image)
# - Copies only the installed packages from builder
# - Copies only the application source files
# - Creates a non-root user (appuser, UID 1000)
# - No build tools, no gcc, no pip in the final image
FROM python:3.11-slim AS runtime
```

**Security features:**
- ✅ Non-root user (`appuser`) — container cannot write to system paths
- ✅ No secrets baked in — `.env` is excluded by `.dockerignore`
- ✅ Minimal attack surface — no build tools in runtime image
- ✅ Built-in `HEALTHCHECK` — Docker knows when the app is ready

### docker-compose.yml Services

| Service | Purpose | Port |
|---|---|---|
| `app` | FastAPI server | `8000:8000` |
| `test` | Playwright test runner | (no port, internal only) |

The `test` service uses `depends_on: app: condition: service_healthy` — it **waits for the health check to pass** before running tests.

```bash
# Start only the API server
docker compose up app

# Run tests (starts app first, waits for health, then runs tests)
docker compose run --rm test

# Start everything
docker compose up

# Stop everything
docker compose down
```

---

## 12. Configuration Reference

All settings are read from the `.env` file (copy from `.env.example`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `BING_COOKIES` | ✅ Yes | — | Your `_U` cookie from copilot.microsoft.com |
| `COPILOT_STYLE` | No | `balanced` | Conversation style: `creative`, `balanced`, `precise` |
| `COPILOT_PERSONA` | No | `copilot` | Persona: `copilot` or `bing` |
| `HOST` | No | `0.0.0.0` | Server bind address |
| `PORT` | No | `8000` | Server port |
| `RELOAD` | No | `false` | Auto-reload on code changes (dev only) |
| `API_KEY` | No | `` | Optional API key for authentication |
| `USE_PROXY` | No | `false` | Route traffic through HTTP/HTTPS proxy |

---

## 13. Troubleshooting

### `"Failed to create conversation, received status: 404"`
Your `BING_COOKIES` value is invalid or expired.
→ Follow [Section 5](#5-getting-your-bing-cookie) to get a fresh cookie.

### `"Agent is already running"` (HTTP 409)
You called `/v1/agent/start` when the agent is already running.
→ Call `/v1/agent/stop` first, then `/v1/agent/start`.

### `"Agent is paused"` (HTTP 409)
You submitted a task while the agent is paused.
→ Call `/v1/agent/resume` first.

### `"Agent is not started"` (HTTP 409)
You submitted a task without starting the agent.
→ Call `/v1/agent/start` first.

### Port 8000 already in use
```bash
# Find what's using port 8000
lsof -i :8000
# Kill it or change PORT in .env
```

### Docker daemon not running
```bash
open -a Docker   # macOS
# Wait ~20 seconds for Docker Desktop to start
```

### Container exits immediately
```bash
docker logs copilot-api
# Check for missing BING_COOKIES or import errors
```

---

## License

MIT — See individual reference repo licenses for their implementations.

**Reference implementations analysed:**
- [sydney-py](https://github.com/vsakkas/sydney.py) — WebSocket Copilot client
- [ReEdgeGPT](https://github.com/Integration-Automation/ReEdgeGPT) — Bing Chat reverse-engineering
- [ReCopilot](https://github.com/Integration-Automation/ReCopilot) — Microsoft Copilot API
- [llm-api-open](https://github.com/f33rni/llm-api-open) — Browser automation with image support
- [copilotscrape](https://github.com/alabr0s/copilotscrape) — Selenium-based scraper
