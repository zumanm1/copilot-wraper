#!/bin/bash
# ============================================================
# OpenClaw AI Agent Terminal — start.sh (C4)
#
# OpenClaw connects to AI providers. We route it through
# Container 1 (copilot-api) as the Anthropic-compatible backend.
#
# Usage from host:
#   docker compose run --rm openclaw-terminal          # menu
#   docker compose run --rm openclaw-terminal bash     # bash shell
#   docker compose run --rm openclaw-terminal openclaw # direct
#   docker compose run --rm openclaw-terminal ask "q"  # one-shot
#   docker compose run --rm openclaw-terminal status   # health
# ============================================================

API_ROOT="${ANTHROPIC_BASE_URL:-http://app:8000}"
API="${API_ROOT}/v1"
API_KEY="${ANTHROPIC_API_KEY:-sk-ant-not-needed-xxxxxxxxxxxxx}"

# ── Pre-configure OpenClaw to use Container 1 ─────────────────
configure_openclaw() {
    local cfg_dir="${HOME}/.openclaw"
    mkdir -p "$cfg_dir"
    # Write config pointing to C1 as Anthropic-compatible backend
    cat > "$cfg_dir/config.yaml" << YAML
provider: anthropic
anthropicApiKey: "${API_KEY}"
anthropicBaseUrl: "${API_ROOT}"
model: claude-sonnet-4-6
daemon: false
telemetry: false
YAML
}

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         🤖  OpenClaw AI Agent Terminal  (C4)             ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  Backend : %-44s ║\n" "$API_ROOT"
    printf "║  Auth    : %-44s ║\n" "via Container 1 (copilot-api)"
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
    local aid="${AGENT_ID:-c4-openclaw}"
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
        python3 /workspace/calculator.py 2>/dev/null || echo "calculator.py not found in /workspace"
    else
        python3 /workspace/calculator.py "$expression" 2>/dev/null || echo "Error running calculator"
    fi
}

# ── Built-in: status ─────────────────────────────────────────────────────────
cmd_status() {
    echo ""
    echo "  Container Health"
    echo "  ─────────────────────────────────────────"
    if curl -sf "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ Container 1 (copilot-api)     $API_ROOT"
    else
        echo "  ❌ Container 1 (copilot-api)     OFFLINE"
    fi
    echo "  ANTHROPIC_BASE_URL → $API_ROOT"
    openclaw --version 2>/dev/null || openclaw version 2>/dev/null || echo "  openclaw: installed"
    echo ""
}

# ── Shell environment setup ────────────────────────────────────────────────────
setup_shell() {
    cat > /tmp/.openclaw_rc << 'RC'
API_ROOT="${ANTHROPIC_BASE_URL:-http://app:8000}"
API_KEY="${ANTHROPIC_API_KEY:-sk-ant-not-needed-xxxxxxxxxxxxx}"

ask() {
    local question="$*"
    [ -z "$question" ] && { echo "Usage: ask \"question\""; return 1; }
    local aid="${AGENT_ID:-c4-openclaw}"
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
    python3 /workspace/calculator.py "$@" 2>/dev/null || echo "calculator.py not found"
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
    echo "  ask \"question\"     — query via /v1/messages"
    echo "  openclaw           — launch OpenClaw agent"
    echo "  status             — check container health"
    echo ""
}

export PS1='\[\033[1;32m\][openclaw]\[\033[0m\] \w $ '
echo ""
echo "  Type 'help' for commands, 'openclaw' to launch OpenClaw."
echo "  Backend: $API_ROOT"
echo ""
RC
}

# ── Apply config on every start ───────────────────────────────────────────────
configure_openclaw

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
        bash|sh)
            print_banner
            setup_shell
            exec bash --rcfile /tmp/.openclaw_rc
            ;;
        openclaw)
            print_banner
            shift
            exec openclaw "$@"
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
echo "    1) OpenClaw — AI coding agent (autonomous, routes through copilot-api)"
echo "    2) Shell    — interactive bash (ask / status helpers built-in)"
echo ""
echo "  Or connect directly from host:"
echo "    docker compose run --rm openclaw-terminal bash"
echo "    docker compose run --rm openclaw-terminal openclaw"
echo "    docker compose run --rm openclaw-terminal ask \"your question\""
echo ""

read -rp "  Choose [1-2]: " choice

case "$choice" in
    1)
        echo ""
        echo "  Starting OpenClaw (backend: $API_ROOT)"
        echo ""
        exec openclaw
        ;;
    2|*)
        setup_shell
        exec bash --rcfile /tmp/.openclaw_rc
        ;;
esac
