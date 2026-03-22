#!/bin/bash
# ============================================================
# Copilot AI Agent Terminal — start.sh
#
# Usage from host:
#   docker compose run --rm agent-terminal           # this menu
#   docker compose run --rm agent-terminal bash      # direct bash
#   docker compose run --rm agent-terminal aider     # direct Aider
#   docker compose run --rm agent-terminal opencode  # direct OpenCode
#   docker compose run --rm agent-terminal ask "your question"
#   docker compose run --rm agent-terminal calc "10 million + 5 million + 500k"
# ============================================================

API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"
MODEL="${AIDER_MODEL:-openai/copilot}"

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🤖  Copilot AI Agent Terminal                    ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  Backend : %-44s ║\n" "$API"
    printf "║  Model   : %-44s ║\n" "$MODEL"
    echo "╠══════════════════════════════════════════════════════════╣"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "║  Status  : ✅ copilot-api ONLINE                        ║"
    else
        echo "║  Status  : ⚠️  copilot-api OFFLINE — start Container 1   ║"
    fi
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
}

# ── Built-in: ask ─────────────────────────────────────────────────────────────
cmd_ask() {
    local question="$*"
    if [ -z "$question" ]; then
        echo "Usage: ask \"your question\""
        exit 1
    fi
    local aid="${AGENT_ID:-c2-aider}"
    echo ""
    echo "  [${aid}] Asking: $question"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$question" \
        --api-url "$API/chat/completions" \
        --agent-id "$aid" \
        --format openai
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
    # Container 1
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ Container 1 (copilot-api)     $API_ROOT"
        curl -sf "$API_ROOT/v1/models" \
          | python3 -c "import json,sys; d=json.load(sys.stdin); print('     Models:', ', '.join(m['id'] for m in d['data']))"
    else
        echo "  ❌ Container 1 (copilot-api)     OFFLINE"
    fi
    # Container 3
    if curl -sf "http://browser-auth:8001/health" > /dev/null 2>&1; then
        echo "  ✅ Container 3 (browser-auth)    http://browser-auth:8001"
        curl -sf "http://browser-auth:8001/status" \
          | python3 -c "import json,sys; d=json.load(sys.stdin); print('     Browser:', d.get('browser','?'), '| Pages:', d.get('open_pages',0))"
    else
        echo "  ⚠️  Container 3 (browser-auth)   OFFLINE or not started"
    fi
    echo ""
}

# ── Built-in: help ────────────────────────────────────────────────────────────
cmd_help() {
    echo ""
    echo "  Available commands inside the bash shell:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  ask \"question\"          — ask Copilot a question directly"
    echo "  calc \"expression\"       — run calculator (e.g. 10m + 5m + 500k)"
    echo "  calc                    — run full calculator test suite"
    echo "  aider                   — launch Aider coding agent"
    echo "  opencode                — launch OpenCode agent"
    echo "  status                  — check all container health"
    echo "  help                    — show this help"
    echo ""
    echo "  From HOST terminal:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  docker compose run --rm agent-terminal"
    echo "  docker compose run --rm agent-terminal bash"
    echo "  docker compose run --rm agent-terminal aider"
    echo "  docker compose run --rm agent-terminal opencode"
    echo "  docker compose run --rm agent-terminal ask \"question\""
    echo "  docker compose run --rm agent-terminal calc \"10m + 5m + 500k\""
    echo ""
}

# ── Shell environment setup (bashrc for interactive shell) ────────────────────
setup_shell() {
    # Inject helper functions into the interactive bash session
    export -f cmd_ask cmd_calc cmd_status cmd_help 2>/dev/null || true

    cat > /tmp/.agent_rc << 'RC'
# Copilot Agent Terminal — shell helpers
API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"

ask() {
    local question="$*"
    [ -z "$question" ] && { echo "Usage: ask \"question\""; return 1; }
    local aid="${AGENT_ID:-c2-aider}"
    echo ""
    echo "  [${aid}] Asking: $question"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$question" \
        --api-url "$API/chat/completions" \
        --agent-id "$aid" \
        --format openai
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
    API_ROOT="${OPENAI_API_BASE%/v1}"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ Container 1 (copilot-api) ONLINE"
    else
        echo "  ❌ Container 1 (copilot-api) OFFLINE"
    fi
    if curl -sf "http://browser-auth:8001/health" > /dev/null 2>&1; then
        echo "  ✅ Container 3 (browser-auth) ONLINE"
    else
        echo "  ⚠️  Container 3 (browser-auth) offline"
    fi
    echo ""
}

help() {
    echo ""
    echo "  ask \"question\"      — query Copilot directly"
    echo "  calc \"expr\"         — calculator (e.g. 10m + 5m + 500k)"
    echo "  calc                — run full test suite"
    echo "  aider               — launch Aider coding agent"
    echo "  opencode            — launch OpenCode agent"
    echo "  status              — check container health"
    echo ""
}

export PS1='\[\033[1;36m\][copilot-agent]\[\033[0m\] \w $ '
echo ""
echo "  Type 'help' for available commands, 'ask \"...\"' to query Copilot."
echo ""
RC
}

# ── Route subcommands passed as args ─────────────────────────────────────────
# Allows: docker compose run --rm agent-terminal ask "..."
#         docker compose run --rm agent-terminal calc "..."
#         docker compose run --rm agent-terminal bash
#         docker compose run --rm agent-terminal aider
if [ $# -gt 0 ]; then
    case "$1" in
        standby)
            echo "[C2] Standby mode — health server on port 8080"
            exec python3 -c "
import http.server,json,urllib.request
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try: urllib.request.urlopen('${API_ROOT}/health',timeout=3); c1='online'
        except: c1='offline'
        s=200 if c1=='online' else 503
        self.send_response(s);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'container':'C2','status':'standby','c1':c1}).encode())
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
            exec bash --rcfile /tmp/.agent_rc
            ;;
        aider)
            print_banner
            exec aider \
                --model "$MODEL" \
                --openai-api-base "$API" \
                --openai-api-key "${OPENAI_API_KEY:-not-needed}" \
                --no-auto-commits \
                --no-check-update
            ;;
        opencode)
            print_banner
            exec opencode
            ;;
        python3)
            exec "$@"
            ;;
        *)
            # Unknown arg — pass to bash
            exec "$@"
            ;;
    esac
fi

# ── Interactive menu (no args) ────────────────────────────────────────────────
print_banner

AGENTS=()
LABELS=()

command -v aider    &>/dev/null && AGENTS+=("aider")    && LABELS+=("Aider    — AI coding agent  (edits files, uses Copilot)")
command -v opencode &>/dev/null && AGENTS+=("opencode") && LABELS+=("OpenCode — modern AI agent  (TUI, uses Copilot)")
AGENTS+=("bash")
LABELS+=("Shell    — interactive bash  (ask / calc / status built-in)")

echo "  Available agents:"
for i in "${!AGENTS[@]}"; do
    printf "    %d) %s\n" "$((i+1))" "${LABELS[$i]}"
done
echo ""
echo "  Or connect directly from host:"
echo "    docker compose run --rm agent-terminal bash"
echo "    docker compose run --rm agent-terminal ask \"your question\""
echo "    docker compose run --rm agent-terminal calc \"10m + 5m + 500k\""
echo ""

read -rp "  Choose [1-${#AGENTS[@]}]: " choice

if ! [[ "$choice" =~ ^[0-9]+$ ]] || \
   [ "$choice" -lt 1 ] || \
   [ "$choice" -gt "${#AGENTS[@]}" ]; then
    echo ""
    echo "  No valid choice — dropping into bash shell."
    setup_shell
    exec bash --rcfile /tmp/.agent_rc
fi

selected="${AGENTS[$((choice-1))]}"

case "$selected" in
    aider)
        echo ""
        echo "  Starting Aider (model: $MODEL | api: $API)"
        echo "  Tips: /add <file>  /diff  /run  /exit"
        echo ""
        exec aider \
            --model "$MODEL" \
            --openai-api-base "$API" \
            --openai-api-key "${OPENAI_API_KEY:-not-needed}" \
            --no-auto-commits \
            --no-check-update
        ;;
    opencode)
        echo ""
        echo "  Starting OpenCode (api: $API)"
        echo ""
        exec opencode
        ;;
    bash)
        setup_shell
        exec bash --rcfile /tmp/.agent_rc
        ;;
esac
