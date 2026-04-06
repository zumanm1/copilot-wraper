#!/bin/bash
# ============================================================
# Container 7a — OpenClaw Gateway Entrypoint
#
# Attempts to start the openclaw gateway in foreground mode.
# If the gateway fails to bind (requires interactive onboarding),
# falls back to a lightweight health server so the container
# stays alive for exec-based interactive setup.
#
# Provider chain: C7a → C1 (http://app:8000/v1) → Copilot
# Auth: C3 (browser-auth) feeds cookies to C1 transparently.
#
# Interactive setup (if gateway doesn't auto-start):
#   docker compose exec openclaw-gateway openclaw onboard
#   docker compose exec openclaw-gateway openclaw gateway run
# ============================================================
set -euo pipefail

PROVIDER_URL="${OPENCLAW_PROVIDER_BASE_URL:-http://app:8000/v1}"
PROVIDER_KEY="${OPENCLAW_PROVIDER_API_KEY:-sk-not-needed}"
GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-copilot-local-gateway-token}"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║      OpenClaw Gateway  (C7a)  v2026.3.13                ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  Provider  : %-43s ║\n" "$PROVIDER_URL"
printf "║  Gateway   : %-43s ║\n" "0.0.0.0:${GATEWAY_PORT}"
printf "║  OpenClaw  : %-43s ║\n" "$(openclaw --version 2>/dev/null || echo 'installed')"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

export OPENAI_API_KEY="${PROVIDER_KEY}"
export OPENAI_BASE_URL="${PROVIDER_URL}"
export OPENCLAW_NO_RESPAWN=1

# Try starting the gateway in background first
openclaw gateway run \
    --dev \
    --allow-unconfigured \
    --port "${GATEWAY_PORT}" \
    --bind all \
    --token "${GATEWAY_TOKEN}" &
GW_PID=$!

# Wait up to 30s for port to open
echo "[openclaw-gateway] Waiting for gateway to bind port ${GATEWAY_PORT}..."
READY=false
for i in $(seq 1 30); do
    sleep 1
    if grep -q "$(printf '%04X' "${GATEWAY_PORT}")" /proc/net/tcp /proc/net/tcp6 2>/dev/null; then
        READY=true
        break
    fi
    # Check if process died
    if ! kill -0 "$GW_PID" 2>/dev/null; then
        echo "[openclaw-gateway] Gateway process exited early."
        break
    fi
done

if [ "$READY" = true ]; then
    echo "[openclaw-gateway] Gateway listening on port ${GATEWAY_PORT}."
    wait "$GW_PID"
else
    echo "[openclaw-gateway] Gateway did not bind port ${GATEWAY_PORT}."
    echo "[openclaw-gateway] Falling back to standby health server."
    echo ""
    echo "  The full gateway requires interactive onboarding."
    echo "  To set up, exec into this container and run:"
    echo ""
    echo "    docker compose exec openclaw-gateway openclaw onboard"
    echo "    docker compose exec openclaw-gateway openclaw gateway run"
    echo ""
    echo "  C7b CLI (ask, status) works without the gateway"
    echo "  by talking directly to C1."
    echo ""

    # Kill the entire process group (gateway spawns child processes)
    kill "$GW_PID" 2>/dev/null || true
    pkill -P "$GW_PID" 2>/dev/null || true
    sleep 2
    # Force-kill any survivors
    kill -9 "$GW_PID" 2>/dev/null || true
    pkill -9 -f "openclaw-gateway" 2>/dev/null || true
    wait "$GW_PID" 2>/dev/null || true

    # Write fallback health server to file (avoids quoting issues with exec -e)
    cat > /tmp/standby-server.js << 'SERVERJS'
const http = require("http");
const port = parseInt(process.env.OPENCLAW_GATEWAY_PORT || "18789");
http.createServer((req, res) => {
  const body = req.url === "/readyz"
    ? { status: "not_ready", message: "Run: docker compose exec openclaw-gateway openclaw onboard" }
    : { status: "standby", openclaw: "2026.3.13", port };
  res.writeHead(req.url === "/readyz" ? 503 : 200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}).listen(port, "0.0.0.0", () => console.log("[openclaw-gateway] Standby health server on port " + port));
SERVERJS

    exec node /tmp/standby-server.js
fi
