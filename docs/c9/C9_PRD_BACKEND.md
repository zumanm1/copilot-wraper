# C9_JOKES — Product Requirements: Backend

> **⚠️ Docs behind code (2026-04-10):** This PRD was written before the 6 UI/UX gap-fixes were implemented. New pages (`/session-manager`, `/docuz-tasked`), grouped nav, expanded API surface (Tasks/Alerts/Tokens/Session Manager), dynamic agent filter, and severity fix are live but not reflected here. See `/docuz-tasked` in-app for the current reference.


## Purpose

Web application serving HTML plus JSON/SSE APIs for health polling, validation orchestration, and direct C1 chat proxying.

## Architecture

- **Process**: single Flask worker on `0.0.0.0:6090` inside `C9_jokes` container.
- **Outbound calls only**: `GET` C1 `/health`, C3 `/health`, C7a `/healthz`; `GET` C1 `/v1/sessions`; `POST` C1 `/v1/chat/completions` or `/v1/messages` when user triggers chat (with configurable `X-Agent-ID`).
- **No inbound mutation** of other containers: no writes to shared `.env`, no Docker socket in v1.

## Routes (scaffold vs full)

| Method | Path | Scaffold behavior | Full behavior |
|--------|------|-------------------|---------------|
| GET | `/`, `/health`, … | Render stub templates | Same + inject live data |
| GET | `/api/status` | Aggregate JSON: C1/C3/C7a probe | Same |
| POST | `/api/validate/run` | Optional: shell out or in-process mirror of `tests/validate_all_agents.py` logic | Persist run to SQLite |
| POST | `/api/chat` | Proxy to C1 with chosen agent id; JSON by default, SSE when `stream:true` | Rate-limit + log redaction |

## Future: Live updates

- Chat streaming is implemented with SSE on `POST /api/chat` when `stream:true`.
- Optional WebSocket or SSE log-tail for validation dashboards remains future work.

## Acceptance criteria

- App starts with `flask run` or `python -m c9_jokes.app` equivalent on port 6090.
- Misconfigured `C1_URL` returns JSON error, not crash loop.

## Dependencies

- `requests` for HTTP to peers.
- Environment: `C1_URL`, `C3_URL`, `C7A_URL`, `DATABASE_PATH` (SQLite file path).

## Out of scope

- CrewAI integration in scaffold (document future worker queue).
