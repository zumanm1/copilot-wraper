#!/bin/bash
# ============================================================
# Cline AI Agent Terminal — start.sh
#
# Usage from host:
#   docker compose run --rm cline-terminal           # this menu
#   docker compose run --rm cline-terminal bash      # direct bash
#   docker compose run --rm cline-terminal cline     # direct Cline
#   docker compose run --rm cline-terminal ask "your question"
#   docker compose run --rm cline-terminal calc "10 million + 5 million + 500k"
#   docker compose run --rm cline-terminal status    # health check
# ============================================================

API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"
MODEL="${CLINE_MODEL:-copilot}"

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🤖  Cline AI Agent Terminal  (C4)                ║"
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
    echo ""
    echo "  Asking Copilot: $question"
    echo "  ─────────────────────────────────────────"
    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
    'model': 'copilot',
    'messages': [{'role': 'user', 'content': sys.argv[1]}],
    'stream': False
}))
" "$question")
    curl -sf -X POST "$API/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d['choices'][0]['message']['content'])
print()
print('  [tokens:', d['usage']['total_tokens'], '| model:', d['model'] + ']')
"
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
          | python3 -c "import json,sys; d=json.load(sys.stdin); print('     Models:', ', '.join(m['id'] for m in d['data']))"
    else
        echo "  ❌ Container 1 (copilot-api)     OFFLINE"
    fi
    echo ""
}

# ── Built-in: help ────────────────────────────────────────────────────────────
cmd_help() {
    echo ""
    echo "  Available commands:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  ask \"question\"     — ask Copilot a question directly"
    echo "  calc \"expression\"  — run calculator (e.g. 10m + 5m + 500k)"
    echo "  calc               — run full calculator test suite"
    echo "  cline              — launch Cline AI coding agent"
    echo "  status             — check container health"
    echo "  help               — show this help"
    echo ""
    echo "  From HOST terminal:"
    echo "  ─────────────────────────────────────────────────────────"
    echo "  docker compose run --rm cline-terminal"
    echo "  docker compose run --rm cline-terminal bash"
    echo "  docker compose run --rm cline-terminal cline"
    echo "  docker compose run --rm cline-terminal ask \"question\""
    echo "  docker compose run --rm cline-terminal calc \"10m + 5m + 500k\""
    echo ""
}

# ── Shell environment setup ────────────────────────────────────────────────────
setup_shell() {
    cat > /tmp/.cline_rc << 'RC'
# Cline Agent Terminal — shell helpers
API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"

ask() {
    local question="$*"
    [ -z "$question" ] && { echo "Usage: ask \"question\""; return 1; }
    echo ""
    echo "  Asking Copilot: $question"
    echo "  ─────────────────────────────────────────"
    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({'model':'copilot','messages':[{'role':'user','content':sys.argv[1]}],'stream':False}))
" "$question")
    curl -sf -X POST "$API/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d['choices'][0]['message']['content'])
print()
print('  [tokens:', d['usage']['total_tokens'], '| model:', d['model'] + ']')
"
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
    echo ""
}

help() {
    echo ""
    echo "  ask \"question\"     — query Copilot directly"
    echo "  calc \"expr\"        — calculator (e.g. 10m + 5m + 500k)"
    echo "  calc               — run full test suite"
    echo "  cline              — launch Cline coding agent"
    echo "  status             — check container health"
    echo ""
}

export PS1='\[\033[1;35m\][cline-agent]\[\033[0m\] \w $ '
echo ""
echo "  Type 'help' for available commands, 'cline' to launch Cline."
echo ""
RC
}

# ── Route subcommands ─────────────────────────────────────────────────────────
if [ $# -gt 0 ]; then
    case "$1" in
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
            exec bash --rcfile /tmp/.cline_rc
            ;;
        cline)
            print_banner
            shift
            exec cline "$@"
            ;;
        python3)
            exec "$@"
            ;;
        *)
            exec "$@"
            ;;
    esac
fi

# ── Interactive menu (no args) ────────────────────────────────────────────────
print_banner

echo "  Available options:"
echo "    1) Cline  — AI coding agent (autonomous, uses Copilot backend)"
echo "    2) Shell  — interactive bash (ask / calc / status built-in)"
echo ""
echo "  Or connect directly from host:"
echo "    docker compose run --rm cline-terminal bash"
echo "    docker compose run --rm cline-terminal cline"
echo "    docker compose run --rm cline-terminal ask \"your question\""
echo "    docker compose run --rm cline-terminal calc \"10m + 5m + 500k\""
echo ""

read -rp "  Choose [1-2]: " choice

case "$choice" in
    1)
        echo ""
        echo "  Starting Cline (model: $MODEL | api: $API)"
        echo ""
        exec cline
        ;;
    2|*)
        setup_shell
        exec bash --rcfile /tmp/.cline_rc
        ;;
esac
