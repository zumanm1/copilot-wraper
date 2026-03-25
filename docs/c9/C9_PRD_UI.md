# C9_JOKES — Product Requirements: UI

## Purpose

Define the visual layout, navigation, and page structure for the C9 validation webapp (port 6090) so operators can monitor stack health without touching C1–C8 runtime behavior.

## Requirements

1. **Seven pages** (six feature pages + one API reference), reachable from a persistent top nav.
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
| `/api` | API reference | Static curl examples for C1, C3, C9 |

## Acceptance criteria

- All seven routes render without JavaScript errors (scaffold: server-rendered only).
- Nav highlights current page (active class).
- Static assets served from `/static/style.css`.

## Dependencies

- Flask Jinja2 templates under `c9_jokes/templates/`.

## Out of scope (scaffold)

- Full design system, i18n, accessibility audit beyond basic semantics.
