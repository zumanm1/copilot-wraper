# 02 — Authentication Flow

## Overview

Authentication is **browser-session based**. There is no API key for the upstream M365 Copilot service. Instead:

1. A real Chromium browser (in C3) is signed in to M365 via the noVNC UI
2. Session cookies are extracted and written to `.env`
3. C1 reads those cookies and attaches them to every upstream request

There are two provider modes with different auth flows:

| Mode | Provider | How auth works |
|------|----------|---------------|
| `m365` (default) | `COPILOT_PROVIDER=m365` | C3 Playwright browser holds the live M365 session — cookies are used by the browser itself, not forwarded |
| `copilot` (consumer) | `COPILOT_PROVIDER=copilot` | Cookies extracted to `.env` → C1 sends them as HTTP `Cookie:` header on direct WebSocket calls |

The current deployment uses **`m365` mode** (`COPILOT_PORTAL_PROFILE=m365_hub`).

---

## M365 Auth Flow (Current Default)

```
Developer action:
  1. Open http://localhost:6080  (noVNC — C3's browser UI)
  2. Navigate to https://m365.cloud.microsoft/chat
  3. Sign in with M365 account
  4. Browser now has active session cookies (OH.SID, MSFPC, etc.)

Cookie extraction (optional but recommended for persistence):
  curl -X POST http://localhost:8001/extract
  → C3 reads cookies from the Playwright browser context
  → Writes COPILOT_COOKIES=... to /app/.env (shared volume)
  → C1 hot-reloads config (POST http://app:8000/v1/reload-config)

Runtime (every chat request):
  Agent → C1 POST /v1/chat/completions
       → C1._c3_proxy_call()
       → C3 POST /chat { prompt, agent_id, mode }
       → C3 Playwright browser (already signed in) types prompt
       → M365 Copilot responds via SignalR WS
       → C3 returns text to C1
       → C1 returns JSON to agent
```

In M365 mode, **the cookies never leave C3**. The Playwright browser is already authenticated; C1 only needs to call C3's `/chat` HTTP endpoint.

---

## Required M365 Cookies

C3 validates the session by checking for these cookies in the browser context:

| Cookie | Domain | Purpose |
|--------|--------|---------|
| `OH.SID` | `m365.cloud.microsoft` | M365 Copilot session ID (primary) |
| `MSFPC` | `*.microsoft.com` | Microsoft first-party cookie (secondary) |
| `OH.FLID` | `m365.cloud.microsoft` | Feature flags / load balancer |
| `OH.DCAffinity` | `m365.cloud.microsoft` | Datacenter affinity |

If `OH.SID` is absent, C3's `session-health` endpoint returns `session: "expired"`.

---

## Consumer (Copilot) Auth Flow

Used when `COPILOT_PROVIDER=copilot` or `COPILOT_PORTAL_PROFILE=consumer`.

```
1. Open http://localhost:6080 (noVNC)
2. Navigate to https://copilot.microsoft.com
3. Sign in
4. POST http://localhost:8001/extract
   → Extracts cookies: __Host-copilot-anon, MUID, _EDGE_S, MSFPC, _U (bing)
   → Writes to /app/.env as COPILOT_COOKIES=...

Runtime:
  Agent → C1 POST /v1/chat/completions
       → CopilotBackend._raw_copilot_call()
       → _make_cookie_header() reads COPILOT_COOKIES from env
       → POST https://copilot.microsoft.com/c/api/conversations
         Cookie: <extracted cookies>
       → WSS wss://copilot.microsoft.com/c/api/chat
         Cookie: <extracted cookies>
       → Streaming response chunks
```

---

## How Cookies Flow Through the Stack

```
User signs in via noVNC (C3 browser)
         │
         ▼
C3: context.cookies()  →  cookie string
         │
         ▼
/app/.env  (shared volume between C1 and C3)
  COPILOT_COOKIES=OH.SID=xxx; MSFPC=xxx; ...
         │
         ▼
C1: config.py  os.getenv("COPILOT_COOKIES")
         │
         ▼
copilot_backend._make_cookie_header()
  → returns "OH.SID=xxx; MSFPC=xxx; ..."
         │
         ▼
aiohttp request headers:
  Cookie: OH.SID=xxx; MSFPC=xxx; ...
  Origin: https://m365.cloud.microsoft
  Referer: https://m365.cloud.microsoft/chat
```

---

## X-Chat-Mode Header

The `X-Chat-Mode` header controls which M365 Copilot mode is used:

| Value | M365 UI Mode | Use case |
|-------|-------------|---------|
| `work` (default) | Work tab — accesses M365 data, calendar, email, files | Code agents, productivity queries |
| `web` | Web tab — general web search | Consumer-style queries |

Passed from agent → C1 → C3 → Playwright (clicks Work/Web toggle in M365 UI).

---

## Auth Dialog Handling (C3)

When M365 shows an "Authentication required" dialog (session expired mid-use):

1. C3 detects the `h2` element containing "Authentication required"
2. Clicks the "Continue" button (M365 auth gate)
3. Waits 8 seconds for re-authentication
4. Re-checks if the dialog is still present
5. If still blocked: returns `{"success": false, "error": "Authentication required..."}`

**Recovery:** Sign in again via noVNC at `http://localhost:6080` then retry.

---

## Session Health Check

```bash
curl http://localhost:8001/session-health
```

Response:
```json
{
  "session": "active",       // or "expired" / "unknown"
  "profile": "m365_hub",
  "reason": null,            // error string if not active
  "checked_at": "2026-03-27T...",
  "pool_warning": null,      // "pool_exhausted" if all tabs busy
  "chat_mode": "work"
}
```

```bash
curl http://localhost:8001/status
```

Response:
```json
{
  "status": "ok",
  "browser": "running",
  "open_pages": 7,
  "pool_size": 6,
  "pool_available": 6,
  "pool_initialized": true
}
```

---

## Pool Recovery (after restart with DNS failure)

If C3 starts before DNS resolves (M365 not reachable), tabs may fail to initialize:

```bash
# Reset PagePool without restarting C3
curl -X POST http://localhost:8001/pool-reset
```

This reinitializes all tabs. Safe to call at any time.

---

## Provider Auto-Detection

`config.resolved_provider()` resolves the effective provider:

| `COPILOT_PROVIDER` | `COPILOT_PORTAL_PROFILE` | Effective |
|--------------------|--------------------------|-----------|
| `auto` | `m365_hub` | `m365` |
| `auto` | `consumer` | `copilot` |
| `m365` | any | `m365` |
| `copilot` | any | `copilot` |

A mismatch warning is logged at C1 startup if `COPILOT_PROVIDER` and `COPILOT_PORTAL_PROFILE` disagree.
