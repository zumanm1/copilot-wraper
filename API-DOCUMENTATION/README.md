# API-DOCUMENTATION — Copilot OpenAI-Compatible API Wrapper

This folder is the complete reference for understanding the system architecture and integrating new AI agents with **zero or minimal changes** to C1 (copilot-api) or C3 (browser-auth).

---

## 5-Minute Quick-Start: Add a New Agent

```bash
# 1. Copy the stub Dockerfile
cp API-DOCUMENTATION/stubs/Dockerfile.cx-stub Dockerfile.c10-myagent

# 2. Edit: set your tool's install command and entrypoint
#    (see stubs/Dockerfile.cx-stub — 3 lines to change)

# 3. Copy and edit the docker-compose snippet
#    Paste stubs/docker-compose-cx-snippet.yml into docker-compose.yml
#    Change: container_name, AGENT_ID, port, image name

# 4. Build and start
docker compose build c10-myagent
docker compose up c10-myagent -d

# 5. Validate
bash API-DOCUMENTATION/stubs/validate_new_agent.sh c10-myagent
```

That's it. C1 and C3 require **no code changes**.

---

## Document Index

| # | File | What it covers |
|---|------|---------------|
| 01 | [Architecture Deep-Dive](01-architecture-deep-dive.md) | Every container, how C1+C3 work internally |
| 02 | [Authentication Flow](02-authentication-flow.md) | Cookies, noVNC, M365 session, provider modes |
| 03 | [Agent ID & Routing](03-agent-id-and-routing.md) | X-Agent-ID header, sticky tabs, session lifecycle |
| 04 | [API Reference](04-api-reference.md) | All C1/C3/C9 endpoints, schemas, headers |
| 05 | [New Agent Integration Steps](05-new-agent-integration-steps.md) | Step-by-step guide for any new AI agent |
| 06 | [Testing & Validation](06-testing-validation.md) | How to verify a new agent against C1+C3 |

## Stubs

| File | Purpose |
|------|---------|
| [stubs/Dockerfile.cx-stub](stubs/Dockerfile.cx-stub) | Dockerfile template for a new agent container |
| [stubs/docker-compose-cx-snippet.yml](stubs/docker-compose-cx-snippet.yml) | docker-compose service block template |
| [stubs/start.sh](stubs/start.sh) | Launcher script template (standby + ask + status) |
| [stubs/validate_new_agent.sh](stubs/validate_new_agent.sh) | Auto-test script: smoke-tests a new agent against C1+C3 |

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Your AI Agent (Cx)                                             │
│  e.g. Aider, Claude Code, KiloCode, Hermes, any OpenAI client  │
│                                                                 │
│  OPENAI_API_BASE=http://app:8000/v1    ← OpenAI format          │
│  ANTHROPIC_BASE_URL=http://app:8000   ← Anthropic format        │
│  X-Agent-ID: cx-myagent               ← sticky session tag      │
└────────────────────┬────────────────────────────────────────────┘
                     │ HTTP POST /v1/chat/completions
                     │       or /v1/messages
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  C1: copilot-api  (http://localhost:8000)                       │
│  FastAPI — OpenAI + Anthropic compatible                        │
│  Per-agent session registry, connection pool, circuit breaker   │
└────────────────────┬────────────────────────────────────────────┘
                     │ POST http://browser-auth:8001/chat
                     │ (M365 provider path)
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  C3: browser-auth  (http://localhost:8001)                      │
│  Playwright headless Chrome + noVNC                             │
│  PagePool: 6 pre-warmed M365 Copilot tabs                       │
└────────────────────┬────────────────────────────────────────────┘
                     │ Playwright browser interaction
                     ▼
             M365 Copilot Chat
     (m365.cloud.microsoft/chat via SignalR WS)
```

---

## Key URLs (all containers running)

| Service | URL | Purpose |
|---------|-----|---------|
| C1 API | `http://localhost:8000` | OpenAI-compatible inference endpoint |
| C1 Health | `http://localhost:8000/health` | Liveness check |
| C1 Sessions | `http://localhost:8000/v1/sessions` | Active agent sessions |
| C3 Browser UI | `http://localhost:6080` | noVNC — sign in to M365 here |
| C3 API | `http://localhost:8001` | Cookie/pool management API |
| C3 Status | `http://localhost:8001/status` | Pool + browser status |
| C9 Dashboard | `http://localhost:6090` | Validation console |
| C9 Pairs UI | `http://localhost:6090/pairs` | Run all agents in parallel |
