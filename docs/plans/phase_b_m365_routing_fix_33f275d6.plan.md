---
name: Phase B M365 Routing Fix
status: ✅ COMPLETE (2026-03-25)
overview: Implement Phase B so C1/C2 can authenticate and send requests through an M365-compatible path for `m365_hub`, while preserving the existing Copilot path for non-M365 profiles. Validate with automated tests and an optional live signed-in round-trip.
source: /Users/macbook/UNINSTALL00/.cursor/plans/phase_b_m365_routing_fix_33f275d6.plan.md
continuation_notes:
  - Continued under "phase-b-m365-complete" with locked target behavior: dual-mode routing (`m365_hub` => M365, others => Copilot).
  - End-to-end validation target is a real signed-in M365 browser session with C2 `ask_helper.py` round-trip.
  - Implementation commits and push references are recorded in git history for this repo.
  - COMPLETED 2026-03-25: C2 Aider → C1 → C3 → M365 validated end-to-end. Aider received response from M365 Copilot.
---

# Phase B: Fix C2→C1→C3 M365 Integration

## Goal

Resolve the known limitation where C1 always targets `copilot.microsoft.com`, causing 403s with M365-only session cookies, and make `m365_hub` operate through an M365-compatible request path.

## Assumed Scope (from current context)

- Use **dual-mode routing**:
  - `m365_hub` profile: route to M365-compatible API flow.
  - existing profiles: keep current `copilot.microsoft.com` behavior.
- Validate with tests and live check if session is available.

## Investigation and Design

- Review current request construction and endpoint selection in [copilot_backend.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/copilot_backend.py).
- Review profile/base-url selection in [config.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/config.py).
- Review C2 request bridge and payload assumptions in [ask_helper.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/ask_helper.py).
- Confirm C3 cookie/profile semantics in [browser_auth/cookie_extractor.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/browser_auth/cookie_extractor.py).

## Implementation Plan

- Add a **provider strategy layer** in [copilot_backend.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/copilot_backend.py):
  - `CopilotPublicProvider` (existing flow, minimal refactor).
  - `M365Provider` (Phase B flow for `m365_hub`).
- Extend config knobs in [config.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/config.py):
  - explicit provider selection (`auto|copilot|m365`), default `auto`.
  - M365 API base and feature flags for fallback behavior.
- Update cookie/session loading logic in [copilot_backend.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/copilot_backend.py):
  - enforce profile/provider compatibility checks.
  - emit actionable errors if required M365 session material is missing.
- Update C2 bridge assumptions in [ask_helper.py](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/ask_helper.py) only if response envelope differs for M365.
- Keep backward compatibility for existing non-M365 usage.

## Validation Plan

- Add/adjust unit tests for routing and provider selection (expected likely under [tests](/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper/tests)).
- Add targeted tests for:
  - `m365_hub` selects M365 provider.
  - non-M365 profiles keep old provider.
  - missing M365 auth/session returns clear error instead of opaque 403.
- Run full test suite.
- Live validation (if signed-in session present):
  - C3 extract -> C1 load -> C2 `ask_helper.py` prompt -> verify successful response path.

## Delivery

- Commit 1: provider abstraction + config wiring.
- Commit 2: M365 provider path + tests.
- Commit 3: C2/C1 integration polish + docs notes.
- Push to `origin/main` after green tests and optional live verification.
