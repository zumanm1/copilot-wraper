#!/bin/bash
# ============================================================
# Container 7a — OpenClaw Gateway Entrypoint
#
# Non-interactive replacement for ./scripts/docker/setup.sh.
# Writes provider + gateway config.json BEFORE starting the
# gateway (EC1: prevents race between config write and start).
#
# Provider chain: C7a → C1 (http://app:8000/v1) → Copilot
# Auth: C3 (browser-auth) feeds cookies to C1 transparently.
#
# Reference: OpenClaw Docker Setup Guide v2026.3.13 (adapted)
# ============================================================
set -euo pipefail

PROVIDER_URL="${OPENCLAW_PROVIDER_BASE_URL:-http://app:8000/v1}"
PROVIDER_KEY="${OPENCLAW_PROVIDER_API_KEY:-sk-not-needed}"
GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-copilot-local-gateway-token}"
CONFIG_DIR="${OPENCLAW_CONFIG_DIR:-/root/.openclaw}"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"

# ── Banner ────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║      🤖  OpenClaw Gateway  (C7a)  v2026.3.13            ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  Provider  : %-43s ║\n" "$PROVIDER_URL"
printf "║  Gateway   : %-43s ║\n" "ws://0.0.0.0:${GATEWAY_PORT}"
printf "║  Bind      : %-43s ║\n" "lan (host + container CLI reachable)"
printf "║  Auth      : %-43s ║\n" "via C1 copilot-api → C3 cookies"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Health checks (after start):                           ║"
printf "║    %-53s ║\n" "curl http://localhost:${GATEWAY_PORT}/healthz"
printf "║    %-53s ║\n" "curl http://localhost:${GATEWAY_PORT}/readyz"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Write config BEFORE starting gateway (EC1 race condition fix) ─────────
mkdir -p "$CONFIG_DIR"

cat > "$CONFIG_DIR/config.json" << CONFIG
{
  "provider": "openai",
  "openai": {
    "baseUrl": "${PROVIDER_URL}",
    "apiKey": "${PROVIDER_KEY}"
  },
  "gateway": {
    "bind": "lan",
    "port": ${GATEWAY_PORT},
    "token": "${GATEWAY_TOKEN}"
  },
  "telemetry": false
}
CONFIG

echo "[openclaw-gateway] Config written → $CONFIG_DIR/config.json"
echo "[openclaw-gateway] Provider       → $PROVIDER_URL"
echo "[openclaw-gateway] Starting gateway on port $GATEWAY_PORT..."
echo ""

# ── Start gateway (exec: replaces shell, becomes PID 1) ───────────────────
# Primary:  openclaw gateway start  (official CLI wrapper, per reference doc)
# Fallback: node dist/index.js      (raw Node.js — same binary, no wrapper)
if command -v openclaw &> /dev/null; then
    exec openclaw gateway start
else
    exec node dist/index.js gateway start
fi
