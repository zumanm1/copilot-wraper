# M365 Copilot (web) — network discovery notes

**Status:** ✅ Complete — Phase B implemented and validated (2026-03-25).

## Purpose

Decide whether the Microsoft 365 web hub uses the **same** `copilot.microsoft.com` WebSocket/REST chat API as the consumer Copilot UI, or a **different** host or protocol (Phase B adapter).

## Decision

| Finding | Action |
|---------|--------|
| **Different host AND protocol** | Implemented Phase B: C3 browser proxy with SignalR WS interception |

M365 Copilot uses `substrate.office.com` with the **SignalR** protocol — completely different from the consumer `copilot.microsoft.com` WebSocket protocol.

## Findings (2026-03-25)

_Recorded by: Cascade (automated capture via C3 Playwright WS interception)_

### WebSocket

- **Chat WebSocket URL:** `wss://substrate.office.com/m365Copilot/Chathub/{session-guid}`
- **Notification WS:** `wss://go.trouter.skype.com/v4/c?tc=...` (Trouter — push notifications, not chat)
- **Protocol:** SignalR (not raw JSON events like consumer Copilot)
- **Frame delimiter:** `\x1e` (ASCII record separator) — multiple JSON objects per frame

### SignalR Frame Types

| Type | Purpose |
|------|---------|
| `1` | Invocation — `target: "update"` with streaming progress (`messages[]` array) |
| `2` | Completion — final response with `item.messages[]` containing user echo + bot response |
| `3` | Close signal |
| `6` | Ping (keep-alive) |

### Bot Response Location (type=2 frame)

```json
{
  "type": 2,
  "invocationId": "0",
  "item": {
    "messages": [
      {"text": "user prompt echo", "author": "user", ...},
      {"text": "Bot response text here", "author": "bot", ...}
    ]
  }
}
```

Extract: `item.messages[].text` where `author != "user"`

### Headers

- **Origin:** `https://m365.cloud.microsoft`
- **Referer:** `https://m365.cloud.microsoft/chat`
- **Auth:** Browser OAuth session (not standalone cookies) — requires active Playwright browser context

### Key Differences from Consumer Copilot

| Aspect | Consumer (`copilot.microsoft.com`) | M365 (`m365.cloud.microsoft`) |
|--------|-------------------------------------|-------------------------------|
| **Chat WS host** | `copilot.microsoft.com` | `substrate.office.com` |
| **Protocol** | Custom JSON events (`send`, `appendText`, `done`) | SignalR (`type=1` invocations, `type=2` completions) |
| **Auth** | Cookie-based (`_U`, `MUID`) | Browser OAuth session (not portable cookies) |
| **Direct WS access** | ✅ Yes, with cookies | ❌ No, requires browser session |

### Implementation

Because M365's auth binds to the browser session (not standalone cookies), the solution uses C3 as a **live browser proxy**:

1. C1 `_c3_proxy_call()` sends prompt to C3 `POST /chat`
2. C3 `browser_chat()` navigates to `m365.cloud.microsoft/chat` via Playwright
3. C3 types prompt using `document.execCommand('insertText')` (instant, React-compatible)
4. C3 presses Enter to submit
5. C3 intercepts SignalR WS frames, parses `type=2` completion for bot text
6. C3 returns `{"success": true, "text": "..."}`

## Resolved Issues

- **Challenge-state / `invalid-event`:** Resolved by routing M365 through C3 browser proxy instead of direct WS. The browser handles all challenge/auth flows natively.
- **Large prompt timeout (`Locator.type: Timeout 30000ms`):** Fixed by using `execCommand('insertText')` for prompts >200 chars instead of character-by-character `.type()`.
- **Consecutive request failure (no WS opens on 2nd call):** Fixed by navigating to `about:blank` before each `/chat` request, forcing full SPA teardown.
- **DOM artifacts in response:** Fixed by parsing SignalR `type=2` completion frames instead of relying on DOM fallback scraping.

## Operational Notes

- Primary authentication: `https://m365.cloud.microsoft` via noVNC (`http://localhost:6080`)
- `https://copilot.microsoft.com/` remains available as consumer/secondary path
- Keep manual mouse/keyboard control in noVNC unblocked by default
- Auto-dismiss of auth dialogs is opt-in via `BROWSER_AUTH_AUTO_DISMISS_AUTH_DIALOG=true`
- Hot-reload: `browser_auth/` is bind-mounted into C3; `uvicorn --reload` detects changes without container restart
