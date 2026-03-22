#!/bin/bash
# ============================================================
# Container 7b — OpenClaw CLI / TUI  start.sh
#
# Companion CLI for the C7a OpenClaw gateway.
# Provides all 3 reference-doc access modes plus extras:
#
# Mode 2 (container CLI → gateway, per reference doc §8):
#   docker compose run --rm openclaw-cli tui
#   docker compose run --rm openclaw-cli gateway status
#   docker compose run --rm openclaw-cli devices list
#   docker compose run --rm openclaw-cli dashboard --no-open
#
# Extra — one-shot ask (→ C1 directly, professor persona):
#   docker compose run --rm openclaw-cli ask "question"
#
# Extra — interactive bash shell with helpers:
#   docker compose run --rm openclaw-cli bash
#
# Extra — pass-through any openclaw subcommand:
#   docker compose run --rm openclaw-cli config set gateway.bind lan
#   docker compose run --rm openclaw-cli channels add --channel telegram ...
#
# ask: bypasses gateway → C1 /v1/chat/completions (fast, one-shot)
#      uses X-Agent-ID: c7-openclaw for session isolation in C1
# tui: connects to C7a gateway → C1 → Copilot (stateful conversation)
#
# Reference: OpenClaw Docker Setup Guide v2026.3.13 (adapted)
# ============================================================

API_ROOT="${API_URL:-http://app:8000}"
GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://openclaw-gateway:18789}"
GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-copilot-local-gateway-token}"
AGENT_ID="${AGENT_ID:-c7-openclaw}"

# ── Banner ────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║      🤖  OpenClaw CLI / TUI  (C7b)  v2026.3.13          ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  C1 Backend : %-42s ║\n" "$API_ROOT"
    printf "║  Gateway    : %-42s ║\n" "$GATEWAY_URL"
    printf "║  Agent ID   : %-42s ║\n" "$AGENT_ID"
    echo "╠══════════════════════════════════════════════════════════╣"
    if curl -sf --max-time 3 "$API_ROOT/health" > /dev/null 2>&1; then
        echo "║  C1 Status  : ✅ copilot-api ONLINE                     ║"
    else
        echo "║  C1 Status  : ⚠️  copilot-api OFFLINE — start C1 first  ║"
    fi
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
}

# ── ask (direct → C1, EC3: bypasses gateway intentionally for speed) ──────
cmd_ask() {
    local question="$*"
    if [ -z "$question" ]; then
        echo "Usage: ask \"your question\""
        exit 1
    fi
    echo ""
    echo "  [${AGENT_ID}] Asking: $question"
    echo "  ─────────────────────────────────────────"
    python3 /workspace/ask_helper.py \
        "$question" \
        --api-url "$API_ROOT/v1/chat/completions" \
        --agent-id "$AGENT_ID" \
        --format openai
    echo ""
}

# ── tui (Mode 2: container CLI → C7a gateway → C1 → Copilot) ─────────────
cmd_tui() {
    echo ""
    echo "  Connecting to OpenClaw gateway at $GATEWAY_URL"
    echo "  TUI keyboard shortcuts:"
    echo "    Enter=send  Esc=abort  Ctrl+D=exit  Ctrl+G=agent picker  Ctrl+P=session picker"
    echo "  TUI built-in commands:"
    echo "    /status  /deliver on  /deliver off  !<shell-cmd>"
    echo ""
    exec openclaw tui --url "$GATEWAY_URL" --token "$GATEWAY_TOKEN"
}

# ── status (reference doc health checks §6, adapted) ─────────────────────
cmd_status() {
    echo ""
    echo "  Health Checks — OpenClaw C7 Stack"
    echo "  ─────────────────────────────────────────"

    # C1 health
    if curl -sf --max-time 3 "$API_ROOT/health" > /dev/null 2>&1; then
        echo "  ✅ C1 copilot-api        ONLINE   $API_ROOT"
    else
        echo "  ❌ C1 copilot-api        OFFLINE  → docker compose up app -d"
    fi

    # C7a /healthz (reference doc §6: liveness)
    GW_HTTP="${GATEWAY_URL/ws:\/\//http://}"
    GW_HTTP="${GW_HTTP/wss:\/\//https://}"
    if curl -sf --max-time 3 "$GW_HTTP/healthz" > /dev/null 2>&1; then
        echo "  ✅ C7a gateway /healthz  ONLINE   $GW_HTTP"
    else
        echo "  ⚠️  C7a gateway /healthz OFFLINE  → docker compose up openclaw-gateway -d"
    fi

    # C7a /readyz (reference doc §6: readiness)
    if curl -sf --max-time 3 "$GW_HTTP/readyz" > /dev/null 2>&1; then
        echo "  ✅ C7a gateway /readyz   READY    $GW_HTTP"
    else
        echo "  ⚠️  C7a gateway /readyz  NOT READY (provider may be connecting)"
    fi

    # C1 active agent sessions
    echo ""
    echo "  C1 Active Agent Sessions:"
    curl -sf --max-time 3 "$API_ROOT/v1/sessions" 2>/dev/null \
        | python3 -m json.tool 2>/dev/null \
        | sed 's/^/    /' \
        || echo "    (sessions endpoint unavailable)"

    echo ""
    echo "  Reference doc access modes:"
    echo "    Mode 1 (host CLI): openclaw tui --url ws://127.0.0.1:18789 --token \$OPENCLAW_GATEWAY_TOKEN"
    echo "    Mode 2 (this):     docker compose run --rm openclaw-cli tui"
    echo "    Mode 3 (debug):    docker compose exec openclaw-gateway sh"
    echo ""
}

# ── Shell env setup (bash helpers) ───────────────────────────────────────
setup_shell() {
    cat > /tmp/.openclaw_cli_rc << 'RC'
API_ROOT="${API_URL:-http://app:8000}"
GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://openclaw-gateway:18789}"
GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-copilot-local-gateway-token}"
AGENT_ID="${AGENT_ID:-c7-openclaw}"

ask() {
    [ -z "$*" ] && { echo "Usage: ask \"question\""; return 1; }
    echo "  [${AGENT_ID}] $*"
    python3 /workspace/ask_helper.py "$*" \
        --api-url "$API_ROOT/v1/chat/completions" \
        --agent-id "$AGENT_ID" \
        --format openai
}

tui() {
    exec openclaw tui --url "$GATEWAY_URL" --token "$GATEWAY_TOKEN"
}

status() {
    echo ""
    curl -sf --max-time 3 "$API_ROOT/health" > /dev/null \
        && echo "  ✅ C1 copilot-api ONLINE" \
        || echo "  ❌ C1 copilot-api OFFLINE"
}

help() {
    echo ""
    echo "  ask \"q\"           — one-shot query → C1 (professor persona)"
    echo "  tui               — TUI session  → C7a gateway → C1"
    echo "  status            — health checks (C1 + C7a gateway)"
    echo "  openclaw <cmd>    — raw OpenClaw CLI passthrough"
    echo "  devices list      — list devices registered to gateway"
    echo "  dashboard --no-open — get browser dashboard URL"
    echo ""
}

export PS1='\[\033[1;35m\][openclaw-c7b]\[\033[0m\] \w $ '
echo ""
echo "  OpenClaw C7b shell ready. Type 'help' for commands."
echo "  C1 Backend: $API_ROOT | Gateway: $GATEWAY_URL"
echo ""
RC
}

# ── Subcommand router ─────────────────────────────────────────────────────
if [ $# -gt 0 ]; then
    case "$1" in
        ask)
            shift
            cmd_ask "$@"
            exit $?
            ;;
        tui)
            cmd_tui
            ;;
        status)
            cmd_status
            exit 0
            ;;
        bash|sh)
            print_banner
            setup_shell
            exec bash --rcfile /tmp/.openclaw_cli_rc
            ;;
        python3)
            exec "$@"
            ;;
        # Pass-through all native openclaw subcommands (reference doc Modes 2+3)
        # e.g.: gateway status, devices list, dashboard --no-open,
        #       channels add, config set gateway.bind lan
        *)
            exec openclaw "$@"
            ;;
    esac
fi

# ── Interactive menu ──────────────────────────────────────────────────────
print_banner

echo "  Available options:"
echo "    1) TUI     — full conversation via C7a gateway (stateful, Ctrl+G for agents)"
echo "    2) Ask     — one-shot query via C1 (fast, professor/polymath persona)"
echo "    3) Shell   — bash with ask / tui / status / openclaw helpers"
echo ""
echo "  Or run directly from host:"
echo "    docker compose run --rm openclaw-cli tui"
echo "    docker compose run --rm openclaw-cli ask \"your question\""
echo "    docker compose run --rm openclaw-cli devices list"
echo "    docker compose run --rm openclaw-cli dashboard --no-open"
echo ""

read -rp "  Choose [1-3]: " choice

case "$choice" in
    1)
        cmd_tui
        ;;
    2)
        read -rp "  Your question: " q
        cmd_ask "$q"
        ;;
    3|*)
        setup_shell
        exec bash --rcfile /tmp/.openclaw_cli_rc
        ;;
esac
