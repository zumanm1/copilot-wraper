# C8 Hermes Agent Setup and Integration Guide

## Overview

C8 runs the [Nous Research Hermes Agent](https://github.com/NousResearch/hermes-agent) v2026.3.17 — a long-lived personal AI agent with persistent memory, installable skills, cron scheduling, and MCP support. All LLM inference is routed through C1 (copilot-api) via the OpenAI-compatible `/v1/chat/completions` endpoint.

## Architecture

```
C8 Hermes Agent CLI
  │
  │  OPENAI_BASE_URL=http://app:8000/v1
  │  POST /v1/chat/completions (OpenAI format)
  │  X-Agent-ID: c8-hermes
  │
  ▼
C1 copilot-api
  │  Dedicated session for c8-hermes
  │
  ▼
C3 browser-auth
  │  POST /chat → Playwright types into M365 Copilot UI
  │  Intercepts SignalR WS from substrate.office.com
  │  Returns response text
  │
  ▼
C1 formats response as OpenAI chat completion
  → returns to Hermes CLI
```

## Prerequisites

1. All core containers running: `docker compose up -d app browser-auth`
2. C3 browser-auth with active M365 session (sign in via http://localhost:6080)
3. C1 API healthy: `curl http://localhost:8000/health`

## Quick Start

```bash
# 1. Start C8 in standby mode (default)
docker compose up -d hermes-agent

# 2. Test connectivity
docker compose run --rm hermes-agent status

# 3. One-shot question via ask helper (fast, uses C1 directly)
docker compose run --rm hermes-agent ask "What is 2+2?"

# 4. One-shot question via Hermes native CLI (uses Hermes agent features)
docker compose run --rm hermes-agent hermes-chat "What is 2+2?"

# 5. Interactive Hermes agent session
docker compose run --rm hermes-agent hermes
# Or attach to the running standby container:
docker compose exec C8_hermes-agent hermes

# 6. Bash shell with helpers
docker compose run --rm hermes-agent bash
```

## Two Ask Modes

| Mode | Command | Path | Features |
|------|---------|------|----------|
| `ask` | `docker compose run --rm hermes-agent ask "q"` | ask_helper.py → C1 `/v1/chat/completions` | Fast, one-shot, no agent features |
| `hermes-chat` | `docker compose run --rm hermes-agent hermes-chat "q"` | Hermes CLI → `OPENAI_BASE_URL` → C1 | Full agent: memory, tools, skills |

## Configuration

### Environment Variables (set in docker-compose.yml)

| Variable | Value | Purpose |
|----------|-------|---------|
| `OPENAI_BASE_URL` | `http://app:8000/v1` | Routes Hermes inference to C1 |
| `OPENAI_API_KEY` | `not-needed` | Placeholder (C1 bypasses validation) |
| `HERMES_INFERENCE_PROVIDER` | `openai` | Forces OpenAI-compat path, skips OpenRouter/Nous auth |
| `LLM_MODEL` | `copilot` | Model name visible in C1 logs |
| `HERMES_HOME` | `/root/.hermes` | Persistent config dir (named volume) |
| `TERMINAL_ENV` | `local` | Run terminal commands inside container (no nested Docker) |
| `AGENT_ID` | `c8-hermes` | Session isolation in C1 |

### Key Files

| File | Purpose |
|------|---------|
| `Dockerfile.hermes` | Builds C8 image: Python 3.11, uv, Hermes v2026.3.17, extras [cli,mcp,pty,cron] |
| `hermes-agent/start.sh` | Entrypoint: banner, ask, hermes-chat, status, standby health server |
| `hermes-agent/hermes-config.yaml` | Pre-seeded config: local terminal backend, compact display, compression |

### Hermes Config (hermes-config.yaml)

```yaml
terminal:
  backend: local          # Run commands inside this container
display:
  tool_progress: compact  # Compact output for container stdout
session_idle_minutes: 120 # Auto-expire idle sessions
compression:
  enabled: true
  threshold: 0.70         # Summarise when context hits 70%
```

### Persistent Storage

C8 uses a Docker named volume `hermes-config` mounted at `/root/.hermes`:
- `memories/` — Persistent memory across sessions
- `skills/` — Installed/improvable skills
- `sessions/` — Conversation history
- `cron/` — Scheduled tasks
- `config.yaml` — Agent configuration

Data survives container restarts and rebuilds.

## Hermes Interactive Commands

Once inside the Hermes CLI (`hermes` or `docker compose exec C8_hermes-agent hermes`):

| Command | Description |
|---------|-------------|
| `/memory` | View/manage persistent memories |
| `/skills` | List/install/improve skills |
| `/cron` | Manage scheduled tasks |
| `/tools` | Configure available tools |
| `/exit` | Exit the CLI |
| `hermes doctor` | Run diagnostics |
| `hermes model` | Select LLM provider/model |

## Troubleshooting

### Issue: 500 error / empty response
**Cause**: C3 browser session expired (M365 goes idle).
**Fix**: Re-navigate C3: `curl -X POST http://localhost:8001/navigate -H 'Content-Type: application/json' -d '{"url":"https://m365.cloud.microsoft"}'`

### Issue: Hermes refuses to respond / safety guardrails
**Cause**: M365 Copilot has content guardrails that reject certain prompt patterns.
**Fix**: Rephrase the question naturally. Exact-reply instructions (e.g. "Reply with exactly: X") are sometimes blocked.

### Issue: hermes-chat timeout
**Cause**: Hermes sends a system prompt + conversation history that may be large.
**Fix**: The C1 system prompt truncation (500 chars) applies to the Anthropic `/v1/messages` path only. For OpenAI `/v1/chat/completions`, messages pass through normally. If timeouts occur, check C3 browser state.

### Issue: "hermes: command not found"
**Cause**: Image not built or symlink missing.
**Fix**: `docker compose build hermes-agent && docker compose up -d hermes-agent`
