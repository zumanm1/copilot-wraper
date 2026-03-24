# M365 Copilot (web) — network discovery notes

**Status:** Template — fill after manual DevTools capture.

## Purpose

Decide whether the Microsoft 365 web hub uses the **same** `copilot.microsoft.com` WebSocket/REST chat API as the consumer Copilot UI, or a **different** host or protocol (Phase B adapter).

## Capture checklist (blocking for Phase B)

1. Sign in at `https://m365.cloud.microsoft/` in Chromium.
2. Open DevTools → Network; filter WS and Fetch/XHR.
3. Record:
   - WebSocket URL used for chat
   - REST URL for conversation/thread creation
   - `Origin`, `Referer`, `Authorization`, and cookie names on those requests
   - One sample request/response JSON pair for a user message

## Decision

| Finding | Action |
|---------|--------|
| Same host/path as consumer | Phase A only (current code path) |
| Different host or payload | Implement `M365HubCopilotBackend` (Phase B) |

## Log (append findings below)

_Date:_  
_Recorded by:_  

### WebSocket

### REST

### Headers

### Sample JSON

## Open issue (documented, not solved yet)

- **Challenge-state interaction remains unresolved** for some Copilot sessions:
  - C1 can receive a WebSocket `challenge` event with null method/parameter.
  - Depending on how it is acknowledged, upstream may return empty output or `invalid-event`.
- **Do not force users to change primary sign-in flow**:
  - Primary authentication remains `https://m365.cloud.microsoft.com` (normalized internally).
  - `https://copilot.microsoft.com/` remains available as a secondary/manual path and is not removed.
- **Operational note**:
  - Keep manual mouse/keyboard control in noVNC unblocked by default.
  - Auto-dismiss of auth dialogs is now opt-in via `BROWSER_AUTH_AUTO_DISMISS_AUTH_DIALOG=true`.
