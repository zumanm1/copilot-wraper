# C9_JOKES — Product Requirements: UI

> **⚠️ Docs behind code (2026-04-10):** This PRD was written before the 6 UI/UX gap-fixes were implemented. New pages (`/session-manager`, `/docuz-tasked`), grouped nav, expanded API surface (Tasks/Alerts/Tokens/Session Manager), dynamic agent filter, and severity fix are live but not reflected here. See `/docuz-tasked` in-app for the current reference.


## Purpose

Define the visual layout, navigation, and page structure for the C9 validation webapp (port 6090) so operators can monitor stack health without touching C1–C8 runtime behavior.

## Requirements

1. **Core seven pages** from the original scaffold (dashboard, health, pairs, chat, logs, sessions, API reference), plus **Agent** (`/agent`), **Multi-Agent** (`/multi-agent`), and **multi-Agento** (`/multi-Agento`), all reachable from the persistent top nav.
2. **Responsive layout**: usable on laptop width (1280px) minimum; nav collapses to a simple list on narrow viewports.
3. **Default theme**: dark, high-contrast text; optional light theme toggle deferred to post-scaffold.
4. **Status affordances**: green/red/yellow badges for pass/warn/fail; monospace for JSON snippets and log excerpts.
5. **Shared chrome**: `base.html` with title block, nav links, footer note (“read-only observer; does not modify C1–C8”).

## Page inventory

| Route | Page name | Primary UI elements |
|-------|-----------|---------------------|
| `/` | Dashboard | Grid of container cards, last run timestamp, quick “Run validation” link |
| `/health` | Container health | Table: name, URL probed, HTTP status, response snippet |
| `/pairs` | Pair validation | Table of pair tests; buttons for sequential vs parallel (post-scaffold: wire to API) |
| `/chat` | Agent chat | Form: agent preset, prompt textarea, submit; response panel |
| `/logs` | Logs viewer | Filter dropdown + scrollable log list |
| `/sessions` | Session manager | Embed or link to C1 `/v1/sessions` JSON (pretty-print) |
| `/api` | API reference | Curl-oriented examples for C1, C3, C9 (canonical); `/api/docs` redirects here |
| `/agent` | Agent workspace | IDE-style task loop backed by **C10** sandbox |
| `/multi-agent` | Multi-agent (legacy path) | Concurrent panes sharing **C10** workspace |
| `/multi-Agento` | Multi-agent (session-scoped) | Concurrent panes with **C11** per-session workspace |

## Acceptance criteria

- All documented routes render without JavaScript errors on supported browsers (hybrid: server templates + page scripts for dashboards and agents).
- Nav highlights current page (active class).
- Static assets served from `/static/style.css`.

## Dependencies

- Flask Jinja2 templates under `c9_jokes/templates/`.

## Out of scope (scaffold)

- Full design system, i18n, accessibility audit beyond basic semantics.
