# C5 Claude Code Setup and Integration Guide

## Overview

C5 (claude-code-terminal) runs the official Anthropic Claude Code CLI, routed through C1 (copilot-api) so all LLM inference is served by the local M365 Copilot pipeline (C1 → C3 → M365).

Claude Code thinks it's talking to Anthropic's API, but `ANTHROPIC_BASE_URL` redirects every `/v1/messages` call to C1, which translates and proxies through C3's browser automation.

## Architecture

```
C5 Claude Code CLI
  │
  │  ANTHROPIC_BASE_URL=http://app:8000
  │  POST /v1/messages (Anthropic format)
  │
  ▼
C1 copilot-api
  │  _anthropic_messages_to_prompt()
  │  → truncates large system prompts (>500 chars)
  │  → extracts user message text
  │
  ▼
C3 browser-auth
  │  POST /chat  →  Playwright types into M365 Copilot UI
  │  Intercepts SignalR WS from substrate.office.com
  │  Returns response text
  │
  ▼
C1 formats response as Anthropic Messages API schema
  → returns to Claude Code CLI
```

## Prerequisites

1. All containers running: `docker compose ps`
2. C3 browser-auth with active M365 session (sign in via http://localhost:6080)
3. C1 API healthy: `curl http://localhost:8000/health`

## Quick Start

```bash
# 1. Start containers
docker compose up -d

# 2. Authenticate M365 (if not already done)
open http://localhost:6080
# Sign in with your Microsoft 365 credentials in the noVNC browser

# 3. Test connectivity
docker compose run --rm claude-code-terminal status

# 4. One-shot question via ask helper
docker compose run --rm claude-code-terminal ask "What is 2+2?"

# 5. One-shot question via Claude Code CLI
docker compose run --rm claude-code-terminal bash -c 'claude -p "What is 2+2?"'

# 6. Interactive Claude Code session
docker compose run --rm claude-code-terminal
# Then type: claude
```

## How It Works

### Authentication Bypass

Claude Code v2.x requires interactive login before accepting commands. The C5 Docker image pre-seeds `~/.claude/.credentials.json` and `~/.claude/settings.json` with placeholder OAuth tokens so the login prompt is skipped entirely. C1 does not validate API keys — the placeholder key `sk-ant-not-needed-xxxxxxxxxxxxx` is sufficient.

### System Prompt Handling

Claude Code sends a ~20 KB system prompt with every request. C1 truncates this to 500 chars to avoid C3 browser proxy timeouts. M365 Copilot doesn't use system prompts, so no functionality is lost.

### Permissive Request Parsing

Claude Code sends Anthropic-specific fields (tools, metadata, tool_use blocks) that C1 doesn't need. The Pydantic models accept and silently ignore these extra fields, extracting only text content from messages.

### Per-Agent Session Isolation

C5 sends `X-Agent-ID: c5-claude-code` with every request. C1 routes this to a dedicated backend session, isolated from C2 Aider and other agents.

## Configuration

### Environment Variables (set in docker-compose.yml)

| Variable | Value | Purpose |
|----------|-------|---------|
| `ANTHROPIC_BASE_URL` | `http://app:8000` | Routes Claude Code to C1 |
| `ANTHROPIC_API_KEY` | `sk-ant-not-needed-xxx` | Placeholder (C1 bypasses validation) |
| `AGENT_ID` | `c5-claude-code` | Session isolation in C1 |
| `DO_NOT_TRACK` | `1` | Disable telemetry |

### Key Files

| File | Purpose |
|------|---------|
| `Dockerfile.claude-code` | Builds C5 image, installs Claude Code, pre-seeds credentials |
| `claude-code-terminal/start.sh` | Entrypoint: banner, ask, status, claude CLI routing |
| `server.py` `/v1/messages` | C1 Anthropic-compatible endpoint |
| `models.py` `AnthropicRequest` | Permissive Pydantic model for Claude Code requests |

## Available Commands

```bash
# Health check
docker compose run --rm claude-code-terminal status

# One-shot ask (uses ask_helper.py → /v1/messages)
docker compose run --rm claude-code-terminal ask "your question"

# Claude Code CLI (non-interactive, print mode)
docker compose run --rm claude-code-terminal bash -c 'claude -p "your question"'

# Claude Code CLI (interactive)
docker compose run --rm claude-code-terminal
# Then type: claude

# Version check
docker compose run --rm claude-code-terminal bash -c 'claude --version'

# Shell access
docker compose run --rm claude-code-terminal bash
```

## Troubleshooting

### Issue: Claude Code shows login prompt
**Cause**: Image was not rebuilt after Dockerfile change.
**Fix**: `docker compose build claude-code-terminal && docker compose up -d claude-code-terminal`

### Issue: 500 error / empty response
**Cause**: C3 browser session expired (M365 goes idle).
**Fix**:
1. Open http://localhost:6080 (noVNC)
2. Re-navigate to M365: `curl -X POST http://localhost:8001/navigate -H 'Content-Type: application/json' -d '{"url":"https://m365.cloud.microsoft"}'`
3. Retry the request

### Issue: TimeoutError from C1
**Cause**: System prompt too large or C3 proxy slow.
**Fix**: The 500-char system prompt cap (in `server.py`) prevents this. If it recurs, check C3 browser state.

### Issue: 422 Validation Error
**Cause**: Claude Code sent a request field C1 doesn't expect.
**Fix**: The permissive Pydantic models (`extra="allow"`) prevent this. If it recurs, check `models.py`.
