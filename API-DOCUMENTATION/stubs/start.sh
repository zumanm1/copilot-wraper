#!/bin/bash
# ============================================================
# New AI Agent Container — start.sh template
#
# Copy this to your agent container via Dockerfile:
#   COPY API-DOCUMENTATION/stubs/start.sh /agent/start.sh
#   RUN chmod +x /agent/start.sh
#   ENTRYPOINT ["/agent/start.sh"]
#
# Usage:
#   docker compose run --rm myagent           # standby (default)
#   docker compose run --rm myagent bash      # interactive shell
#   docker compose run --rm myagent ask "q"   # one-shot question
#   docker compose run --rm myagent status    # health check
# ============================================================

API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"
AGENT_ID="${AGENT_ID:-c10-myagent}"

# ── Banner ────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🤖  AI Agent Container                          ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  Agent ID: %-44s ║\n" "$AGENT_ID"
    printf "║  Backend : %-44s ║\n" "$API"
    echo "╠══════════════════════════════════════════════════════════╣"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "║  Status  : ✅ copilot-api ONLINE                        ║"
    else
        echo "║  Status  : ⚠️  copilot-api OFFLINE — start Container 1   ║"
    fi
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
}

# ── ask: one-shot question via C1 ────────────────────────────
cmd_ask() {
    local question="$*"
    if [ -z "$question" ]; then
        echo "Usage: ask \"your question\""
        exit 1
    fi
    echo ""
    echo "  [$AGENT_ID] Asking: $question"
    echo "  ─────────────────────────────────────────"
    local payload
    payload=$(python3 -c "
import json, sys
q = sys.argv[1]
print(json.dumps({
    'model': 'copilot',
    'messages': [{'role': 'user', 'content': q}],
    'stream': False
}))
" "$question")
    local result
    result=$(curl -sf -X POST "$API/chat/completions" \
        -H "Content-Type: application/json" \
        -H "X-Agent-ID: $AGENT_ID" \
        -H "X-Chat-Mode: work" \
        --max-time 360 \
        -d "$payload" 2>&1)
    local text
    text=$(echo "$result" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception as e:
    detail = ''
    try:
        d = json.loads(sys.stdin.read() if hasattr(sys.stdin,'read') else '')
        detail = d.get('detail', '')
    except Exception:
        pass
    print(f'ERROR: {e} | {detail or result}')
" 2>/dev/null || echo "ERROR: $result")
    echo "  $text"
    echo ""
}

# ── status: health check ──────────────────────────────────────
cmd_status() {
    echo ""
    echo "  Container Health"
    echo "  ─────────────────────────────────────────"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ C1 copilot-api     $API_ROOT"
    else
        echo "  ❌ C1 copilot-api     OFFLINE"
    fi
    if curl -sf "http://browser-auth:8001/health" > /dev/null 2>&1; then
        echo "  ✅ C3 browser-auth    http://browser-auth:8001"
        local pool
        pool=$(curl -sf "http://browser-auth:8001/status" 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"pool={d['pool_available']}/{d['pool_size']} init={d['pool_initialized']}\")" 2>/dev/null)
        echo "       $pool"
    else
        echo "  ❌ C3 browser-auth    OFFLINE"
    fi
    echo ""
}

# ── Standby health server ─────────────────────────────────────
# Runs a minimal HTTP server on :8080 answering GET /health
# so docker-compose healthcheck passes while container is idle.
cmd_standby() {
    echo "  [$AGENT_ID] Standby mode (health server on :8080)"
    echo "  To use interactively: docker compose exec $(hostname) bash"
    python3 - <<'PYEOF'
import http.server, os, json, threading

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/health/"):
            body = json.dumps({"status": "ok", "agent": os.getenv("AGENT_ID","unknown")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a):
        pass   # suppress access log

server = http.server.HTTPServer(("0.0.0.0", 8080), Handler)
print(f"  Health server listening on :8080", flush=True)
server.serve_forever()
PYEOF
}

# ── Main dispatcher ───────────────────────────────────────────
CMD="${1:-standby}"
shift 2>/dev/null || true

case "$CMD" in
    standby)    cmd_standby ;;
    ask)        print_banner; cmd_ask "$@" ;;
    status)     print_banner; cmd_status ;;
    bash|sh)    exec /bin/bash "$@" ;;
    *)
        # Pass through to your tool directly, e.g.:
        #   docker compose run --rm myagent my-tool --help
        print_banner
        exec "$CMD" "$@"
        ;;
esac
