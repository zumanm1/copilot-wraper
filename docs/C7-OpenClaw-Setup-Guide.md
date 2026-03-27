# C7 — OpenClaw Setup Guide (Gateway + CLI)

> **Last updated: 2026-03-27**
> Containers C7a (`openclaw-gateway`) and C7b (`openclaw-cli`) — OpenClaw AI agent framework routed through C1 to Microsoft Copilot.

---

## Overview

OpenClaw is split across two containers:

| Container | Service name | Port | Role |
|---|---|---|---|
| **C7a** `C7a_openclaw-gateway` | `openclaw-gateway` | **18789** (host + internal) | WebSocket gateway — multiplexes CLI clients through C1 |
| **C7b** `C7b_openclaw-cli` | `openclaw-cli` | 8080 (internal health) | CLI / TUI — connects to C7a gateway |

**Agent ID:** `c7-openclaw`
**Base image:** `node:22-alpine` (requires Node ≥ 22.16 — native addon compilation)
**Connects to:** C7a → C1 at `http://app:8000/v1`

---

## Architecture

```
C7b openclaw-cli (TUI)
  └── OPENCLAW_GATEWAY_URL=ws://openclaw-gateway:18789
  └── OPENCLAW_GATEWAY_TOKEN=copilot-local-gateway-token
        └── WebSocket connection to C7a
              └── C7a openclaw-gateway
                    └── OPENCLAW_PROVIDER_BASE_URL=http://app:8000/v1
                    └── OPENCLAW_PROVIDER_API_KEY=sk-not-needed
                          └── POST http://app:8000/v1/chat/completions
                                Header: X-Agent-ID: c7-openclaw
                                Body: {model:"copilot", messages:[...]}
                                      └── C1 → CopilotBackend["c7-openclaw"] → Copilot
```

C7a acts as a **multiplexing WebSocket hub**. Multiple C7b instances (or external OpenClaw SDK clients) can connect to C7a simultaneously — all requests funnel through C7a's connection pool to C1.

---

## Quick Start

```bash
# Start both C7a and C7b
docker compose up openclaw-gateway openclaw-cli -d

# Verify C7a gateway health
curl http://localhost:18789/healthz
# → {"status":"ok"} or {"alive":true}

# Verify C7b health
docker compose exec openclaw-cli curl -sf http://localhost:8080/health
# → ok

# Start interactive OpenClaw TUI
docker compose exec openclaw-cli openclaw
```

---

## C7a: Gateway — Environment Variables

Set automatically by `docker-compose.yml`:

| Variable | Value | Description |
|---|---|---|
| `OPENCLAW_PROVIDER_BASE_URL` | `http://app:8000/v1` | Routes all AI calls to C1 |
| `OPENCLAW_PROVIDER_API_KEY` | `sk-not-needed` | Placeholder key (C1 ignores it) |
| `OPENCLAW_GATEWAY_TOKEN` | `copilot-local-gateway-token` | Auth token for CLI connections |
| `OPENCLAW_TZ` | `Africa/Johannesburg` | Gateway timezone |
| `AGENT_ID` | `c7-openclaw` | Agent ID sent to C1 as `X-Agent-ID` |

---

## C7b: CLI — Environment Variables

| Variable | Value | Description |
|---|---|---|
| `OPENCLAW_GATEWAY_URL` | `ws://openclaw-gateway:18789` | Points to C7a |
| `OPENCLAW_GATEWAY_TOKEN` | `copilot-local-gateway-token` | Must match C7a token |
| `API_URL` | `http://app:8000` | C1 base URL for direct health checks |
| `TZ` | `Africa/Johannesburg` | CLI timezone |
| `AGENT_ID` | `c7-openclaw` | Agent ID used in C9 |

---

## Container Commands

### C7a Gateway

```bash
# Check gateway health
curl http://localhost:18789/healthz

# View gateway logs
docker compose logs openclaw-gateway --tail 50

# Restart gateway
docker compose restart openclaw-gateway
```

### C7b CLI

```bash
# Start interactive OpenClaw TUI
docker compose exec openclaw-cli openclaw

# One-shot question (via ask_helper.py in /workspace)
docker compose exec openclaw-cli ask "Review this Python script"

# Direct bash shell
docker compose exec openclaw-cli bash

# Check health
docker compose exec openclaw-cli curl -sf http://localhost:8080/health
```

---

## Calling C7 via C1 (External)

The `c7-openclaw` session is accessible from any OpenAI-compatible client:

```bash
# curl
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c7-openclaw" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello from C7"}]}'

# Python (OpenAI SDK)
import openai
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed",
    default_headers={"X-Agent-ID": "c7-openclaw"}
)
response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Hello"}]
)
print(response.choices[0].message.content)
```

---

## Using an External OpenClaw Client

Any OpenClaw SDK client can connect to C7a directly:

```javascript
// Node.js / TypeScript example
const OpenClaw = require('openclaw');

const client = new OpenClaw.Client({
  gatewayUrl: 'ws://localhost:18789',
  token: 'copilot-local-gateway-token'
});

const response = await client.chat('What is the meaning of life?');
console.log(response);
```

---

## Gateway Token

The `OPENCLAW_GATEWAY_TOKEN` must match between C7a and C7b (and any external clients). Both are set to `copilot-local-gateway-token` by default in `docker-compose.yml`.

To change the token, update both services in `docker-compose.yml` and rebuild:
```yaml
# In docker-compose.yml, both openclaw-gateway and openclaw-cli:
OPENCLAW_GATEWAY_TOKEN=my-custom-token
```
```bash
docker compose up -d openclaw-gateway openclaw-cli
```

---

## Thinking Mode with C7

```bash
# Deep thinking mode via C1 directly
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c7-openclaw" \
  -H "X-Chat-Mode: deep" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Design a microservices architecture"}]}'
```

---

## Validating C7 via C9

From the C9 validation console (`http://localhost:6090`):

1. **Chat page** (`/chat`): Select "C7b OpenClaw" from the agent dropdown
2. **Pairs page** (`/pairs`): Include C7 in a batch run
3. **Logs page** (`/logs`): Filter by `agent_id = c7-openclaw`

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `curl http://localhost:18789/healthz` fails | C7a not running or port mismatch | `docker compose up openclaw-gateway -d` |
| C7b can't connect to gateway | Token mismatch | Verify `OPENCLAW_GATEWAY_TOKEN` matches in both services |
| Native addon compile error | Node.js < 22 | Image uses `node:22-alpine`; ensure no base image override |
| Empty responses | Cookies expired | Re-run C3 extraction flow |
| Container unhealthy after restart | Gateway startup race | `docker compose restart openclaw-gateway openclaw-cli` |
| `c7-openclaw` session expired | 30 min idle TTL | New session auto-created on next request |

---

## Related Guides

- [C2 Aider Setup Guide](C2-Aider-Setup-Guide.md) — Another OpenAI-format agent
- [Architecture Deep-Dive](../API-DOCUMENTATION/01-architecture-deep-dive.md) — Full system architecture
- [Agent ID & Routing](../API-DOCUMENTATION/03-agent-id-and-routing.md) — Session isolation details
- [API Reference](../API-DOCUMENTATION/04-api-reference.md) — C1/C3/C9 endpoint reference
