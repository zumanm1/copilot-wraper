#!/bin/bash
# ============================================================
# Copilot AI Agent Terminal Launcher
# Connects to copilot-api (Container 1) for inference.
# ============================================================

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         🤖  Copilot AI Agent Terminal                    ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  Backend : %-44s ║\n" "$OPENAI_API_BASE"
printf "║  Model   : %-44s ║\n" "$AIDER_MODEL"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Verify the API backend is reachable
if curl -sf "${OPENAI_API_BASE%/v1}/health" > /dev/null 2>&1; then
    echo "  ✅ copilot-api is reachable"
else
    echo "  ⚠️  copilot-api not reachable at $OPENAI_API_BASE"
    echo "     Make sure Container 1 is running: docker compose up app -d"
fi
echo ""

# Discover available agents
AGENTS=()
LABELS=()

if command -v aider &>/dev/null; then
    AGENTS+=("aider")
    LABELS+=("Aider    — terminal coding agent (recommended)")
fi

if command -v opencode &>/dev/null; then
    AGENTS+=("opencode")
    LABELS+=("OpenCode — modern terminal AI agent")
fi

AGENTS+=("bash")
LABELS+=("Shell    — interactive bash (manual control)")

echo "Available agents:"
for i in "${!AGENTS[@]}"; do
    printf "  %d) %s\n" "$((i+1))" "${LABELS[$i]}"
done
echo ""

read -rp "Choose agent [1-${#AGENTS[@]}]: " choice

# Validate input
if ! [[ "$choice" =~ ^[0-9]+$ ]] || \
   [ "$choice" -lt 1 ] || \
   [ "$choice" -gt "${#AGENTS[@]}" ]; then
    echo "Invalid choice — starting bash shell."
    exec bash
fi

selected="${AGENTS[$((choice-1))]}"

case "$selected" in
    aider)
        echo ""
        echo "Starting Aider..."
        echo "  Model : $AIDER_MODEL"
        echo "  API   : $OPENAI_API_BASE"
        echo ""
        echo "Tips:"
        echo "  - Type your task or question and press Enter"
        echo "  - Aider will read/edit files in /workspace"
        echo "  - Add files: /add <filename>"
        echo "  - Quit: /exit or Ctrl+C"
        echo ""
        exec aider \
            --model "$AIDER_MODEL" \
            --openai-api-base "$OPENAI_API_BASE" \
            --openai-api-key "$OPENAI_API_KEY" \
            --no-auto-commits \
            --no-check-update
        ;;
    opencode)
        echo ""
        echo "Starting OpenCode..."
        echo "  API: $OPENAI_API_BASE"
        echo ""
        exec opencode
        ;;
    bash)
        echo ""
        echo "Starting bash shell. Available commands:"
        echo "  aider --model \$AIDER_MODEL    # Start Aider"
        echo "  opencode                       # Start OpenCode"
        echo "  curl \$OPENAI_API_BASE/../health  # Check API"
        echo ""
        exec bash
        ;;
esac
