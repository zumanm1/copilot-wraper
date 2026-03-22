#!/bin/bash
# ============================================================
# Claude Code Terminal — start.sh
#
# Usage from host:
#   docker compose run --rm claude-code-terminal         # menu
#   docker compose run --rm claude-code-terminal bash    # direct bash
#   docker compose run --rm claude-code-terminal ask "q" # one-shot query
#   docker compose run --rm claude-code-terminal status  # health check
# ============================================================

API_ROOT="${ANTHROPIC_BASE_URL:-http://app:8000}"
API_KEY="${ANTHROPIC_API_KEY:-sk-ant-not-needed-xxxxxxxxxxxxx}"

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🤖  Claude Code Terminal  (C5)                   ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  Backend : %-44s ║\n" "$API_ROOT"
    printf "║  Endpoint: %-44s ║\n" "$API_ROOT/v1/messages"
    echo "╠══════════════════════════════════════════════════════════╣"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "║  Status  : ✅ copilot-api ONLINE                        ║"
    else
        echo "║  Status  : ⚠️  copilot-api OFFLINE — start Container 1   ║"
    fi
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
}

# ── Built-in: ask (uses /v1/messages Anthropic-compat endpoint) ───────────────
cmd_ask() {
    local question="$*"
    if [ -z "$question" ]; then
        echo "Usage: ask \"your question\""
        exit 1
    fi
    local aid="${AGENT_ID:-c5-claude-code}"
    echo ""
    echo "  [${aid}] Asking: $question"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$question" \
        --api-url "$API_ROOT/v1/messages" \
        --agent-id "$aid" \
        --format anthropic \
        --api-key "$API_KEY"
    echo ""
}

# ── Built-in: calc ────────────────────────────────────────────────────────────
cmd_calc() {
    local expression="$*"
    if [ -z "$expression" ]; then
        python3 /workspace/calculator.py
    else
        python3 /workspace/calculator.py "$expression"
    fi
}

# ── Built-in: status ─────────────────────────────────────────────────────────
cmd_status() {
    echo ""
    echo "  Container Health"
    echo "  ─────────────────────────────────────────"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ Container 1 (copilot-api)     $API_ROOT"
        curl -sf "$API_ROOT/v1/models" \
          | python3 -c "import json,sys; d=json.load(sys.stdin); print('     Models:', ', '.join(m['id'] for m in d['data']))" 2>/dev/null || true
    else
        echo "  ❌ Container 1 (copilot-api)     OFFLINE"
    fi
    echo "  ANTHROPIC_BASE_URL → $API_ROOT"
    echo ""
}

# ── Built-in: help ────────────────────────────────────────────────────────────
cmd_help() {
    echo ""
    echo "  Available commands:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  ask \"question\"     — ask via /v1/messages endpoint"
    echo "  calc \"expression\"  — run calculator (e.g. 10m + 5m + 500k)"
    echo "  calc               — run full calculator test suite"
    echo "  status             — check container health"
    echo "  help               — show this help"
    echo ""
    echo "  From HOST terminal:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  docker compose run --rm claude-code-terminal"
    echo "  docker compose run --rm claude-code-terminal bash"
    echo "  docker compose run --rm claude-code-terminal ask \"question\""
    echo "  docker compose run --rm claude-code-terminal calc \"10m + 5m + 500k\""
    echo ""
}

# ── Shell environment setup ────────────────────────────────────────────────────
setup_shell() {
    cat > /tmp/.claude_code_rc << 'RC'
# Claude Code Terminal — shell helpers
API_ROOT="${ANTHROPIC_BASE_URL:-http://app:8000}"
API_KEY="${ANTHROPIC_API_KEY:-sk-ant-not-needed-xxxxxxxxxxxxx}"

ask() {
    local question="$*"
    [ -z "$question" ] && { echo "Usage: ask \"question\""; return 1; }
    local aid="${AGENT_ID:-c5-claude-code}"
    echo ""
    echo "  [${aid}] Asking: $question"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$question" \
        --api-url "$API_ROOT/v1/messages" \
        --agent-id "$aid" \
        --format anthropic \
        --api-key "$API_KEY"
    echo ""
}

calc() {
    if [ -z "$*" ]; then
        python3 /workspace/calculator.py
    else
        python3 /workspace/calculator.py "$@"
    fi
}

status() {
    echo ""
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ Container 1 (copilot-api) ONLINE"
    else
        echo "  ❌ Container 1 (copilot-api) OFFLINE"
    fi
    echo "  ANTHROPIC_BASE_URL → $API_ROOT"
    echo ""
}

help() {
    echo ""
    echo "  ask \"question\"     — query via /v1/messages"
    echo "  calc \"expr\"        — calculator (e.g. 10m + 5m + 500k)"
    echo "  calc               — run full test suite"
    echo "  claude             — launch Claude Code interactive session"
    echo "  status             — check container health"
    echo ""
}

export PS1='\[\033[1;33m\][claude-code]\[\033[0m\] \w $ '
echo ""
echo "  Type 'help' for commands, 'claude' to launch Claude Code."
echo "  ANTHROPIC_BASE_URL is routed through copilot-api."
echo ""
RC
}

# ── Route subcommands ─────────────────────────────────────────────────────────
if [ $# -gt 0 ]; then
    case "$1" in
        standby)
            echo "[C5] Standby mode — health server on port 8080"
            exec python3 -c "
import http.server,json,urllib.request
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try: urllib.request.urlopen('${API_ROOT}/health',timeout=3); c1='online'
        except: c1='offline'
        s=200 if c1=='online' else 503
        self.send_response(s);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'container':'C5','status':'standby','c1':c1}).encode())
    def log_message(self,*a):pass
http.server.HTTPServer(('0.0.0.0',8080),H).serve_forever()
"
            ;;
        ask)
            shift
            cmd_ask "$@"
            exit $?
            ;;
        calc)
            shift
            cmd_calc "$@"
            exit $?
            ;;
        status)
            cmd_status
            exit 0
            ;;
        help)
            cmd_help
            exit 0
            ;;
        bash|sh)
            print_banner
            setup_shell
            exec bash --rcfile /tmp/.claude_code_rc
            ;;
        python3)
            exec "$@"
            ;;
        *)
            exec "$@"
            ;;
    esac
fi

# ── Interactive menu (no args) — launch claude directly ───────────────────────
print_banner

echo "  Starting Claude Code interactive session..."
echo "  (ANTHROPIC_BASE_URL → $API_ROOT)"
echo ""
echo "  Tip: Run with 'bash' subcommand for a helper shell:"
echo "    docker compose run --rm claude-code-terminal bash"
echo ""

exec claude
