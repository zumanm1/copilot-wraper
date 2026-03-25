# C9_JOKES — Product Requirements: Backend

## Purpose

Flask application serving HTML and JSON APIs for health polling, validation orchestration, and optional direct C1 chat proxy.

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
| POST | `/api/chat` | Proxy to C1 with chosen agent id | Rate-limit + log redaction |

## Future: WebSocket

- Optional `flask-socketio` or SSE for live validation log tail (out of scaffold).

## Acceptance criteria

- App starts with `flask run` or `python -m c9_jokes.app` equivalent on port 6090.
- Misconfigured `C1_URL` returns JSON error, not crash loop.

## Dependencies

- `requests` for HTTP to peers.
- Environment: `C1_URL`, `C3_URL`, `C7A_URL`, `DATABASE_PATH` (SQLite file path).

## Out of scope

- CrewAI integration in scaffold (document future worker queue).
