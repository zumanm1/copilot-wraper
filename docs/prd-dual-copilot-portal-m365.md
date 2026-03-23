# PRD: Dual Copilot portal profile (consumer vs Microsoft 365 web hub)

**Version:** 1.0  
**Status:** Phase A implemented (parameterized portal + cookies; API host defaults to consumer Copilot until network discovery proves otherwise)

## Summary

Operators can choose **Copilot (consumer)** or **Microsoft 365 Copilot (web)** as the browser login surface. C1 uses configurable `Origin`/`Referer` aligned with that portal while **Phase A** keeps the chat API at `copilot.microsoft.com` unless `COPILOT_PORTAL_API_BASE_URL` is set.

## Configuration

| Variable | Values | Default |
|----------|--------|---------|
| `COPILOT_PORTAL_PROFILE` | `consumer`, `m365_hub` | `consumer` |
| `COPILOT_PORTAL_BASE_URL` | Optional full portal URL | Derived from profile |
| `COPILOT_PORTAL_API_BASE_URL` | Optional API origin (https, no path) | `https://copilot.microsoft.com` |

## C3 setup UI

- `GET http://localhost:8001/setup` — HTML form (dropdown + save)
- `POST /setup` — form fields: `profile`, optional `portal_base_url`, optional `api_base_url`

## Phase B gate

Before changing WebSocket/REST contracts, capture traces from `https://m365.cloud.microsoft/` and document in [copilot-m365-network-notes.md](copilot-m365-network-notes.md).

## Disclaimer

This project is not affiliated with Microsoft. Reverse-engineered clients may violate terms of use; use at your own risk.
