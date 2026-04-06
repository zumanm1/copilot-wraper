# C2 Aider Setup and Authentication Guide

## Overview

C2 (agent-terminal) runs Aider AI coding agent that connects to C1 (copilot-api) for LLM inference. C1 supports two modes:

- **Consumer mode** (`consumer` profile): C1 connects directly to `copilot.microsoft.com` via WebSocket using cookies extracted by C3
- **M365 mode** (`m365_hub` profile): C1 proxies each prompt through C3's `/chat` endpoint, which uses Playwright to submit it through the real M365 Copilot web UI and intercepts the SignalR WebSocket response

In M365 mode, C3 acts as a **live browser proxy** — the authenticated browser session is used for every chat request.

## Prerequisites

1. All containers running: `docker compose ps`
2. C3 browser-auth accessible at: http://localhost:6080
3. C1 API server at: http://localhost:8000

## Authentication Process

### Step 1: Access Browser UI
```bash
# Open noVNC browser in your web browser
open http://localhost:6080
```

### Step 2: Complete M365 Authentication
1. In the noVNC browser window, navigate to `https://m365.cloud.microsoft`
2. Complete full Microsoft 365 login with your credentials
3. Wait for successful authentication (you should see the M365 interface)

### Step 3: Extract Cookies
```bash
cd copilot-openai-wrapper
curl -X POST http://localhost:8001/extract
```

Expected response:
```json
{
  "status": "ok",
  "authenticated": true,
  "mode": "authenticated",
  "cookies_extracted": 14,
  "cookie_names": ["OH.FLID", "MSFPC", "OH.SID", "OH.DCAffinity", ...],
  "message": "Extracted 14 cookies in authenticated mode (OH.SID present)."
}
```

### Step 4: Reload C1 Configuration
```bash
curl -X POST http://localhost:8000/v1/reload-config
```

### Step 5: Test C1 API
```bash
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/copilot","messages":[{"role":"user","content":"test"}],"stream":false}'
```

### Step 6: Start C2 Aider
```bash
# Option 1: Direct Aider launch
docker compose run --rm agent-terminal aider

# Option 2: Interactive shell first
docker compose run --rm agent-terminal bash
# Then run: aider

# Option 3: Exec into running C2
docker compose exec C2b_agent-terminal bash
# Then run: aider
```

## Configuration Details

### Environment Variables (.env)
```bash
# Portal profile — determines routing mode
COPILOT_PORTAL_PROFILE=m365_hub    # or: consumer
COPILOT_PROVIDER=m365              # or: auto, copilot

# Consumer mode only: cookies extracted by C3
COPILOT_COOKIES=...  # Updated by /extract endpoint

# M365 mode: C1 proxies chat through C3 browser session
# C3_URL defaults to http://browser-auth:8001 (Docker internal)
```

### Aider Configuration (auto-configured)
- `OPENAI_API_BASE=http://app:8000/v1` (points to C1)
- `OPENAI_API_KEY=not-needed` (bypassed by custom base URL)
- `AIDER_MODEL=openai/copilot`
- `AGENT_ID=c2-aider` (routes to dedicated C1 backend)

## Troubleshooting

### Issue: "M365 conversation bootstrap redirected to Microsoft login/OAuth"
**Solution**: Complete authentication in noVNC browser at http://localhost:6080

### Issue: "Copilot verification required (hashcash challenge)"
**Solution**: 
1. Navigate browser to https://copilot.microsoft.com
2. Complete any verification challenges
3. Re-extract cookies: `curl -X POST http://localhost:8001/extract`

### Issue: "NO _U COOKIE (Microsoft account auth)"
**Solution**: 
1. Open http://localhost:6080
2. Sign in with Microsoft account in browser
3. Re-extract cookies

### Issue: Aider can't connect to API
**Solution**: 
1. Check C1 health: `curl http://localhost:8000/health`
2. Check C3 health: `curl http://localhost:8001/status`
3. Reload C1 config: `curl -X POST http://localhost:8000/v1/reload-config`

### Issue: "C3 /chat failed: No composer found" (M365 mode)
**Solution**: The M365 browser session expired. Re-authenticate:
1. Open http://localhost:6080 (noVNC)
2. Log into m365.cloud.microsoft with your Microsoft 365 credentials
3. Test: `curl -s -X POST http://localhost:8001/chat -H 'Content-Type: application/json' -d '{"prompt":"hello","timeout":30000}'`

### Issue: "Locator.type: Timeout 30000ms exceeded" (M365 mode)
**Solution**: This was fixed by using `execCommand('insertText')` for large prompts. If it recurs, ensure the browser session is active and the M365 chat page loads correctly in noVNC.

### Issue: M365 Copilot refuses system prompts
**Note**: M365 Copilot may reply "I can't follow system instructions directly" for Aider's system prompt. This is M365's content policy, not a code bug. The response will still be functional — M365 answers helpfully despite declining the system instruction.

## Architecture Flow

### Consumer mode
```
C2 (Aider) → C1 (API) ──WSS──► copilot.microsoft.com
                ▲
C3 (Browser Auth) ── cookies ──► .env ──► C1
```

### M365 mode (Phase B — current)
```
C2 (Aider) → C1 (API) ──POST /chat──► C3 (Browser Proxy)
                                         │
                                    Playwright types prompt
                                    into m365.cloud.microsoft/chat
                                         │
                                    SignalR WS response from
                                    substrate.office.com
                                         │
                                    C3 parses type=2 frame
                                    returns text to C1
```

### Key technical details (M365 mode)

| Component | Detail |
|---|---|
| **SignalR protocol** | Frames delimited by `\x1e`; bot response in `type=2` completion frame `item.messages[].text` where `author != "user"` |
| **Text input** | `document.execCommand('insertText')` for prompts >200 chars (instant, React-compatible); Playwright `.type()` for short prompts |
| **Submit** | `page.keyboard.press("Enter")` — triggers React's synthetic event system |
| **Page lifecycle** | Navigate to `about:blank` then `/chat` before each message (forces full SPA teardown, prevents stale state) |
| **Composer detection** | `wait_for_selector('[role="textbox"][contenteditable="true"]')` — waits for React hydration |

## Container Status Commands

```bash
# Check all containers
docker compose ps

# Check C1 API health
curl http://localhost:8000/health

# Check C3 browser status
curl http://localhost:8001/status

# Test C2 connectivity
docker compose run --rm agent-terminal status
```

## Related Guides

- [C5 Claude Code Setup Guide](C5-Claude-Code-Setup-Guide.md) — Anthropic `/v1/messages` path
- [C8 Hermes Agent Setup Guide](C8-Hermes-Setup-Guide.md) — persistent memory, skills, cron
- [M365 Network Notes](copilot-m365-network-notes.md) — SignalR protocol details

## Quick Start Sequence

```bash
# 1. Start containers
docker compose up -d

# 2. Authenticate via browser
open http://localhost:6080
# Complete M365 login in browser

# 3. Extract cookies
curl -X POST http://localhost:8001/extract

# 4. Reload C1 config
curl -X POST http://localhost:8000/v1/reload-config

# 5. Start Aider
docker compose run --rm agent-terminal aider
```
