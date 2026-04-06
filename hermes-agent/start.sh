#!/bin/bash
# ============================================================
# Copilot Hermes Agent Terminal — start.sh
#
# Usage from host:
#   docker compose run --rm hermes-agent               # this menu
#   docker compose run --rm hermes-agent bash          # direct bash
#   docker compose run --rm hermes-agent hermes        # Hermes CLI
#   docker compose run --rm hermes-agent ask "question"  # one-shot via C1
#   docker compose run --rm hermes-agent hermes-chat "q" # one-shot via Hermes
#   docker compose run --rm hermes-agent status        # health check
#
# Standby (default, persistent):
#   docker compose exec C8_hermes-agent hermes         # attach interactively
# ============================================================

API_ROOT="${OPENAI_BASE_URL:-http://app:8000/v1}"
API_ROOT="${API_ROOT%/v1}"
AGENT_ID_VAL="${AGENT_ID:-c8-hermes}"
HERMES_VERSION="$(hermes version 2>/dev/null | head -1 || echo 'unknown')"

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🧠  Hermes Agent Terminal  (C8)                  ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  C1 Backend : %-42s ║\n" "$API_ROOT"
    printf "║  Agent ID   : %-42s ║\n" "$AGENT_ID_VAL"
    printf "║  Hermes     : %-42s ║\n" "$HERMES_VERSION"
    echo "╠══════════════════════════════════════════════════════════╣"
    if curl -sf --max-time 3 "$API_ROOT/health" > /dev/null 2>&1; then
        echo "║  C1 Status  : ✅ copilot-api ONLINE                     ║"
    else
        echo "║  C1 Status  : ⚠️  copilot-api OFFLINE — start C1 first  ║"
    fi
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
}

# ── Built-in: ask (uses ask_helper.py for fast one-shot queries) ──────────────
cmd_ask() {
    local question="$*"
    if [ -z "$question" ]; then
        echo "Usage: ask \"your question\""
        exit 1
    fi
    echo ""
    echo "  [${AGENT_ID_VAL}] Asking: $question"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$question" \
        --api-url "$API_ROOT/v1/chat/completions" \
        --agent-id "$AGENT_ID_VAL" \
        --format openai
    echo ""
}

# ── Built-in: hermes-chat (uses Hermes native CLI for one-shot) ───────────────
cmd_hermes_chat() {
    local question="$*"
    if [ -z "$question" ]; then
        echo "Usage: hermes-chat \"your question\""
        exit 1
    fi
    echo ""
    echo "  [hermes] Asking: $question"
    echo "  ─────────────────────────────────────────"
    hermes chat -q "$question" 2>&1
    echo ""
}

# ── Built-in: status ─────────────────────────────────────────────────────────
cmd_status() {
    echo ""
    echo "  Health Checks — Hermes C8 Stack"
    echo "  ─────────────────────────────────────────"
    if curl -sf --max-time 5 "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ C1 copilot-api  ONLINE   $API_ROOT"
        curl -sf "$API_ROOT/v1/models" \
          | python3 -c "import json,sys; d=json.load(sys.stdin); print('     Models:', ', '.join(m['id'] for m in d['data']))" 2>/dev/null || true
    else
        echo "  ❌ C1 copilot-api  OFFLINE  → docker compose up app -d"
    fi
    if curl -sf --max-time 5 "http://browser-auth:8001/health" > /dev/null 2>&1; then
        echo "  ✅ C3 browser-auth ONLINE   http://browser-auth:8001"
    else
        echo "  ⚠️  C3 browser-auth OFFLINE (cookies may be stale)"
    fi
    echo "  🧠 Hermes version  : $HERMES_VERSION"
    echo "  🔧 Inference via   : ${HERMES_INFERENCE_PROVIDER:-openai} → $API_ROOT"
    echo "  📂 Hermes home     : ${HERMES_HOME:-/root/.hermes}"
    echo ""
    hermes doctor 2>&1 | head -20 || true
    echo ""
}

# ── Built-in: help ────────────────────────────────────────────────────────────
cmd_help() {
    echo ""
    echo "  Available commands:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  hermes              — launch Hermes interactive CLI"
    echo "  ask \"question\"      — one-shot ask via C1 (fast)"
    echo "  hermes-chat \"q\"     — one-shot ask via Hermes native CLI"
    echo "  status              — health check (C1, C3, Hermes)"
    echo "  help                — show this help"
    echo ""
    echo "  From HOST terminal:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  docker compose exec C8_hermes-agent hermes"
    echo "  docker compose run --rm hermes-agent ask \"question\""
    echo "  docker compose run --rm hermes-agent hermes-chat \"q\""
    echo "  docker compose run --rm hermes-agent status"
    echo "  docker compose run --rm hermes-agent bash"
    echo ""
    echo "  Hermes-specific:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  hermes model         — select LLM provider / model"
    echo "  hermes tools         — configure available tools"
    echo "  hermes doctor        — diagnostics"
    echo "  hermes skills list   — list installed skills"
    echo "  hermes memory list   — list remembered facts"
    echo ""
}

# ── Shell environment setup ───────────────────────────────────────────────────
setup_shell() {
    cat > /tmp/.hermes_rc << 'RC'
# Hermes Agent Terminal — shell helpers
API_ROOT="${OPENAI_BASE_URL:-http://app:8000/v1}"
API_ROOT="${API_ROOT%/v1}"
AGENT_ID_VAL="${AGENT_ID:-c8-hermes}"

ask() {
    [ -z "$*" ] && { echo "Usage: ask \"question\""; return 1; }
    echo ""
    echo "  [${AGENT_ID_VAL}] Asking: $*"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$*" \
        --api-url "$API_ROOT/v1/chat/completions" \
        --agent-id "$AGENT_ID_VAL" \
        --format openai
    echo ""
}

hermes-chat() {
    [ -z "$*" ] && { echo "Usage: hermes-chat \"question\""; return 1; }
    hermes chat -q "$*" 2>&1
}

status() {
    echo ""
    if curl -sf --max-time 5 "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ C1 copilot-api ONLINE"
    else
        echo "  ❌ C1 copilot-api OFFLINE"
    fi
    if curl -sf --max-time 5 "http://browser-auth:8001/health" > /dev/null 2>&1; then
        echo "  ✅ C3 browser-auth ONLINE"
    else
        echo "  ⚠️  C3 browser-auth OFFLINE"
    fi
    echo ""
}

help() {
    echo ""
    echo "  hermes              — Hermes interactive CLI"
    echo "  ask \"question\"      — one-shot via C1"
    echo "  hermes-chat \"q\"     — one-shot via Hermes"
    echo "  status              — health check"
    echo ""
}

export PS1='\[\033[1;35m\][hermes-agent]\[\033[0m\] \w $ '
echo ""
echo "  Type 'help' for commands, 'hermes' to launch the agent CLI."
echo ""
RC
}

# ── Route subcommands passed as args ─────────────────────────────────────────
if [ $# -gt 0 ]; then
    case "$1" in
        standby)
            echo "[C8] Hermes Agent — standby mode, health server on :8080"
            exec python3 -c "
import http.server, json, urllib.request
API='${API_ROOT}'
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            urllib.request.urlopen(API + '/health', timeout=3)
            c1 = 'online'
        except:
            c1 = 'offline'
        s = 200 if c1 == 'online' else 503
        self.send_response(s)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'container': 'C8',
            'agent': 'hermes',
            'status': 'standby',
            'c1': c1
        }).encode())
    def log_message(self, *a): pass
http.server.HTTPServer(('0.0.0.0', 8080), H).serve_forever()
"
            ;;
        ask)
            shift
            cmd_ask "$@"
            exit $?
            ;;
        hermes-chat)
            shift
            cmd_hermes_chat "$@"
            exit $?
            ;;
        hermes)
            print_banner
            exec hermes
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
            exec bash --rcfile /tmp/.hermes_rc
            ;;
        python3)
            exec "$@"
            ;;
        *)
            # Unknown arg — pass through (e.g. hermes subcommands)
            exec "$@"
            ;;
    esac
fi

# ── Interactive menu (no args) ────────────────────────────────────────────────
print_banner

echo "  Available modes:"
echo "    1) hermes        — launch Hermes interactive CLI (persistent memory + skills)"
echo "    2) ask           — drop into bash shell with ask / status helpers"
echo ""
echo "  Or from host:"
echo "    docker compose exec C8_hermes-agent hermes"
echo "    docker compose run --rm hermes-agent ask \"your question\""
echo ""

read -rp "  Choose [1-2]: " choice

case "$choice" in
    1)
        echo ""
        echo "  Starting Hermes CLI (backend: $API_ROOT)"
        echo "  Tips: /memory  /skills  /cron  /tools  /exit"
        echo ""
        exec hermes
        ;;
    2)
        setup_shell
        exec bash --rcfile /tmp/.hermes_rc
        ;;
    *)
        echo ""
        echo "  No valid choice — dropping into bash shell."
        setup_shell
        exec bash --rcfile /tmp/.hermes_rc
        ;;
esac
