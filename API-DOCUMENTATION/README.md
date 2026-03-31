# API-DOCUMENTATION — Copilot OpenAI-Compatible API Wrapper

> **Last updated: 2026-03-27**

This folder is the complete reference for the full 9-container stack — architecture, authentication, agent routing, API endpoints, and testing. It also covers APP2 (the C9 validation console) and includes setup guides for every agent container.

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

C1 and C3 require **no code changes**.

---

## Document Index

### Core Reference (API-DOCUMENTATION/)

| # | File | What it covers |
|---|------|----------------|
| 01 | [Architecture Deep-Dive](01-architecture-deep-dive.md) | All 9 containers, APP1/APP2 split, communication flows, protocols, networking |
| 02 | [Authentication Flow](02-authentication-flow.md) | Cookie auth, portal profiles, X-Chat-Mode vs X-Work-Mode, config reload |
| 03 | [Agent ID & Routing](03-agent-id-and-routing.md) | X-Agent-ID header, session registry, lock hierarchy, PagePool sticky tabs |
| 04 | [API Reference](04-api-reference.md) | All C1/C3/C9 endpoints with request/response schemas and headers |
| 05 | [New Agent Integration Steps](05-new-agent-integration-steps.md) | Step-by-step guide to add any new AI agent container |
| 06 | [Testing & Validation](06-testing-validation.md) | curl smoke tests, thinking mode, file upload, C9 console, unit tests, E2E |

### Agent Setup Guides (docs/)

| File | Container | What it covers |
|------|-----------|----------------|
| [C2-Aider-Setup-Guide.md](../docs/C2-Aider-Setup-Guide.md) | C2 | Aider + OpenCode coding agents, M365 auth, troubleshooting |
| [C5-Claude-Code-Setup-Guide.md](../docs/C5-Claude-Code-Setup-Guide.md) | C5 | Claude Code CLI routing via Anthropic format, credential bypass |
| [C6-KiloCode-Setup-Guide.md](../docs/C6-KiloCode-Setup-Guide.md) | C6 | KiloCode CLI, OpenAI format, workspace, thinking mode |
| [C7-OpenClaw-Setup-Guide.md](../docs/C7-OpenClaw-Setup-Guide.md) | C7a+C7b | OpenClaw gateway + CLI, token auth, WebSocket multiplexing |
| [C8-Hermes-Setup-Guide.md](../docs/C8-Hermes-Setup-Guide.md) | C8 | Hermes persistent-memory agent, skills, cron, MCP |
| [C9-Validation-Console-Guide.md](../docs/C9-Validation-Console-Guide.md) | C9 (APP2) | Full C9 guide — UI/UX, backend, database, API, features in depth |

### Installation & Operations

| File | What it covers |
|------|----------------|
| [INSTALL.md](../INSTALL.md) | Fresh-machine install guide — macOS + Linux, all 9 containers |
| [README.md](../README.md) | Main project README — Quick Start, all sections, container reference |
| [docs/validation-runbook.md](../docs/validation-runbook.md) | Test runbook — infrastructure checks, pool reset, known issues |

### Stubs (for new agent containers)

| File | Purpose |
|------|---------|
| [stubs/Dockerfile.cx-stub](stubs/Dockerfile.cx-stub) | Dockerfile template (3 sections to customise) |
| [stubs/docker-compose-cx-snippet.yml](stubs/docker-compose-cx-snippet.yml) | docker-compose service block template |
| [stubs/start.sh](stubs/start.sh) | Launcher script template (standby + ask + status) |
| [stubs/validate_new_agent.sh](stubs/validate_new_agent.sh) | Auto-validation script: 4-step health + roundtrip test |

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  APP1 — API & Agent Layer (C1–C8)                                            │
│                                                                              │
│  AI Agent (any OpenAI or Anthropic client)                                   │
│     OPENAI_API_BASE=http://app:8000/v1    ← OpenAI format (C2, C6, C7, C8)   │
│     ANTHROPIC_BASE_URL=http://app:8000   ← Anthropic format (C5)             │
│     X-Agent-ID: cx-myagent               ← sticky session isolation          │
│     X-Chat-Mode: auto|quick|deep         ← thinking depth                    │
│     X-Work-Mode: work|web                ← M365 data scope (M365 only)       │
│                          │                                                   │
│                          ▼                                                   │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  C1: copilot-api  :8000                                                │  │
│  │  FastAPI — per-agent session registry — response cache — circuit       │  │
│  │  breaker — OpenAI + Anthropic format — file upload (/v1/files)         │  │
│  └───────────────────┬─────────────────────────────────────────────────── ┘  │
│                       │                                                      │
│         ┌─────────────┴──────────────┐                                      │
│         │ consumer profile           │ m365_hub profile                      │
│         ▼                            ▼                                      │
│  WSS copilot.microsoft.com    C3: browser-auth :8001 / :6080                │
│  (Cookie: COPILOT_COOKIES)    Playwright PagePool(6) → M365 Copilot         │
│                               SignalR WS (substrate.office.com)             │
│                                                                              │
│  Agent Containers (all route through C1):                                   │
│    C2  Aider + OpenCode  →  OpenAI /v1/chat/completions                     │
│    C5  Claude Code       →  Anthropic /v1/messages                          │
│    C6  KiloCode          →  OpenAI /v1/chat/completions                     │
│    C7a OpenClaw Gateway  →  :18789 WebSocket hub                            │
│    C7b OpenClaw CLI      →  via C7a                                         │
│    C8  Hermes Agent      →  OpenAI /v1/chat/completions                     │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  APP2 — Validation Console (C9)                                              │
│                                                                              │
│  C9: c9-jokes  :6090                                                         │
│  FastAPI + Jinja2 + SQLite (c9-data volume)                                  │
│                                                                              │
│  /            Dashboard    — health cards for all containers                 │
│  /chat        Chat UI      — single agent, live streaming, file upload        │
│  /pairs       Pairs UI     — batch multi-agent (parallel / sequential)       │
│  /logs        Logs         — full audit trail (source, elapsed_ms)           │
│  /health      Health       — container health snapshots                      │
│  /sessions    Sessions     — live proxy of C1 /v1/sessions                  │
│  /api         API reference — server-rendered ( /api/docs → redirect )       │
│                                                                              │
│  Connects to C1 (chat, file upload)                                          │
│  Probes C2–C8 health endpoints                                               │
│  Writes all results to SQLite c9.db                                          │
│  Never modifies C1–C8                                                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Key URLs (all containers running)

### APP1 — API & Agent Layer

| Service | URL | Purpose |
|---------|-----|---------|
| C1 API | `http://localhost:8000` | OpenAI + Anthropic inference endpoint |
| C1 Health | `http://localhost:8000/health` | Liveness check |
| C1 Swagger | `http://localhost:8000/docs` | Interactive API browser |
| C1 Sessions | `http://localhost:8000/v1/sessions` | Active per-agent sessions |
| C1 Cache | `http://localhost:8000/v1/cache/stats` | Response cache statistics |
| C3 noVNC | `http://localhost:6080` | Remote browser — sign in to Copilot here |
| C3 API | `http://localhost:8001` | Cookie extraction + pool management |
| C3 Status | `http://localhost:8001/status` | Browser + pool status |
| C3 Session Health | `http://localhost:8001/session-health` | M365 session validity |
| C7a Gateway | `http://localhost:18789` | OpenClaw WebSocket gateway |

### APP2 — Validation Console

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `http://localhost:6090/` | All container health at a glance |
| Chat | `http://localhost:6090/chat` | Interactive single-agent chat with live token streaming |
| Pairs | `http://localhost:6090/pairs` | Batch multi-agent validation |
| Logs | `http://localhost:6090/logs` | Full audit trail |
| Health | `http://localhost:6090/health` | Health snapshot history |
| Sessions | `http://localhost:6090/sessions` | C1 session proxy |
| API reference | `http://localhost:6090/api` | C9 API reference (`/api/docs` redirects) |
