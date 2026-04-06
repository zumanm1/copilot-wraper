# C6 — KiloCode CLI Setup Guide

> **Last updated: 2026-03-27**
> Container C6 (`kilocode-terminal`) — KiloCode AI coding agent routed through C1 to Microsoft Copilot.

---

## Overview

| Property | Value |
|---|---|
| **Container name** | `C6_kilocode` |
| **Service name** | `kilocode-terminal` |
| **Agent ID** | `c6-kilocode` |
| **Base image** | `node:20-alpine` |
| **API format** | OpenAI `POST /v1/chat/completions` |
| **Routes to** | C1 at `http://app:8000/v1` |
| **Health port** | 8080 (internal only) |
| **Workspace** | `/workspace` (shared with all agent containers) |

KiloCode is a terminal-first AI coding assistant. In this stack, it connects to C1 (which proxies all requests to Microsoft Copilot) instead of a real OpenAI or Anthropic endpoint.

---

## Quick Start

```bash
# Start C6 (if not already running)
docker compose up kilocode-terminal -d

# Verify it is healthy
docker compose exec kilocode-terminal curl -sf http://localhost:8080/health
# → ok

# Start an interactive KiloCode session
docker compose exec kilocode-terminal kilocode

# One-shot question
docker compose exec kilocode-terminal ask "Explain this Python file"
```

---

## How C6 Connects to C1

C6's environment is pre-configured to route all AI calls to C1:

```
kilocode CLI
  └── OPENAI_API_BASE=http://app:8000/v1
  └── OPENAI_API_KEY=not-needed
  └── KILO_MODEL=copilot
        └── POST http://app:8000/v1/chat/completions
              Header: X-Agent-ID: c6-kilocode
              Body: {model:"copilot", messages:[...]}
                    └── C1 → CopilotBackend["c6-kilocode"] → Copilot
```

No real OpenAI API key is needed. The `not-needed` placeholder satisfies KiloCode's key requirement.

---

## Environment Variables

Set automatically by `docker-compose.yml`:

| Variable | Value | Description |
|---|---|---|
| `OPENAI_API_BASE` | `http://app:8000/v1` | Routes all calls to C1 |
| `OPENAI_API_KEY` | `not-needed` | Placeholder (C1 ignores it) |
| `KILO_MODEL` | `copilot` | Model name used in requests |
| `DO_NOT_TRACK` | `1` | Disables KiloCode telemetry |
| `AGENT_ID` | `c6-kilocode` | Passed as `X-Agent-ID` header |

---

## Container Commands

```bash
# Interactive KiloCode coding session
docker compose exec kilocode-terminal kilocode

# One-shot question (via ask_helper.py in /workspace)
docker compose exec kilocode-terminal ask "Write unit tests for calculator.py"

# Direct bash shell
docker compose exec kilocode-terminal bash

# Check health
docker compose exec kilocode-terminal curl -sf http://localhost:8080/health
```

---

## Calling C6 via C1 (External)

You can send requests to C6's isolated session from any OpenAI-compatible client:

```bash
# curl
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c6-kilocode" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello from C6"}]}'

# Python (OpenAI SDK)
import openai
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed",
    default_headers={"X-Agent-ID": "c6-kilocode"}
)
response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Refactor this code"}]
)
print(response.choices[0].message.content)
```

---

## Using Thinking Mode with C6

Add `X-Chat-Mode` to control reasoning depth:

```bash
# Deep thinking mode
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c6-kilocode" \
  -H "X-Chat-Mode: deep" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Design a REST API for a to-do app"}]}'
```

| X-Chat-Mode | Copilot style | Best for |
|---|---|---|
| `auto` | smart | General coding questions |
| `quick` | balanced | Fast syntax lookups |
| `deep` | reasoning | Architecture and design decisions |

---

## Workspace

C6 shares `/workspace` with all other agent containers. Files created by KiloCode are accessible from C2 (Aider), C5 (Claude Code), C8 (Hermes), etc.:

```bash
# List workspace contents from C6
docker compose exec kilocode-terminal ls /workspace

# Run KiloCode on a specific file
docker compose exec kilocode-terminal bash -c "cd /workspace && kilocode calculator.py"
```

---

## Validating C6 via C9

From the C9 validation console (`http://localhost:6090`):

1. **Chat page** (`/chat`): Select "C6 KiloCode (OpenAI)" from the agent dropdown, type a prompt, click Send
2. **Pairs page** (`/pairs`): Include C6 in a batch run to compare its response alongside other agents
3. **Logs page** (`/logs`): Filter by `agent_id = c6-kilocode` to see all C6 interactions

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `kilocode: not found` | Image not built | `docker compose build kilocode-terminal` |
| Empty responses | Cookies expired | Re-run C3 extraction, `POST /v1/reload-config` |
| `OPENAI_API_BASE` not respected | KiloCode config override | Check `/workspace/.kilocode/config.json` — should not override base URL |
| Container unhealthy | Health server not running | `docker compose restart kilocode-terminal` |
| Session context lost | AGENT_SESSION_TTL expired (30 min idle) | New session created automatically on next request |

---

## Related Guides

- [C2 Aider Setup Guide](C2-Aider-Setup-Guide.md) — Similar OpenAI-format coding agent
- [C5 Claude Code Setup Guide](C5-Claude-Code-Setup-Guide.md) — Anthropic-format coding agent
- [API Reference](../API-DOCUMENTATION/04-api-reference.md) — Full C1 endpoint documentation
- [Agent ID & Routing](../API-DOCUMENTATION/03-agent-id-and-routing.md) — How X-Agent-ID session isolation works
