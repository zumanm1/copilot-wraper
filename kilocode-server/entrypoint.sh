#!/bin/bash
# ============================================================
# KiloCode CLI Terminal — entrypoint.sh (C6)
#
# KiloCode CLI configured to use Container 1 (copilot-api)
# as the OpenAI-compatible AI backend.
#
# Usage from host:
#   docker compose run --rm kilocode-terminal          # menu
#   docker compose run --rm kilocode-terminal bash     # bash shell
#   docker compose run --rm kilocode-terminal kilo     # direct kilo
#   docker compose run --rm kilocode-terminal ask "q"  # one-shot
#   docker compose run --rm kilocode-terminal status   # health
# ============================================================

API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"
MODEL="${KILO_MODEL:-copilot}"

# ── Pre-configure KiloCode to use Container 1 ─────────────────
configure_kilo() {
    local cfg_dir="${HOME}/.kilo"
    mkdir -p "$cfg_dir"
    cat > "$cfg_dir/config.json" << JSON
{
  "apiProvider": "openai-compatible",
  "openAiCompatible": {
    "baseUrl": "${API}",
    "apiKey": "not-needed",
    "modelId": "${MODEL}"
  },
  "telemetry": false
}
JSON
    # Also try kilocode config path
    mkdir -p "${HOME}/.kilocode"
    cp "$cfg_dir/config.json" "${HOME}/.kilocode/config.json" 2>/dev/null || true
}

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🤖  KiloCode CLI Terminal  (C6)                  ║"
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
    local aid="${AGENT_ID:-c6-kilocode}"
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
    echo ""
}

# ── Shell environment setup ────────────────────────────────────────────────────
setup_shell() {
    cat > /tmp/.kilo_rc << 'RC'
API="${OPENAI_API_BASE:-http://app:8000/v1}"
API_ROOT="${API%/v1}"

ask() {
    local question="$*"
    [ -z "$question" ] && { echo "Usage: ask \"question\""; return 1; }
    local aid="${AGENT_ID:-c6-kilocode}"
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

status() {
    echo ""
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ Container 1 (copilot-api) ONLINE"
    else
        echo "  ❌ Container 1 (copilot-api) OFFLINE"
    fi
    echo ""
}

help() {
    echo ""
    echo "  ask \"question\"  — query via /v1/chat/completions"
    echo "  kilo            — launch KiloCode interactive TUI"
    echo "  status          — check container health"
    echo ""
}

export PS1='\[\033[1;34m\][kilocode]\[\033[0m\] \w $ '
echo ""
echo "  Type 'help' for commands, 'kilo' to launch KiloCode."
echo "  Backend: $API"
echo ""
RC
}

# ── Apply config ──────────────────────────────────────────────────────────────
configure_kilo

# ── Route subcommands ─────────────────────────────────────────────────────────
if [ $# -gt 0 ]; then
    case "$1" in
        ask)
            shift
            cmd_ask "$@"
            exit $?
            ;;
        status)
            cmd_status
            exit 0
            ;;
        bash|sh)
            print_banner
            setup_shell
            exec bash --rcfile /tmp/.kilo_rc
            ;;
        kilo|kilocode)
            print_banner
            shift
            exec kilo "$@" 2>/dev/null || exec kilocode "$@"
            ;;
        python3)
            exec "$@"
            ;;
        *)
            exec "$@"
            ;;
    esac
fi

# ── Interactive menu ──────────────────────────────────────────────────────────
print_banner

echo "  Available options:"
echo "    1) KiloCode — AI coding agent TUI (uses Copilot backend)"
echo "    2) Shell    — interactive bash (ask / status helpers)"
echo ""
echo "  Or connect directly from host:"
echo "    docker compose run --rm kilocode-terminal bash"
echo "    docker compose run --rm kilocode-terminal kilo"
echo "    docker compose run --rm kilocode-terminal ask \"your question\""
echo ""

read -rp "  Choose [1-2]: " choice

case "$choice" in
    1)
        echo ""
        echo "  Starting KiloCode (api: $API)"
        echo ""
        kilo 2>/dev/null || kilocode 2>/dev/null || { echo "  Error: kilo command not found"; exec bash; }
        ;;
    2|*)
        setup_shell
        exec bash --rcfile /tmp/.kilo_rc
        ;;
esac
