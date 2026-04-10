# Copilot OpenAI-Compatible API Wrapper

> **Use Microsoft Copilot with any OpenAI or Anthropic client — nine containerised services, zero configuration conflicts.**

> **⚠️ Documentation Status (2026-04-10):** The codebase is ahead of this documentation. Recent additions — grouped nav with keyboard shortcuts, `/session-manager` page, `/docuz-tasked` ops manual, expanded API reference (Tasks/Alerts/Tokens/Session Manager sections), dynamic agent filter in Token Counter, and severity allowlist fix — are **live in the running container but not yet reflected in this README or the `docs/` files**. Use the in-app `/api` and `/docuz-tasked` pages as the authoritative live reference until docs are updated.

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
9. [Terminal Commands Reference](#9-terminal-commands-reference)
10. [Multi-Agent Debate Framework](#10-multi-agent-debate-framework)
11. [Testing](#11-testing)
12. [Configuration Reference](#12-configuration-reference)
13. [Troubleshooting](#13-troubleshooting)
14. [C9 Validation Console](#14-c9-validation-console)

---

## 1. Main Goal

Microsoft Copilot is a powerful AI assistant with no official public API. This project **reverse-engineers the Copilot WebSocket protocol** and wraps it in two fully compatible REST APIs:

- **OpenAI-compatible** — `/v1/chat/completions` works with any OpenAI SDK, LangChain, AutoGen, Open WebUI, Aider, OpenCode, KiloCode, and more
- **Anthropic-compatible** — `/v1/messages` works with Claude Code, the Anthropic SDK, and any client that targets Claude's API

The compose file defines twelve primary runtime containers plus one separate test container:

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
                       │
          ┌────────────┴────────────┐
          │ consumer profile        │ m365_hub profile
          │ (direct WebSocket)      │ (C3 browser proxy)
          ▼                         ▼
  copilot.microsoft.com    C3 browser-auth
                           POST /chat → Playwright
                           → m365.cloud.microsoft
                           → SignalR WS (substrate.office.com)
```

---

## 2. How It Works

### Request Flow

C1 supports **two routing modes** selected by `COPILOT_PROVIDER` (or auto-detected from `COPILOT_PORTAL_PROFILE`):

#### Consumer Copilot (default, `consumer` profile)

1. Client → `POST /v1/chat/completions` on C1
2. C1 opens a **direct WebSocket** to `copilot.microsoft.com` using session cookies extracted by C3
3. Response streams back token-by-token
4. C1 re-formats into OpenAI SSE or Anthropic chunks

#### M365 Copilot (`m365_hub` profile) — Phase B

1. Client → `POST /v1/chat/completions` on C1
2. C1 calls `_c3_proxy_call()` → **HTTP POST** to C3's `/chat` endpoint
3. C3 uses **Playwright** to type the prompt into the real M365 Copilot web UI at `m365.cloud.microsoft/chat`
4. C3 intercepts the **SignalR WebSocket** response from `substrate.office.com/m365Copilot/Chathub/`
5. C3 parses SignalR frames (delimited by `\x1e`), extracts bot text from `type=2` completion frames
6. C3 returns the response text to C1 via JSON
7. C1 re-formats into the OpenAI/Anthropic response schema and returns it to the caller

> **Why the browser proxy?** M365 Copilot uses a SignalR-based protocol on `substrate.office.com` that requires an active browser session with OAuth tokens. Direct WebSocket connections (like the consumer path) don't work because M365's auth model binds to the browser session, not standalone cookies.

### Per-Agent Session Routing

Every request can include an `X-Agent-ID` header. C1 uses this to route the request to a **dedicated backend session** — so C2 (Aider), C5 (Claude Code), C6 (KiloCode), C7b (OpenClaw CLI), and C8 (Hermes) each maintain their own isolated conversation history even when running concurrently.

```
X-Agent-ID: c2-aider      → session pool slot A
X-Agent-ID: c5-claude-code → session pool slot B
X-Agent-ID: c8-hermes      → session pool slot C
```

### Cookie & Session Flow (C3 → C1)

C3 runs a headless Chromium browser with a noVNC remote display. Authentication depends on the profile:

#### Consumer profile
```
noVNC :6080 → login to copilot.microsoft.com → C3 /extract → cookies → .env → C1 reloads
```

#### M365 profile
```
noVNC :6080 → login to m365.cloud.microsoft → session persists in browser
C1 receives prompt → POST C3 /chat → Playwright types in M365 UI → SignalR WS response → C1
```

> **Key difference:** In M365 mode, C3 doesn't just extract cookies — it acts as a **live browser proxy**. The authenticated browser session inside C3 is used for every chat request. C1 calls `POST /chat` on C3 for each prompt, and C3 submits it through the real M365 Copilot web UI.

---

## 3. Container Reference

| Container | Name | Image | Port(s) | Purpose |
|---|---|---|---|---|
| C1b | `C1b_copilot-api` | `copilot-api:latest` | `8000` (host) | FastAPI — OpenAI + Anthropic API |
| C3b | `C3b_browser-auth` | `copilot-browser-auth:latest` | `6080` noVNC, `8001` API | Cookie extraction via headless Chrome |
| C2b | `C2b_agent-terminal` | `copilot-agent-terminal:latest` | `8080` (internal health) | Aider + OpenCode AI agent terminal |
| C5b | `C5b_claude-code` | `copilot-claude-code-terminal:latest` | `8080` (internal health) | Claude Code CLI (Anthropic format) |
| C6b | `C6b_kilocode` | `copilot-kilocode-terminal:latest` | `8080` (internal health) | KiloCode CLI terminal |
| C7ab | `C7ab_openclaw-gateway` | `copilot-openclaw-gateway:latest` | `18789` (host) | OpenClaw gateway (WebSocket hub) |
| C7bb | `C7bb_openclaw-cli` | `copilot-openclaw-cli:latest` | `8080` (internal health) | OpenClaw CLI / TUI |
| C8b | `C8b_hermes-agent` | `copilot-hermes-agent:latest` | `8080` (internal health) | Hermes Agent (memory, skills, cron) |
| C9b | `C9b_jokes` | `copilot-c9-jokes:latest` | `6090` (host) | Validation console — chat, agent, multi-agento, logs, health UI |
| C10b | `C10b_sandbox` | `copilot-c10-sandbox:latest` | internal `:8100` only | Agent workspace sandbox for `/api/agent/*` flows |
| C11b | `C11b_sandbox` | `copilot-c11-sandbox:latest` | internal `:8200` only | Session-scoped sandbox for `/api/multi-Agento/*` |
| C12b | `C12b_sandbox` | `copilot-c12b-sandbox:latest` | `8210` (host) | Lean coding/test sandbox for Tasked pipelines |
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
│  ┌─────── Consumer mode ───────┐  ┌──── M365 mode ──────────────┐ │
│  │ C1 ──WSS──► copilot.ms.com  │  │ C1 ──POST /chat──► C3       │ │
│  │ (direct WebSocket + cookies)│  │ C3 ──Playwright──► M365 UI  │ │
│  └─────────────────────────────┘  │ C3 ──SignalR WS──►          │ │
│                                   │   substrate.office.com       │ │
│  C3 browser-auth                  └─────────────────────────────┘ │
│  :6080 (noVNC)   :8001 (API)                                      │
│                                                                    │
│  C1 copilot-api :8000                                              │
│  /v1/chat/completions  /v1/messages  /v1/agent/*                   │
│                      ▲                                             │
│  C2b agent-terminal ─┤  (Aider, OpenCode)                          │
│  C5b claude-code ────┤  (Claude Code — Anthropic /v1)              │
│  C6b kilocode ───────┤  (KiloCode)                                 │
│  C7ab openclaw-gw ───┤  :18789 (OpenClaw Gateway)                  │
│  C7bb openclaw-cli ──┤  (OpenClaw CLI)                             │
│  C8b hermes-agent ───┘  (Hermes — memory, skills, cron)            │
│                                                                    │
│  CT tests ─────────HTTP tests──────► C1                            │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. Quick Start

> See **[INSTALL.md](INSTALL.md)** for a complete step-by-step guide with Linux and macOS platform notes, build-time estimates, and a full troubleshooting section.

Docker Compose uses the project name `c3btest-feed` from [`docker-compose.yml`](docker-compose.yml). `docker compose build` and `docker compose up` use **service names** such as `app` and `c9-jokes`, while the running containers appear as `C1b_copilot-api`, `C9b_jokes`, and the other explicit `container_name` values.

### Prerequisites

| Requirement | macOS | Linux (Ubuntu/Debian) |
|---|---|---|
| Docker | [Docker Desktop ≥ 4.x](https://www.docker.com/products/docker-desktop/) | Docker Engine + Compose plugin |
| Git | `xcode-select --install` | `sudo apt-get install git` |
| curl | Built-in | `sudo apt-get install curl` |
| Free ports | 8000, 6080, 8001, 18789, 6090 | Same |

**Linux Docker install (one-time):**
```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER   # then log out and back in
```

**Verify Docker:**
```bash
docker --version           # Docker version 24.x or later
docker compose version     # Docker Compose version v2.x or later
```

---

### Step 1 — Clone

```bash
git clone https://github.com/zumanm1/copilot-wraper.git
cd copilot-wraper/copilot-openai-wrapper
```

### Step 2 — Configure

```bash
cp .env.example .env
# Cookies are populated automatically by C3 in Step 4.
# Manual fallback: set BING_COOKIES=<your _U cookie value>
```

**Linux users — Chrome data path:**

The docker-compose.yml mounts your host Chrome profile into C1 (read-only). The default path is the macOS path. On Linux, add this to your `.env`:

```bash
echo 'CHROME_DATA_PATH=${HOME}/.config/google-chrome' >> .env
```

`docker-compose.yml` picks up `CHROME_DATA_PATH` automatically — no file editing needed.

### Step 3 — Build and start the core stack (C1 + C3)

```bash
# Build C1 and C3 images (first run: ~5–10 min)
docker compose build app browser-auth

# Start C1 (API server) and C3 (browser auth)
docker compose up app browser-auth -d

# Verify both are healthy
docker compose ps
curl http://localhost:8000/health
# → {"status":"ok","service":"copilot-openai-wrapper"}
```

### Step 4 — Authenticate via C3 (noVNC)

```bash
# Open the noVNC browser in your host browser
open http://localhost:6080           # macOS
xdg-open http://localhost:6080       # Linux (or paste the URL manually)

# Inside noVNC: navigate to https://copilot.microsoft.com and sign in
# Then extract cookies:
curl -X POST http://localhost:8001/extract
# → {"status":"ok","cookies_saved":true}

# Reload C1 so it picks up the new cookies
curl -X POST http://localhost:8000/v1/reload-config

# Verify C1 has valid cookies
curl http://localhost:8000/v1/debug/cookie
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

### Step 6 — Build and start the full stack (all containers)

Services are split into two groups in `docker-compose.yml`:
- **No profile** — CORE (C1b, C3b, C6b, C9b) + C10b/C11b sandboxes
- **`--profile optional`** — AI Agents (C2b, C5b, C7ab, C7bb, C8b) + C12b sandbox

```bash
# Build CORE images (~8 min)
docker compose build app browser-auth kilocode-terminal c9-jokes

# Build AI Agent images (~10 min)
docker compose --profile optional build agent-terminal claude-code-terminal openclaw-gateway openclaw-cli hermes-agent

# Build Sandbox images (~5 min)
docker compose build c10-sandbox c11-sandbox
docker compose --profile optional build c12b-sandbox

# Start everything
docker compose up app browser-auth kilocode-terminal c9-jokes c10-sandbox c11-sandbox -d
docker compose --profile optional up agent-terminal claude-code-terminal openclaw-gateway openclaw-cli hermes-agent c12b-sandbox -d

# Check status
docker compose ps
```

**Service name → image → running container**

| Compose service | Image | Running container |
|---|---|---|
| `app` | `copilot-api:latest` | `C1b_copilot-api` |
| `agent-terminal` | `copilot-agent-terminal:latest` | `C2b_agent-terminal` |
| `browser-auth` | `copilot-browser-auth:latest` | `C3b_browser-auth` |
| `claude-code-terminal` | `copilot-claude-code-terminal:latest` | `C5b_claude-code` |
| `kilocode-terminal` | `copilot-kilocode-terminal:latest` | `C6b_kilocode` |
| `openclaw-gateway` | `copilot-openclaw-gateway:latest` | `C7ab_openclaw-gateway` |
| `openclaw-cli` | `copilot-openclaw-cli:latest` | `C7bb_openclaw-cli` |
| `hermes-agent` | `copilot-hermes-agent:latest` | `C8b_hermes-agent` |
| `c9-jokes` | `copilot-c9-jokes:latest` | `C9b_jokes` |
| `c10-sandbox` | `copilot-c10-sandbox:latest` | `C10b_sandbox` |
| `c11-sandbox` | `copilot-c11-sandbox:latest` | `C11b_sandbox` |
| `c12b-sandbox` | `copilot-c12b-sandbox:latest` | `C12b_sandbox` |

> **Low-memory machine?** Start CORE only, add groups on demand:
>
> ```bash
> # CORE only
> docker compose up app browser-auth kilocode-terminal c9-jokes -d
>
> # Add AI Agents when needed
> docker compose --profile optional up agent-terminal claude-code-terminal openclaw-gateway openclaw-cli hermes-agent -d
>
> # Add Sandboxes when needed
> docker compose up c10-sandbox c11-sandbox -d
> docker compose --profile optional up c12b-sandbox -d
>
> # Stop unused agents to free CPU/memory
> docker stop C2b_agent-terminal C5b_claude-code C7ab_openclaw-gateway C7bb_openclaw-cli C8b_hermes-agent
> ```

### Step 7 — Verify all containers

Run these checks after the Step 6 startup commands:

```bash
# C1 — FastAPI API server
curl http://localhost:8000/health

# C3 — Browser auth API
curl http://localhost:8001/health

# C3 — noVNC web UI
curl -sf http://localhost:6080/ | head -5

# C7a — OpenClaw gateway
curl http://localhost:18789/healthz

# C9 — Validation console
curl http://localhost:6090/api/status

# Agent containers (exec into each)
docker compose exec agent-terminal      curl -sf http://localhost:8080/health
docker compose exec claude-code-terminal curl -sf http://localhost:8080/health
docker compose exec kilocode-terminal   curl -sf http://localhost:8080/health
docker compose exec openclaw-cli        curl -sf http://localhost:8080/health
docker compose exec hermes-agent        curl -sf http://localhost:8080/health
```

**Full container health table:**

| Container | Service Name | Host URL | Expected |
|---|---|---|---|
| C1b | `app` | `http://localhost:8000/health` | `{"status":"ok"}` |
| C2b | `agent-terminal` | (exec only — no host port) | `ok` |
| C3b API | `browser-auth` | `http://localhost:8001/health` | `{"status":"ok"}` |
| C3b noVNC | `browser-auth` | `http://localhost:6080/` | HTML |
| C5b | `claude-code-terminal` | (exec only) | `ok` |
| C6b | `kilocode-terminal` | (exec only) | `ok` |
| C7ab | `openclaw-gateway` | `http://localhost:18789/healthz` | `{"status":"ok"}` |
| C7bb | `openclaw-cli` | (exec only) | `ok` |
| C8b | `hermes-agent` | (exec only) | `ok` |
| C9b | `c9-jokes` | `http://localhost:6090/api/status` | JSON dict |
| C10b | `c10-sandbox` | `docker compose exec c10-sandbox curl -sf http://localhost:8100/health` | `ok` (internal) |
| C11b | `c11-sandbox` | `docker compose exec c11-sandbox curl -sf http://localhost:8200/health` | `ok` (internal) |
| C12b | `c12b-sandbox` | `http://localhost:8210/health` | `{"status":"ok"}` |

### Step 8 — Open the C9 Validation Console

```bash
open http://localhost:6090          # macOS
xdg-open http://localhost:6090      # Linux
```

| Page | URL | Purpose |
|---|---|---|
| Dashboard | `http://localhost:6090/` | Container health overview |
| Chat | `http://localhost:6090/chat` | Chat with any agent with live token streaming, thinking mode, and file upload |
| Pairs | `http://localhost:6090/pairs` | Batch: run one prompt against multiple agents |
| Logs | `http://localhost:6090/logs` | Full history — source, elapsed_ms, response excerpts |
| Health | `http://localhost:6090/health` | Live health snapshots for all containers |
| API reference | `http://localhost:6090/api` | C9 endpoint reference (`/api/docs` redirects here) |

### Stop everything

```bash
docker compose down

# ⚠ Also remove persistent volumes (browser session, Hermes memory, C9 database):
docker compose down -v
```

---

## 5. Cookie Authentication (C3)

C3 (`C3_browser-auth`) runs a **headless Chromium browser inside Docker** with a noVNC remote display, so you can log into Microsoft Copilot interactively and have your session cookies extracted automatically. This avoids the need to manually copy cookies from your host browser.

**Portal profile (consumer vs M365 web):** open [`http://localhost:8001/setup`](http://localhost:8001/setup) to write `COPILOT_PORTAL_PROFILE` (and optional portal/API URL overrides) into the mounted `.env` and trigger `POST /v1/reload-config` on C1. Then use noVNC to sign in on the matching host (`copilot.microsoft.com` or `m365.cloud.microsoft`) before calling `/extract`. C3 application code lives **in the image** (not bind-mounted): after you change `browser_auth/` or [`portal_urls.py`](portal_urls.py), run `docker compose build browser-auth` and recreate the container.

**M365 web + C1 chat:** With `m365_hub`, C1 still opens the Copilot WebSocket on **`copilot.microsoft.com`** by default (Phase A in [`docs/prd-dual-copilot-portal-m365.md`](docs/prd-dual-copilot-portal-m365.md)); `Origin`/`Referer` follow the M365 portal. **`POST /extract`** walks **`m365.cloud.microsoft`**, the **`m365.cloud.microsoft.com`** alias, **Bing**, then **`copilot.microsoft.com`** so the merged `COPILOT_COOKIES` include tokens scoped for the consumer API as well as M365 web. If you see **403** on the socket, complete sign-in in noVNC (including any **Continue** prompts), then re-run `/extract` and `POST /v1/reload-config` on C1. Override the API host only with `COPILOT_PORTAL_API_BASE_URL` when you have confirmed the correct origin from network traces.

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
| `/setup` | GET, POST | HTML form: portal profile + optional URLs → `.env` + C1 reload |
| `/extract` | POST | Extract cookies from active Chromium session |
| `/chat` | POST | **M365 mode:** Submit a prompt via Playwright, return SignalR response (`{"prompt":"...","timeout":90000}`) |
| `/navigate` | POST | Open a URL in the C3 browser (optional manual flows) |

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

All agent containers (C2b, C5b, C6b, C7ab, C7bb, C8b) run in **standby mode** by default: a lightweight health server listens on port 8080 so Docker can report them as healthy. You attach interactively with `docker compose exec` or run one-shot commands with `docker compose run --rm`.

**Detailed setup guides:**
- [C2 Aider Setup Guide](docs/C2-Aider-Setup-Guide.md)
- [C5 Claude Code Setup Guide](docs/C5-Claude-Code-Setup-Guide.md)
- [C8 Hermes Agent Setup Guide](docs/C8-Hermes-Setup-Guide.md)

**Integration validation status (all confirmed working with C1+C3 M365 pipeline):**

| Container | API Path | Agent ID | Status |
|-----------|----------|----------|--------|
| C2b Aider | OpenAI `/v1/chat/completions` | `c2-aider` | ✅ Validated |
| C5b Claude Code | Anthropic `/v1/messages` | `c5-claude-code` | ✅ Validated |
| C6b KiloCode | OpenAI `/v1/chat/completions` | `c6-kilocode` | ✅ Validated |
| C7bb OpenClaw CLI | OpenAI `/v1/chat/completions` | `c7-openclaw` | ✅ Validated |
| C7ab OpenClaw GW | Gateway standby `:18789/healthz` | `c7-openclaw` | ✅ Validated (standby) |
| C8b Hermes Agent | OpenAI `/v1/chat/completions` | `c8-hermes` | ✅ Validated |

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
docker compose exec C8b_hermes-agent hermes

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

## 9. Terminal Commands Reference

All commands are run from the project root directory on your **host machine**. Start the needed services first using the Step 6 commands for the `c3btest-feed` compose project.

### Health checks

```bash
# ── C1 backbone (required by all agents) ──────────────────────────────────────
curl -s http://localhost:8000/health | python3 -m json.tool

# ── C2 — Agent Terminal ───────────────────────────────────────────────────────
docker compose exec agent-terminal curl -sf http://localhost:8080/health | python3 -m json.tool

# ── C5 — Claude Code Terminal ─────────────────────────────────────────────────
docker compose exec claude-code-terminal curl -sf http://localhost:8080/health | python3 -m json.tool

# ── C6 — KiloCode Terminal ────────────────────────────────────────────────────
docker compose exec kilocode-terminal curl -sf http://localhost:8080/health | python3 -m json.tool

# ── C7a — OpenClaw Gateway ────────────────────────────────────────────────────
curl -s http://localhost:18789/

# ── C7b — OpenClaw CLI ────────────────────────────────────────────────────────
docker compose exec openclaw-cli curl -sf http://localhost:8080/health | python3 -m json.tool

# ── C8 — Hermes Agent ─────────────────────────────────────────────────────────
docker compose exec hermes-agent curl -sf http://localhost:8080/health | python3 -m json.tool
```

**All 6 health checks in parallel:**

```bash
echo "=== C1 ===" && curl -s http://localhost:8000/health &
echo "=== C7a ===" && curl -s http://localhost:18789/ &
echo "=== C2 ===" && docker compose exec agent-terminal curl -sf http://localhost:8080/health &
echo "=== C5 ===" && docker compose exec claude-code-terminal curl -sf http://localhost:8080/health &
echo "=== C6 ===" && docker compose exec kilocode-terminal curl -sf http://localhost:8080/health &
echo "=== C7b ===" && docker compose exec openclaw-cli curl -sf http://localhost:8080/health &
echo "=== C8 ===" && docker compose exec hermes-agent curl -sf http://localhost:8080/health &
wait
```

---

### C2 — Agent Terminal (Aider + OpenCode)

```bash
# Interactive menu — choose Aider or OpenCode
docker compose run --rm agent-terminal

# Launch Aider directly (full coding assistant with repo context)
docker compose run --rm agent-terminal aider

# Launch OpenCode directly
docker compose run --rm agent-terminal opencode

# One-shot ask via C1 (no interactive session)
docker compose run --rm agent-terminal ask "What is the halting problem?"

# Health status (checks C1 reachability, prints versions)
docker compose run --rm agent-terminal status

# Drop into a bash shell with ask/status helpers pre-loaded
docker compose run --rm agent-terminal bash

# Attach to the running standby container
docker compose exec agent-terminal bash
```

---

### C5 — Claude Code Terminal

```bash
# Interactive menu
docker compose run --rm claude-code-terminal

# Launch Claude Code CLI directly (routes to C1 via ANTHROPIC_BASE_URL)
docker compose run --rm claude-code-terminal claude

# One-shot ask via C1
docker compose run --rm claude-code-terminal ask "Explain attention mechanisms"

# Health status
docker compose run --rm claude-code-terminal status

# Bash shell
docker compose run --rm claude-code-terminal bash

# Attach to the running standby container
docker compose exec claude-code-terminal bash
```

---

### C6 — KiloCode Terminal

```bash
# Interactive menu
docker compose run --rm kilocode-terminal

# Launch KiloCode CLI directly
docker compose run --rm kilocode-terminal kilocode

# One-shot ask via C1
docker compose run --rm kilocode-terminal ask "What is gradient descent?"

# Health status
docker compose run --rm kilocode-terminal status

# Bash shell
docker compose run --rm kilocode-terminal bash

# Attach to the running standby container
docker compose exec kilocode-terminal bash
```

---

### C7a — OpenClaw Gateway

```bash
# Check gateway status (standby JSON or live gateway info)
curl -s http://localhost:18789/

# Run the interactive onboarding wizard (required once before gateway is live)
docker compose exec openclaw-gateway openclaw onboard

# After onboarding, restart to activate the real gateway
docker compose restart openclaw-gateway

# Attach to the running container
docker compose exec openclaw-gateway sh

# Tail gateway logs
docker compose logs openclaw-gateway --tail 50 -f
```

---

### C7b — OpenClaw CLI

```bash
# Interactive menu
docker compose run --rm openclaw-cli

# One-shot ask via C1
docker compose run --rm openclaw-cli ask "What is reinforcement learning?"

# Health status
docker compose run --rm openclaw-cli status

# Bash shell
docker compose run --rm openclaw-cli bash

# Attach to the running standby container
docker compose exec openclaw-cli bash
```

---

### C8 — Hermes Agent

```bash
# Interactive menu (choose Hermes CLI or bash shell)
docker compose run --rm hermes-agent

# Launch Hermes interactive CLI (persistent memory + skills + cron scheduling)
docker compose run --rm hermes-agent hermes

# One-shot ask via C1 (fast, no Hermes overhead)
docker compose run --rm hermes-agent ask "Explain transformer architecture"

# One-shot ask via Hermes native CLI (uses Hermes memory + skills)
docker compose run --rm hermes-agent hermes-chat "What is entropy?"

# Full health status (checks C1, C3, Hermes version, hermes doctor output)
docker compose run --rm hermes-agent status

# Bash shell with ask/status/hermes-chat helpers pre-loaded
docker compose run --rm hermes-agent bash

# Attach to the running standby container and open Hermes CLI
docker compose exec C8b_hermes-agent hermes
```

---

### Quick reference table

| Container | Health check | Interactive menu | One-shot ask |
|-----------|-------------|-----------------|--------------|
| C2 | `docker compose exec agent-terminal curl -sf http://localhost:8080/health` | `docker compose run --rm agent-terminal` | `docker compose run --rm agent-terminal ask "..."` |
| C5 | `docker compose exec claude-code-terminal curl -sf http://localhost:8080/health` | `docker compose run --rm claude-code-terminal` | `docker compose run --rm claude-code-terminal ask "..."` |
| C6 | `docker compose exec kilocode-terminal curl -sf http://localhost:8080/health` | `docker compose run --rm kilocode-terminal` | `docker compose run --rm kilocode-terminal ask "..."` |
| C7a | `curl -s http://localhost:18789/` | `docker compose exec openclaw-gateway sh` | — |
| C7b | `docker compose exec openclaw-cli curl -sf http://localhost:8080/health` | `docker compose run --rm openclaw-cli` | `docker compose run --rm openclaw-cli ask "..."` |
| C8 | `docker compose exec hermes-agent curl -sf http://localhost:8080/health` | `docker compose run --rm hermes-agent` | `docker compose run --rm hermes-agent ask "..."` |

---

## 10. Multi-Agent Debate Framework

`tests/agent_debate.py` orchestrates a structured, timed intellectual debate across all six agent containers simultaneously. No topic or role is hardcoded — the moderator LLM picks the topic and assigns a unique stance to each agent dynamically.

### How it works

```
Host (agent_debate.py)
      │
      ├── Phase 0: Moderator LLM (X-Agent-ID: debate-moderator)
      │     Picks a topic from a random seed domain.
      │     Assigns a unique intellectual stance to each of the 6 agents via JSON.
      │
      ├── Phase 1: Opening Statements
      │     Each agent receives: topic + their stance.
      │     Responds with a 4–6 sentence position statement.
      │
      ├── Phase 2: Rebuttal Rounds
      │     Each agent receives: topic + stance + FULL transcript of all prior statements.
      │     Must name and rebut a specific opponent, then advance a new argument.
      │     Stops when either `--duration` reserve is hit or `--max-rebuttal-rounds N` is satisfied.
      │
      ├── Phase 3: Closing Statements
      │     Each agent summarises their position and responds to the strongest counterargument.
      │
      └── Phase 4: Judge LLM (X-Agent-ID: debate-judge)
            Scores all 6 agents on accuracy, depth, engagement, and persuasiveness (1–10 each).
            Declares a winner with a one-sentence reason.
            Prints a ranked leaderboard with per-agent comments.
```

All 6 agents call C1's `/v1/chat/completions` endpoint with unique `X-Agent-ID` headers, giving each its own isolated Copilot session. Agents "hear" each other because the orchestrator embeds the full transcript in every prompt. A 3-second inter-agent delay and automatic 35-second circuit-breaker recovery prevent rate-limiting.

### Usage

```bash
# 10-minute debate with a random topic (default)
python3 tests/agent_debate.py

# 5-minute debate
python3 tests/agent_debate.py --duration 300

# Quick 2-minute validation test (time-bounded; may skip some rebuttals)
python3 tests/agent_debate.py --duration 120

# Exactly 2 full rebuttal passes (each agent speaks once per pass), then closings + judge
# Use a generous --duration so Phase 3–4 always fit (integration default: 900 s)
python3 tests/agent_debate.py --max-rebuttal-rounds 2 --duration 900

# Force a specific topic (LLM still assigns all stances — nothing else is hardcoded)
python3 tests/agent_debate.py --topic "Is P equal to NP?"
python3 tests/agent_debate.py --topic "Does consciousness require embodiment?"
python3 tests/agent_debate.py --topic "Is mathematics discovered or invented?"

# Run with only a subset of agents
python3 tests/agent_debate.py --agents C2a C5 C8

# Point at a non-default C1 endpoint
python3 tests/agent_debate.py --api http://localhost:8000

# Save transcripts to a custom directory
python3 tests/agent_debate.py --output /tmp/debates
```

### Agent keys

| Key | Agent name | Container |
|-----|-----------|-----------|
| `C2a` | C2-Aider | `agent-terminal` |
| `C2b` | C2-OpenCode | `agent-terminal` |
| `C5` | C5-Claude | `claude-code-terminal` |
| `C6` | C6-KiloCode | `kilocode-terminal` |
| `C7b` | C7b-OpenClaw | `openclaw-cli` |
| `C8` | C8-Hermes | `hermes-agent` |

### Scoring criteria

| Criterion | Description |
|-----------|-------------|
| **accuracy** | Factual correctness and precision; avoidance of errors |
| **depth** | Intellectual nuance, sophistication, and layered reasoning |
| **engagement** | How specifically each agent engaged with opponents' arguments |
| **persuasiveness** | Overall persuasive force of the cumulative case |

Each criterion is scored 1–10. Total maximum: 40.

### Sample output

```
TOPIC   : Does true artificial general intelligence require embodiment?
WINNER  : C5-Claude
REASON  : Claude offered the most balanced and nuanced position, effectively
          synthesising opposing arguments into a coherent middle ground.

  Rank  Agent               Acc  Dep  Eng  Per  Total
  ──────────────────────────────────────────────────────
  1     C5-Claude             9    9    9    9     36
  2     C2-OpenCode           8    8    8    8     32
  3     C8-Hermes             8    8    8    8     32
  4     C6-KiloCode           8    8    7    8     31
  5     C2-Aider              8    7    7    7     29
  6     C7b-OpenClaw          8    7    7    7     29
```

### Transcripts

Every debate is saved to `tests/debate-transcripts/debate_<YYYYMMDD_HHMMSS>.json` containing the full setup (topic, stances), every agent statement (with phase, round, and timestamp), and the complete judge scores.

---

## 11. Testing

### Pair Integration Tests + Debate (`tests/run_pair_tests.sh`)

Validates all 7 agent-container ↔ C1+C3 pairs and runs a multi-agent debate with **exactly two rebuttal rounds** (then closings and judging), via `agent_debate.py --max-rebuttal-rounds 2 --duration 900`.

```bash
# Sequential — all 7 pairs then debate (default)
bash tests/run_pair_tests.sh

# Parallel — all 7 pairs in parallel, then debate
bash tests/run_pair_tests.sh --parallel

# Skip the debate (faster, pairs only)
bash tests/run_pair_tests.sh --skip-debate
bash tests/run_pair_tests.sh --parallel --skip-debate
```

**Validated results — All 8 tests PASS:**

| # | Pair | API Format | Tool | Status |
|---|------|-----------|------|--------|
| 1 | C2 OpenCode → C1+C3 | OpenAI `/v1/chat/completions` | OpenCode 1.2.27 | ✅ PASS |
| 2 | C2 Aider → C1+C3 | OpenAI `/v1/chat/completions` | Aider 0.86.2 | ✅ PASS |
| 3 | C5 Claude Code → C1+C3 | Anthropic `/v1/messages` | Claude Code 2.1.81 | ✅ PASS |
| 4 | C6 KiloCode → C1+C3 | OpenAI `/v1/chat/completions` | KiloCode 7.1.0 | ✅ PASS |
| 5 | C7a Gateway → C1+C3 | Health endpoint `:18789` | OpenClaw 2026.3.13 | ✅ PASS |
| 6 | C7b CLI → C1+C3 | OpenAI `/v1/chat/completions` | OpenClaw 2026.3.13 | ✅ PASS |
| 7 | C8 Hermes → C1+C3 | OpenAI `/v1/chat/completions` | Hermes v0.3.0 | ✅ PASS |
| 8 | Multi-Agent Debate (2 rebuttal rounds) | All 6 agents + judge via C1 | `agent_debate.py` | ✅ PASS |

Each pair test validates: tool version inside container → C1 reachability → C3 reachability → round-trip ask with an expected response marker.

The debate test (test 8) validates: C1 health → opening statements (≥4 agents) → **two** Phase 2 rebuttal section headers → judge `WINNER` line → transcript JSON saved.

> **Note on parallel mode:** Tests 1 and 2 share the `agent-terminal` container. The parallel runner staggers them by 10 seconds to avoid session contention. Tests 3–7 run truly in parallel.

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

**C3 extract smoke** (after noVNC sign-in - validates `POST /extract` JSON; does not automate Microsoft login):

```bash
./scripts/smoke_c3_extract.sh
# Optional: also POST a tiny chat to C1 (often fails until cookies are good):
# WITH_CHAT=1 ./scripts/smoke_c3_extract.sh
```

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

## 12. Configuration Reference

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
| `COPILOT_PORTAL_PROFILE` | No | `consumer` | Portal profile: `consumer` or `m365_hub` |
| `COPILOT_PROVIDER` | No | `auto` | Provider: `auto`, `copilot`, or `m365`. Auto selects based on profile |
| `C3_URL` | No | `http://browser-auth:8001` | C3 endpoint for M365 browser proxy (used by C1 in M365 mode) |

### Agent container environment variables

| Variable | Container | Description |
|---|---|---|
| `OPENAI_API_BASE` | C2, C6 | Points to `http://app:8000/v1` |
| `AIDER_MODEL` | C2 | Model name for Aider (`openai/copilot`) |
| `KILO_MODEL` | C6 | Model name for KiloCode (`copilot`) |
| `ANTHROPIC_BASE_URL` | C5 | Points to `http://app:8000` |
| `ANTHROPIC_API_KEY` | C5 | Placeholder key (`sk-ant-not-needed-...`) |
| `OPENCLAW_PROVIDER_BASE_URL` | C7a | Points to `http://app:8000/v1` |
| `OPENCLAW_GATEWAY_TOKEN` | C7a, C7b | Shared gateway auth token |
| `OPENAI_BASE_URL` | C8 | Points to `http://app:8000/v1` |
| `HERMES_INFERENCE_PROVIDER` | C8 | `openai` (forces OpenAI-compat path) |
| `LLM_MODEL` | C8 | `copilot` |
| `AGENT_ID` | All | Per-container session routing header |

---

## 13. Troubleshooting

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
│                            CopilotConnectionPool, _c3_proxy_call (M365),
│                            dual-mode routing (consumer WS / M365 C3 proxy)
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
├── Dockerfile.c9-jokes      C9: python:3.11-slim + FastAPI + SQLite validation console
├── Dockerfile.test          CT: Playwright test runner
│
├── docker-compose.yml       Full stack orchestration (C1–C9 + CT)
│
├── .env                     Your local secrets (NOT committed)
├── .env.example             Template for all configuration variables
│
├── agent-terminal/          C2 launcher scripts and config
│   ├── start.sh
│   ├── opencode.json
│   └── .aider.conf.yml
│
├── browser_auth/            C3 cookie extraction + M365 browser proxy
│   ├── server.py            FastAPI (/extract, /status, /health, /chat)
│   ├── cookie_extractor.py  Playwright: cookie extraction + browser_chat()
│   │                        SignalR WS interception, execCommand text input
│   ├── entrypoint.sh        Xvfb + x11vnc + noVNC + uvicorn (--reload)
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
├── c9_jokes/                C9 Validation Console (FastAPI + SQLite + Jinja2 web UI)
│   ├── app.py               Main FastAPI application
│   ├── schema.sql           SQLite schema (chat_logs, validation_runs, pair_results, health_snapshots)
│   ├── requirements.txt     fastapi, uvicorn, httpx, jinja2, python-multipart
│   ├── static/              CSS + JS assets
│   └── templates/           Jinja2 HTML templates (dashboard, chat, pairs, logs, health, api_reference)
│
├── workspace/               Shared volume mounted by all agent containers
│   ├── ask_helper.py        Universal one-shot ask script (OpenAI + Anthropic)
│   ├── calculator.py        Built-in calculator demo
│   └── professor_prompt.txt System prompt for all agent sessions
│
└── tests/
    ├── run_pair_tests.sh      7-pair integration test runner (sequential + parallel)
    ├── agent_debate.py        Multi-agent debate; optional --max-rebuttal-rounds N
    ├── debate-transcripts/    Saved debate JSON transcripts (debate_<ts>.json)
    ├── test_playwright.py     45 Playwright tests
    ├── test_unit_*.py         Unit tests for all core modules
    ├── conftest.py            pytest fixtures
    ├── validators.py          Response schema validators
    └── reports/               Generated HTML reports + screenshots
```

---

## 14. C9 Validation Console

C9 (`c9-jokes`, port **6090**) is a lightweight FastAPI application with a full web UI for interacting with the entire stack in one place. It connects to every other container over the internal `copilot-net` Docker network and stores all activity in a **SQLite database** at `/app/data/c9.db` (persisted by the `c9-data` named volume).

### Access

```bash
open http://localhost:6090          # macOS
xdg-open http://localhost:6090      # Linux
```

### Pages

| Page | URL | Description |
|---|---|---|
| **Dashboard** | `/` | Real-time health cards for C1–C8, C10, and C11 |
| **Chat** | `/chat` | Single-agent chat with live token streaming, thinking mode, Work/Web toggle, and file upload |
| **Pairs** | `/pairs` | Batch mode — run one prompt against multiple agents (sequential or parallel) |
| **Logs** | `/logs` | Full history of all chat + validation calls (source, elapsed_ms, response excerpt, errors) |
| **Health** | `/health` | Timestamped container health snapshots |
| **API reference** | `/api` | Server-rendered C9 API reference (`/api/docs` → `/api`) |

### Features

- **Thinking mode** — dropdown pill: Auto / Quick Response / Think Deeper (maps to Copilot's balanced / precise / creative styles via `X-Chat-Mode` header to C1)
- **Work / Web toggle** — controls M365 scope via `X-Work-Mode` header to C1
- **File upload** — "+" button supports images (PNG, JPG, GIF, WebP) and documents (PDF, TXT, DOCX, XLSX, PPTX); files are uploaded to C1 `/v1/files` and referenced by `file_id` in the chat message
- **Live token streaming** — `/chat` sends `POST /api/chat` with `stream:true` and renders assistant text progressively while keeping the same `messages[]`, attachments, and session state as the JSON path
- **Source tracking** — Logs page shows `chat`, `chat-stream`, and `validate` in the source column so you can distinguish JSON chat, streamed chat, and batch validation runs
- **Elapsed time** — Every log row shows `elapsed_ms` so you can benchmark response times across agents and thinking modes

### C9 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Health dict for all containers |
| `POST` | `/api/chat` | Single chat call to one agent; JSON by default, SSE when `stream:true` |
| `POST` | `/api/validate` | Batch validation: prompt → N agents (sequential or parallel) |
| `POST` | `/api/upload` | Upload a file to C1; returns `{file_id, filename}` |
| `GET` | `/api/logs` | Paginated chat + validation history |
| `GET` | `/api/health-history` | Historical health snapshots |
| `GET` | `/api/session-health` | Live health probe for each container |

### Environment Variables (set automatically by docker-compose)

| Variable | Value | Purpose |
|---|---|---|
| `C1_URL` | `http://app:8000` | C1 FastAPI server |
| `C2_URL` | `http://agent-terminal:8080` | C2 health probe |
| `C3_URL` | `http://browser-auth:8001` | C3 health probe |
| `C5_URL` | `http://claude-code-terminal:8080` | C5 health probe |
| `C6_URL` | `http://kilocode-terminal:8080` | C6 health probe |
| `C7A_URL` | `http://openclaw-gateway:18789` | C7a gateway probe |
| `C7B_URL` | `http://openclaw-cli:8080` | C7b health probe |
| `C8_URL` | `http://hermes-agent:8080` | C8 health probe |
| `DATABASE_PATH` | `/app/data/c9.db` | SQLite database path |

All values are pre-wired in `docker-compose.yml` — no manual configuration needed.

### Database Schema

C9 maintains three SQLite tables:

| Table | Key Columns | Purpose |
|---|---|---|
| `chat_logs` | `id, agent_id, prompt, response_excerpt, elapsed_ms, source, created_at` | Every chat + validate call |
| `validation_runs` | `id, prompt, agents, mode, created_at` | Batch run metadata |
| `pair_results` | `run_id, agent_id, response, elapsed_ms, error` | Per-agent results from batch runs |
| `health_snapshots` | `id, snapshot_json, created_at` | Container health history |

---

## License

MIT — see individual reference repository licenses for their implementations.

**Reference implementations:**
- [sydney-py](https://github.com/vsakkas/sydney.py) — WebSocket Copilot client
- [ReEdgeGPT](https://github.com/Integration-Automation/ReEdgeGPT) — Bing Chat reverse-engineering
- [ReCopilot](https://github.com/Integration-Automation/ReCopilot) — Microsoft Copilot API
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — Hermes Agent
