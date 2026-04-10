# C9_JOKES — Product Requirements: Database

> **⚠️ Docs behind code (2026-04-10):** This PRD was written before the 6 UI/UX gap-fixes were implemented. New pages (`/session-manager`, `/docuz-tasked`), grouped nav, expanded API surface (Tasks/Alerts/Tokens/Session Manager), dynamic agent filter, and severity fix are live but not reflected here. See `/docuz-tasked` in-app for the current reference.


## Purpose

SQLite persistence for validation history, health snapshots, and chat/audit logs inside C9 only.

## Schema (v1)

See `c9_jokes/schema.sql`. Tables:

1. **validation_runs** — `id`, `started_at`, `finished_at`, `mode` (sequential|parallel), `passed`, `failed`, `raw_summary` (JSON text).
2. **health_snapshots** — `id`, `captured_at`, `target` (c1|c3|c7a), `http_status`, `body_json` (text).
3. **pair_results** — `id`, `run_id`, `pair_name`, `ok`, `detail`, `duration_ms`.
4. **chat_logs** — `id`, `created_at`, `agent_id`, `prompt_excerpt`, `response_excerpt`, `http_status` (no full cookies).

## Acceptance criteria

- DB file lives on named volume `c9-data` at `/app/data/c9.db` (compose).
- Migrations: scaffold uses `schema.sql` executed on startup if DB missing.

## Out of scope

- PostgreSQL, multi-tenant auth, encryption at rest.
