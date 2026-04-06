# PRD: Dual Copilot portal profile (consumer vs Microsoft 365 web hub)

**Version:** 3.0  
**Status:** Phase A + Phase B + Phase C implemented and validated (2026-03-25)

## Summary

Operators can choose **Copilot (consumer)** or **Microsoft 365 Copilot (web)** as the browser login surface.

- **Phase A (consumer):** C1 connects directly to `copilot.microsoft.com` via WebSocket using session cookies.
- **Phase B (M365):** C1 proxies chat through C3's browser session. C3 uses Playwright to interact with the M365 Copilot web UI and intercepts SignalR WebSocket responses from `substrate.office.com`.

Phase B was required because M365 Copilot uses a completely different backend (`substrate.office.com`, SignalR protocol) that requires an active browser OAuth session — standalone cookies are insufficient.

## Configuration

| Variable | Values | Default |
|----------|--------|---------|
| `COPILOT_PORTAL_PROFILE` | `consumer`, `m365_hub` | `consumer` |
| `COPILOT_PORTAL_BASE_URL` | Optional full portal URL | Derived from profile |
| `COPILOT_PORTAL_API_BASE_URL` | Optional API origin (https, no path) | `https://copilot.microsoft.com` |

## C3 setup UI

- `GET http://localhost:8001/setup` — HTML form (dropdown + save)
- `POST /setup` — form fields: `profile`, optional `portal_base_url`, optional `api_base_url`

## Phase B — Completed (2026-03-25)

Network traces captured and documented in [copilot-m365-network-notes.md](copilot-m365-network-notes.md). M365 uses `substrate.office.com` with SignalR protocol — different from consumer. Phase B implemented as C3 browser proxy (`browser_chat()` in `cookie_extractor.py`, `_c3_proxy_call()` in `copilot_backend.py`).

### Files modified for Phase B

| File | Change |
|------|--------|
| `copilot_backend.py` | Added `_c3_proxy_call()`, M365 routing in `_raw_copilot_call()` and `chat_completion_stream()` |
| `browser_auth/cookie_extractor.py` | Added `browser_chat()` — Playwright M365 interaction, SignalR WS interception, `execCommand` text input |
| `browser_auth/server.py` | Added `POST /chat` endpoint |
| `docker-compose.yml` | Added bind mount for hot-reload |
| `browser_auth/entrypoint.sh` | Added `--reload --reload-dir /browser-auth` |

### Bugs fixed during Phase B

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `Locator.type: Timeout 30000ms` | `.type()` at 20ms/char × 2000+ chars | `execCommand('insertText')` for >200 chars |
| Consecutive requests fail | SPA caches state on same-URL navigation | `about:blank` teardown before each `/chat` |
| DOM artifacts in response | SignalR `type=2` frames not parsed | Added `type=2` parser for `item.messages[].text` |

## Phase C — Anthropic Endpoint + Full Agent Validation (2026-03-25)

C1 now exposes an **Anthropic-compatible `/v1/messages` endpoint** alongside the existing OpenAI `/v1/chat/completions`, enabling C5 (Claude Code CLI) to use the M365 Copilot pipeline without any client-side changes.

### Changes for Phase C

| File | Change |
|------|--------|
| `server.py` | Added `/v1/messages` endpoint; `_anthropic_messages_to_prompt()` with system prompt truncation (500 char cap) and `system` as `str \| list` handling |
| `models.py` | Permissive Anthropic Pydantic models (`extra="allow"`, `ConfigDict`) for Claude Code's tools/metadata/tool_use fields |
| `Dockerfile.claude-code` | Pre-seeded `~/.claude/.credentials.json` and `settings.json` to bypass Claude Code v2.x interactive login |
| `claude-code-terminal/start.sh` | Fixed `bash -c` argument passthrough |

### Full Agent Ecosystem Validation (2026-03-25)

All agent containers confirmed working with C1+C3 M365 pipeline:

| Container | API Path | Agent ID | Status |
|-----------|----------|----------|--------|
| C2 Aider | OpenAI `/v1/chat/completions` | `c2-aider` | ✅ PASS |
| C5 Claude Code | Anthropic `/v1/messages` | `c5-claude-code` | ✅ PASS |
| C6 KiloCode | OpenAI `/v1/chat/completions` | `c6-kilocode` | ✅ PASS |
| C7a OpenClaw GW | Gateway standby `:18789/healthz` | `c7-openclaw` | ✅ Standby OK |
| C7b OpenClaw CLI | OpenAI `/v1/chat/completions` | `c7-openclaw` | ✅ PASS |
| C8 Hermes | OpenAI `/v1/chat/completions` | `c8-hermes` | ✅ PASS |

## Disclaimer

This project is not affiliated with Microsoft. Reverse-engineered clients may violate terms of use; use at your own risk.
