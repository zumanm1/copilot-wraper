# C9_JOKES — Product Requirements: UX

## Purpose

Describe user flows for validation, monitoring, and exploratory “ask” actions from the C9 console.

## Requirements

1. **Discoverability**: First-time user lands on Dashboard; one-click path to “how to test” (API page).
2. **Validation flow**: User runs full or pair validation → sees progress (loading state) → table of results with pass/fail and expandable error detail.
3. **Chat flow**: User selects agent channel (maps to `X-Agent-ID` + endpoint) → enters prompt → sees assistant text stream live and then a latency note; errors show HTTP status + body snippet (no secrets).
4. **Logs flow**: Recent runs listed newest-first; filter by `pair_id` or `container`.
5. **Sessions flow**: Read-only view of C1 `/v1/sessions`; refresh button; no delete/mutate in v1.

## Acceptance criteria

- No action in C9 modifies `.env`, C3 browser profile, or C1 connection pool (read-only HTTP to peer services).
- Failed upstream calls show actionable text (e.g. “C3 /chat empty reply — retry or re-extract cookies”).

## Dependencies

- Backend endpoints for `/api/validate`, `/api/health-snapshot`, `/api/chat` (see C9_PRD_API.md).

## Out of scope (scaffold)

- Toast library and extra live-push channels beyond the existing chat SSE flow.
- CrewAI-driven multi-step workflows (future phase).
