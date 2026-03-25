# C9_JOKES — Product Requirements: API

## Purpose

Document REST endpoints exposed by C9 and the upstream APIs used for validation (C1, C3).

## C9 endpoints (JSON)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | `{ "c1": {...}, "c3": {...}, "c7a": {...} }` from live probes |
| GET | `/api/health` | Alias or subset for dashboard widgets |

Scaffold may return minimal JSON; expand per C9_PRD_BACKEND.

## Upstream: C1 (copilot-api)

- `GET http://app:8000/health`
- `GET http://app:8000/v1/sessions`
- `POST http://app:8000/v1/chat/completions` — OpenAI format; header `X-Agent-ID: <id>`
- `POST http://app:8000/v1/messages` — Anthropic format (C5 path)

Example (host):

```bash
curl -sS http://localhost:8000/health
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c9-smoke" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Tell me a joke"}],"stream":false}'
```

## Upstream: C3 (browser-auth)

- `GET http://browser-auth:8001/health`
- `GET http://browser-auth:8001/status` (from LAN: `http://localhost:8001/status`)

## Upstream: C7a (OpenClaw gateway)

- `GET http://openclaw-gateway:18789/healthz`

## Security

- C9 must not log full `Authorization` or cookie headers.
- No exposure of `.env` contents via API.

## Out of scope

- Public OAuth; API keys for C9 (add if exposed beyond LAN).
