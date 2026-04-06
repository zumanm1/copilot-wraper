# 02 — Authentication & Configuration Flow

> **Last updated: 2026-03-27**
> Cookie authentication, portal profiles, X-Chat-Mode vs X-Work-Mode separation, and config reload lifecycle.

---

## Table of Contents

- [Overview](#overview)
- [Cookie Authentication](#cookie-authentication)
- [Portal Profiles](#portal-profiles)
- [Authentication Flow — Consumer Profile](#authentication-flow--consumer-profile)
- [Authentication Flow — M365 Profile](#authentication-flow--m365-profile)
- [X-Chat-Mode — Thinking Depth](#x-chat-mode--thinking-depth)
- [X-Work-Mode — M365 Scope](#x-work-mode--m365-scope)
- [Header Separation Summary](#header-separation-summary)
- [Config Reload Lifecycle](#config-reload-lifecycle)
- [Auto-Cookie Refresh](#auto-cookie-refresh)
- [Manual Cookie Fallback](#manual-cookie-fallback)
- [Session Health Check](#session-health-check)
- [Environment Variable Reference](#environment-variable-reference)

---

## Overview

Authentication is **browser-session based**. There is no API key for the upstream Copilot service. Instead:

```
C3 (headless browser)
  └── User logs in via noVNC
        └── POST /extract → C3 extracts cookies → writes to .env
              └── POST C1 /v1/reload-config → C1 reads new cookies
                    └── All subsequent requests use fresh cookies
```

Two pieces of configuration control every request:
1. **Which Copilot endpoint** to call (`COPILOT_PORTAL_PROFILE` → consumer or M365)
2. **How Copilot reasons** (`X-Chat-Mode`) and **what context to use** (`X-Work-Mode`)

---

## Cookie Authentication

### COPILOT_COOKIES

`COPILOT_COOKIES` is a full cookie string (same format as the browser's `Cookie:` header), extracted by C3:

```
COPILOT_COOKIES=_U=ABC123...; MUID=DEF456...; SRCHD=...; MUIDB=...
```

C1 loads this string from `.env` at startup and on every `POST /v1/reload-config`. It is injected as the `Cookie:` header on every WebSocket connection to Copilot.

### BING_COOKIES

A simpler fallback — just the `_U` cookie value from `bing.com` / `copilot.microsoft.com`:

```
BING_COOKIES=ABC123...
```

If `COPILOT_COOKIES` is empty, C1 falls back to `_U=<BING_COOKIES>`.

### Cookie Validity

Cookies expire periodically (typically every few days). Signs of expired cookies:
- C1 returns 401 or 403 from Copilot
- C3 `/session-health` reports `auth_ok: false`
- Chat responses return error text instead of an answer

Re-run the C3 extraction flow to refresh.

---

## Portal Profiles

`COPILOT_PORTAL_PROFILE` (in `.env`) selects which Copilot product is used:

| Profile | Copilot product | Auth requirement |
|---|---|---|
| `consumer` | `copilot.microsoft.com` | Personal Microsoft account |
| `m365_hub` | `m365.cloud.microsoft` | Microsoft 365 work/school account |

This controls:
- Which URL C3 logs into during cookie extraction
- Which backend provider C1 uses (`copilot` WebSocket vs `m365` C3 proxy)
- Whether C3's PagePool proxies requests (m365 only)

---

## Authentication Flow — Consumer Profile

```
COPILOT_PORTAL_PROFILE=consumer

Step 1 — Login (one-time)
  User → http://localhost:6080 (noVNC)
  In noVNC browser: navigate to https://copilot.microsoft.com
  Sign in with personal Microsoft account
  Complete MFA / consent prompts

Step 2 — Extract cookies
  curl -X POST http://localhost:8001/extract
  C3 Playwright:
    → context.cookies() from copilot.microsoft.com domain
    → writes COPILOT_COOKIES=... to .env
    → POSTs http://app:8000/v1/reload-config

Step 3 — C1 uses cookies on every request
  WSS wss://copilot.microsoft.com/c/api/chat?...
    Cookie: {COPILOT_COOKIES}
    Origin: https://copilot.microsoft.com
    Referer: https://copilot.microsoft.com/

Step 4 — Copilot WebSocket handshake
  101 Switching Protocols (auth via Cookie header)
  → protocol: sydneyv2 / bing-echo
  → send: {"text": prompt, "style": "smart"}
  → receive: appendText events → partCompleted
```

---

## Authentication Flow — M365 Profile

```
COPILOT_PORTAL_PROFILE=m365_hub

Step 1 — Login (one-time)
  User → http://localhost:6080 (noVNC)
  In noVNC browser: navigate to https://m365.cloud.microsoft
  Sign in with Microsoft 365 work/school account
  Complete MFA, select tenant if multi-tenant
  Navigate to https://m365.cloud.microsoft/chat
  Confirm chat UI is visible

Step 2 — Extract cookies
  curl -X POST http://localhost:8001/extract
  C3 Playwright:
    → walks m365.cloud.microsoft → bing.com → copilot.microsoft.com
    → merges cookies from all domains into COPILOT_COOKIES
    → writes to .env
    → POSTs http://app:8000/v1/reload-config

Step 3 — C1 proxies every request through C3
  C1 _c3_proxy_call():
    POST http://browser-auth:8001/chat
    { "prompt": "...", "agent_id": "c2-aider", "mode": "work" }

Step 4 — C3 PagePool serves the request
  PagePool.acquire("c2-aider") → sticky Tab 1
  Tab 1: already logged into https://m365.cloud.microsoft/chat
  Type prompt → Press Enter
  Intercept SignalR WS (substrate.office.com) → extract type=2 frame
  Return { "success": true, "text": "..." }

Key difference: In M365 mode, C1 does NOT open a direct WebSocket to Copilot.
C3's active Playwright browser session IS the authentication — OAuth tokens live
in the browser context, not in a cookie string.
```

---

## X-Chat-Mode — Thinking Depth

`X-Chat-Mode` controls **how deeply Copilot reasons** before responding. This is independent of authentication.

```
Client → C1: Header X-Chat-Mode: deep
                │
                ▼
        C1 resolve_chat_style_with_mode(model, temperature, chat_mode)

        THINKING_MODE_MAP = {
            "auto":  "smart",      ← default — balanced reasoning
            "quick": "balanced",   ← fast, surface-level response
            "deep":  "reasoning",  ← deep step-by-step thinking (o1-style)
        }
                │
                ▼
        backend.style = "reasoning"  (used in WebSocket message to Copilot)
```

**Who sets X-Chat-Mode:**
- **C9 chat/pairs pages** — thinking mode dropdown (Auto / Quick Response / Think Deeper)
- **Direct API callers** — set header manually
- **Agent containers** — use default (`auto` / `smart`) unless explicitly set

**Note:** When both model name and `X-Chat-Mode` are provided, `X-Chat-Mode` takes precedence.

---

## X-Work-Mode — M365 Scope

`X-Work-Mode` controls **what knowledge Copilot draws on**. Only meaningful in M365 profile mode.

```
Client → C1: Header X-Work-Mode: work
                │
                ▼
        C1 → C3 POST /chat  { "mode": "work" }
                │
                ▼
        C3 _browser_chat_on_page(page, prompt, mode="work")
          └─ Clicks the Work/Web toggle in M365 Copilot UI before typing

Values:
  work  → M365 Work tab (SharePoint, Teams, Emails, Calendar)
  web   → Web tab (public internet search)
  ""    → default (last selected state)
```

**Who sets X-Work-Mode:**
- **C9 chat/pairs pages** — Work / Web toggle button
- **Direct API callers** — set header manually

**Has no effect in consumer profile mode** (copilot.microsoft.com has no Work/Web toggle).

---

## Header Separation Summary

`X-Chat-Mode` and `X-Work-Mode` are **completely separate concerns** and are often confused:

| Header | Purpose | Controls |
|---|---|---|
| `X-Agent-ID` | Session isolation | Which `CopilotBackend` instance handles the request |
| `X-Chat-Mode` | **Thinking depth** | Copilot reasoning style: `smart` / `balanced` / `reasoning` |
| `X-Work-Mode` | **M365 data scope** | C3 clicks Work or Web tab in M365 UI |

All three can be combined freely:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c8-hermes" \
  -H "X-Chat-Mode: deep" \
  -H "X-Work-Mode: work" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Summarise my recent emails"}]}'
```

---

## Config Reload Lifecycle

The `.env` file is the shared config between C1 and C3:

```
.env (shared bind-mount: both C1 and C3 have it at /app/.env)
  ├── C1 reads at: startup + POST /v1/reload-config
  └── C3 writes at: POST /extract

Reload sequence:
  1. C3 /extract  → writes COPILOT_COOKIES to .env
  2. C3 /extract  → POST http://app:8000/v1/reload-config  (auto-triggers)
  3. C1 handler   → load_dotenv(override=True)
                  → config.reload_cookies()  (refreshes _cached_cookie)
                  → returns {"status":"ok","cookies_loaded":true}
  4. All new requests use updated cookies immediately
  5. In-flight requests complete with old cookies (no interruption)

Manual trigger:
  curl -X POST http://localhost:8000/v1/reload-config
```

---

## Auto-Cookie Refresh

When `AUTO_COOKIE_REFRESH=true` (default), C1 can automatically trigger C3 to refresh cookies on auth failures:

```python
# On 401/403 from Copilot WebSocket (consumer profile):
if AUTO_COOKIE_REFRESH and provider == "copilot":
    → POST http://browser-auth:8001/extract
    → POST /v1/reload-config (self)
    → retry original request once
```

Auto-refresh is **disabled for M365** by default (`AUTO_COOKIE_REFRESH_M365=false`) because triggering a new extraction during an active M365 session can disrupt in-flight C3 PagePool requests.

---

## Manual Cookie Fallback

If C3 is unavailable or noVNC is inaccessible:

```bash
# 1. Open https://copilot.microsoft.com in your host browser and sign in
# 2. Press F12 → Application → Cookies → https://copilot.microsoft.com
# 3. Copy the _U cookie value
# 4. Edit .env:
BING_COOKIES=<paste _U value here>

# 5. Reload C1:
curl -X POST http://localhost:8000/v1/reload-config

# Verify:
curl http://localhost:8000/v1/debug/cookie
```

---

## Session Health Check

C3 exposes a session health endpoint (queried by C9):

```bash
GET http://localhost:8001/session-health
```

```json
{
  "browser_ok": true,
  "pool_size": 6,
  "tabs_available": 5,
  "tabs_busy": 1,
  "auth_ok": true,
  "m365_session_valid": true,
  "warnings": []
}
```

Warning conditions:
- `tabs_available < 2` — pool may be exhausted under load
- `auth_ok: false` — cookies expired; re-run `/extract`
- `m365_session_valid: false` — M365 session needs re-login in noVNC

---

## Environment Variable Reference

| Variable | Default | Description |
|---|---|---|
| `COPILOT_COOKIES` | (empty) | Full browser cookie string — primary auth |
| `BING_COOKIES` | (empty) | Fallback: just the `_U` cookie value |
| `COPILOT_PORTAL_PROFILE` | `consumer` | `consumer` or `m365_hub` |
| `COPILOT_PROVIDER` | `auto` | Override: `copilot`, `m365`, or `auto` |
| `COPILOT_STYLE` | `balanced` | Default Copilot style when no `X-Chat-Mode` header |
| `COPILOT_PERSONA` | `copilot` | Copilot persona (copilot / bing) |
| `AUTO_COOKIE_REFRESH` | `true` | Auto-trigger C3 extract on 401 (consumer) |
| `AUTO_COOKIE_REFRESH_M365` | `false` | Auto-trigger extract on 401 (M365, risky) |
| `CHROME_KEY_PASSWORD` | (empty) | Chrome keyring password for cookie decryption |
| `REQUEST_TIMEOUT` | `180` | Seconds before C1 times out waiting for Copilot |
| `CONNECT_TIMEOUT` | `15` | Seconds for WebSocket connection timeout |
| `RATE_LIMIT` | `100/minute` | slowapi rate limit on C1 endpoints |
| `API_KEY` | (empty) | If set, clients must send `Authorization: Bearer <key>` |
| `C3_CHAT_TAB_POOL_SIZE` | `6` | Number of C3 Playwright tabs in the PagePool |
