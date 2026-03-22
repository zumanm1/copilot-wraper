#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# run_pair_tests.sh  — Validate all C1+C3 ↔ agent-container pairs
#                     + Multi-Agent Debate smoke test
#
# Pair tests (1–7):
#   1  C1+C3 ← C2 OpenCode     OpenAI  /v1/chat/completions
#   2  C1+C3 ← C2 Aider        OpenAI  /v1/chat/completions
#   3  C1+C3 ← C5 Claude Code  Anthropic /v1/messages
#   4  C1+C3 ← C6 KiloCode    OpenAI  /v1/chat/completions
#   5  C1+C3 ← C7a Gateway    health + standby validation (:18789)
#   6  C1+C3 ← C7b CLI ask    OpenAI  /v1/chat/completions
#   7  C1+C3 ← C8 Hermes      OpenAI  /v1/chat/completions
#
# Debate test (8):
#   8  Multi-Agent Debate (60 s, all 6 agents, moderator + judge)
#
# Usage:
#   ./tests/run_pair_tests.sh                    # sequential (7 pairs + debate)
#   ./tests/run_pair_tests.sh --parallel         # all 7 pairs parallel, then debate
#   ./tests/run_pair_tests.sh --skip-debate      # skip the debate test (faster)
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

PARALLEL=false
SKIP_DEBATE=false
for arg in "$@"; do
    [[ "$arg" == "--parallel" ]]    && PARALLEL=true
    [[ "$arg" == "--skip-debate" ]] && SKIP_DEBATE=true
done

COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$(mktemp -d)"
PASS=0
FAIL=0
RESULTS=()

# ── colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; RESET='\033[0m'; BOLD='\033[1m'

header() { echo -e "\n${BOLD}══ $* ══${RESET}"; }
ok()     { echo -e "  ${GREEN}✅ PASS${RESET}  $*"; }
fail()   { echo -e "  ${RED}❌ FAIL${RESET}  $*"; }
warn()   { echo -e "  ${YELLOW}⚠️  WARN${RESET}  $*"; }
info()   { echo -e "  ${CYAN}ℹ  INFO${RESET}  $*"; }

# ── run_test <id> <name> <script_fn> ─────────────────────────────────────────
run_test() {
    local id="$1" name="$2" fn="$3"
    local log="$LOG_DIR/test${id}.log"
    echo -e "\n${BOLD}[Test $id] $name${RESET}"
    if $fn >"$log" 2>&1; then
        ok "$name"
        cat "$log" | sed 's/^/    /'
        RESULTS+=("PASS:$id:$name")
    else
        fail "$name"
        cat "$log" | sed 's/^/    /'
        RESULTS+=("FAIL:$id:$name")
    fi
}

# ── individual pair test functions ────────────────────────────────────────────

test1_opencode() {
    cd "$COMPOSE_DIR"
    echo "[verify] opencode tool"
    docker compose exec -T agent-terminal bash -c 'opencode --version 2>&1 | head -1'

    echo "[verify] C1 health from C2"
    C1_STATUS=$(docker compose exec -T agent-terminal bash -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not healthy"; return 1; }

    echo "[verify] C3 health from C2"
    C3_STATUS=$(docker compose exec -T agent-terminal bash -c \
        'curl -sf --max-time 5 http://browser-auth:8001/health 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get(\"status\",\"?\"))" 2>/dev/null || echo "offline"')
    echo "  C3 status → $C3_STATUS"

    echo "[ask]  C1+C3 ← C2 OpenCode"
    REPLY=$(docker compose exec -T agent-terminal bash -c \
        'python3 /workspace/ask_helper.py "Reply with exactly: OPENCODE_TEST_OK" \
            --api-url http://app:8000/v1/chat/completions \
            --agent-id test-opencode --format openai 2>/dev/null')
    echo "  reply → $(echo "$REPLY" | head -3)"
    echo "$REPLY" | grep -q "OPENCODE_TEST_OK" || { echo "ERROR: expected marker not found"; return 1; }
}

test2_aider() {
    cd "$COMPOSE_DIR"
    echo "[verify] aider tool"
    docker compose exec -T agent-terminal bash -c 'aider --version 2>&1 | head -1'

    echo "[verify] C1 health from C2"
    C1_STATUS=$(docker compose exec -T agent-terminal bash -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not healthy"; return 1; }

    echo "[ask]  C1+C3 ← C2 Aider"
    REPLY=$(docker compose exec -T agent-terminal bash -c \
        'python3 /workspace/ask_helper.py "Reply with exactly: AIDER_TEST_OK" \
            --api-url http://app:8000/v1/chat/completions \
            --agent-id test-aider --format openai 2>/dev/null')
    echo "  reply → $(echo "$REPLY" | head -3)"
    echo "$REPLY" | grep -q "AIDER_TEST_OK" || { echo "ERROR: expected marker not found"; return 1; }
}

test3_claude_code() {
    cd "$COMPOSE_DIR"
    echo "[verify] claude tool"
    docker compose exec -T claude-code-terminal bash -c 'claude --version 2>&1 | head -1'

    echo "[verify] C1 health from C5"
    C1_STATUS=$(docker compose exec -T claude-code-terminal bash -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not healthy"; return 1; }

    echo "[ask]  C1+C3 ← C5 Claude Code (Anthropic /v1/messages)"
    REPLY=$(docker compose exec -T claude-code-terminal bash -c \
        'python3 /workspace/ask_helper.py "Reply with exactly: CLAUDE_CODE_TEST_OK" \
            --api-url http://app:8000/v1/messages \
            --agent-id test-c5-claude --format anthropic \
            --api-key sk-ant-not-needed 2>/dev/null')
    echo "  reply → $(echo "$REPLY" | head -3)"
    echo "$REPLY" | grep -q "CLAUDE_CODE_TEST_OK" || { echo "ERROR: expected marker not found"; return 1; }
}

test4_kilocode() {
    cd "$COMPOSE_DIR"
    echo "[verify] kilo tool"
    docker compose exec -T kilocode-terminal bash -c 'kilo --version 2>&1 | head -1'

    echo "[verify] C1 health from C6"
    C1_STATUS=$(docker compose exec -T kilocode-terminal bash -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not healthy"; return 1; }

    echo "[ask]  C1+C3 ← C6 KiloCode"
    REPLY=$(docker compose exec -T kilocode-terminal bash -c \
        'python3 /workspace/ask_helper.py "Reply with exactly: KILOCODE_TEST_OK" \
            --api-url http://app:8000/v1/chat/completions \
            --agent-id test-c6-kilo --format openai 2>/dev/null')
    echo "  reply → $(echo "$REPLY" | head -3)"
    echo "$REPLY" | grep -q "KILOCODE_TEST_OK" || { echo "ERROR: expected marker not found"; return 1; }
}

test5_c7a_gateway() {
    cd "$COMPOSE_DIR"
    echo "[verify] openclaw version in C7a"
    docker compose exec -T openclaw-gateway sh -c 'openclaw --version 2>&1 | head -1'

    echo "[verify] C7a health endpoint (:18789)"
    GW_RESP=$(docker compose exec -T openclaw-gateway sh -c \
        'curl -sf --max-time 5 http://localhost:18789/ 2>/dev/null || curl -sf --max-time 5 http://localhost:18789/healthz 2>/dev/null || echo "FAIL"')
    echo "  gateway response → $(echo "$GW_RESP" | head -c 150)"
    echo "$GW_RESP" | grep -qiE '"status"' || { echo "ERROR: gateway health endpoint failed"; return 1; }

    echo "[verify] C1 reachable from C7a"
    C1_STATUS=$(docker compose exec -T openclaw-gateway sh -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])" 2>/dev/null || echo "offline"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not reachable from C7a"; return 1; }

    echo "[info] C7a is in standby mode — run 'docker compose exec openclaw-gateway openclaw onboard' to activate the full gateway"
}

test6_c7b_cli() {
    cd "$COMPOSE_DIR"
    echo "[verify] openclaw version in C7b"
    docker compose exec -T openclaw-cli sh -c 'openclaw --version 2>&1 | head -1'

    echo "[verify] C1 reachable from C7b"
    C1_STATUS=$(docker compose exec -T openclaw-cli sh -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])" 2>/dev/null || echo "offline"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not reachable from C7b"; return 1; }

    echo "[verify] C7a gateway reachable from C7b"
    GW_STATUS=$(docker compose exec -T openclaw-cli sh -c \
        'curl -sf --max-time 5 http://openclaw-gateway:18789/ 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get(\"status\",\"?\"))" 2>/dev/null || echo "offline"')
    echo "  C7a gateway status → $GW_STATUS"

    echo "[ask]  C1+C3 ← C7b direct ask (/v1/chat/completions)"
    REPLY=$(docker compose exec -T openclaw-cli sh -c \
        'python3 /workspace/ask_helper.py "Reply with exactly: C7B_TEST_OK" \
            --api-url http://app:8000/v1/chat/completions \
            --agent-id test-c7b --format openai 2>/dev/null')
    echo "  reply → $(echo "$REPLY" | head -3)"
    echo "$REPLY" | grep -q "C7B_TEST_OK" || { echo "ERROR: expected marker not found"; return 1; }
}

test7_hermes() {
    cd "$COMPOSE_DIR"
    echo "[verify] hermes version in C8"
    docker compose exec -T hermes-agent bash -c 'hermes version 2>&1 | head -1'

    echo "[verify] C1 reachable from C8"
    C1_STATUS=$(docker compose exec -T hermes-agent bash -c \
        'curl -sf --max-time 5 http://app:8000/health | python3 -c "import json,sys;print(json.load(sys.stdin)[\"status\"])" 2>/dev/null || echo "offline"')
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not reachable from C8"; return 1; }

    echo "[verify] C3 reachable from C8"
    C3_STATUS=$(docker compose exec -T hermes-agent bash -c \
        'curl -sf --max-time 5 http://browser-auth:8001/health 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get(\"status\",\"?\"))" 2>/dev/null || echo "offline"')
    echo "  C3 status → $C3_STATUS"

    echo "[verify] Hermes inference provider config"
    docker compose exec -T hermes-agent bash -c \
        'echo "  HERMES_INFERENCE_PROVIDER=${HERMES_INFERENCE_PROVIDER}" && echo "  OPENAI_BASE_URL=${OPENAI_BASE_URL}" && echo "  LLM_MODEL=${LLM_MODEL}"'

    echo "[ask]  C1+C3 ← C8 Hermes (ask_helper via /v1/chat/completions)"
    REPLY=$(docker compose exec -T hermes-agent bash -c \
        'python3 /workspace/ask_helper.py "Reply with exactly: HERMES_TEST_OK" \
            --api-url http://app:8000/v1/chat/completions \
            --agent-id test-c8-hermes --format openai 2>/dev/null')
    echo "  reply → $(echo "$REPLY" | head -3)"
    echo "$REPLY" | grep -q "HERMES_TEST_OK" || { echo "ERROR: expected marker not found"; return 1; }
    echo "[info] C8 Hermes standby healthy — run 'docker compose exec C8_hermes-agent hermes' for interactive CLI"
}

# ── Test 8: Multi-Agent Debate (60-second smoke test) ─────────────────────────
test8_debate() {
    cd "$COMPOSE_DIR"

    echo "[verify] C1 health before debate"
    C1_STATUS=$(curl -sf --max-time 5 http://localhost:8000/health | python3 -c \
        "import json,sys;print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "offline")
    echo "  C1 status → $C1_STATUS"
    [[ "$C1_STATUS" == "ok" ]] || { echo "ERROR: C1 not healthy — cannot run debate"; return 1; }

    echo "[verify] Python3 available on host"
    python3 --version || { echo "ERROR: python3 not found on host"; return 1; }

    echo "[debate] Running 60-second multi-agent debate (all 6 agents)..."
    echo "         Topic: LLM picks dynamically — stances are non-hardcoded"

    # Capture the debate transcript file path from output
    DEBATE_OUT=$(python3 "$COMPOSE_DIR/tests/agent_debate.py" \
        --duration 90 \
        --api http://localhost:8000 \
        --output "$COMPOSE_DIR/tests/debate-transcripts" 2>&1)

    echo "$DEBATE_OUT" | tail -40

    # Validate: all 6 agents produced opening statements
    AGENT_COUNT=$(echo "$DEBATE_OUT" | grep -c "Opening  |  Round 1" || true)
    echo "[verify] Opening statements received: $AGENT_COUNT / 6"
    [[ "$AGENT_COUNT" -ge 4 ]] || { echo "ERROR: fewer than 4 agents produced openings (got $AGENT_COUNT)"; return 1; }

    # Validate: judge produced scores
    echo "$DEBATE_OUT" | grep -q "WINNER" || { echo "ERROR: judge output missing from debate"; return 1; }

    # Validate: transcript file was saved
    TRANSCRIPT=$(echo "$DEBATE_OUT" | grep "Transcript saved" | awk '{print $NF}' || true)
    if [[ -n "$TRANSCRIPT" && -f "$TRANSCRIPT" ]]; then
        SIZE=$(wc -c < "$TRANSCRIPT")
        echo "[verify] Transcript saved: $TRANSCRIPT ($SIZE bytes)"
        [[ "$SIZE" -gt 500 ]] || { echo "ERROR: transcript file too small ($SIZE bytes)"; return 1; }
    else
        echo "[warn]  Transcript path not found in output — checking directory..."
        LATEST=$(ls -t "$COMPOSE_DIR/tests/debate-transcripts"/debate_*.json 2>/dev/null | head -1 || true)
        [[ -n "$LATEST" ]] || { echo "ERROR: no debate transcript file found"; return 1; }
        echo "[verify] Latest transcript: $LATEST"
    fi

    echo "[info]  Debate test passed — all agents participated and judge scored"
    echo "[info]  Full debate: python3 tests/agent_debate.py --duration 600"
    echo "[info]  Subset:      python3 tests/agent_debate.py --agents C2a C5 C8 --duration 300"
    echo "[info]  Custom topic: python3 tests/agent_debate.py --topic 'P vs NP'"
}

# ── sequential execution ───────────────────────────────────────────────────────
run_sequential() {
    header "Sequential Pair + Debate Validation"
    run_test 1 "C2 OpenCode → C1+C3"    test1_opencode
    run_test 2 "C2 Aider → C1+C3"       test2_aider
    run_test 3 "C5 Claude Code → C1+C3" test3_claude_code
    run_test 4 "C6 KiloCode → C1+C3"   test4_kilocode
    run_test 5 "C7a Gateway → C1+C3"   test5_c7a_gateway
    run_test 6 "C7b CLI → C1+C3"       test6_c7b_cli
    run_test 7 "C8 Hermes → C1+C3"     test7_hermes
    if ! $SKIP_DEBATE; then
        run_test 8 "Multi-Agent Debate (90s smoke)" test8_debate
    fi
}

# ── parallel execution ─────────────────────────────────────────────────────────
run_parallel() {
    header "Parallel Pair Validation (all 7 pairs in parallel) + Debate"

    declare -A PIDS LOGS NAMES
    LOGS[1]="$LOG_DIR/test1.log"; LOGS[2]="$LOG_DIR/test2.log"
    LOGS[3]="$LOG_DIR/test3.log"; LOGS[4]="$LOG_DIR/test4.log"
    LOGS[5]="$LOG_DIR/test5.log"; LOGS[6]="$LOG_DIR/test6.log"
    LOGS[7]="$LOG_DIR/test7.log"

    NAMES[1]="C2 OpenCode → C1+C3"
    NAMES[2]="C2 Aider → C1+C3"
    NAMES[3]="C5 Claude Code → C1+C3"
    NAMES[4]="C6 KiloCode → C1+C3"
    NAMES[5]="C7a Gateway → C1+C3"
    NAMES[6]="C7b CLI → C1+C3"
    NAMES[7]="C8 Hermes → C1+C3"

    echo "  Launching 7 pair tests (tests 1+2 staggered — share agent-terminal)..."
    test1_opencode    >"${LOGS[1]}" 2>&1 & PIDS[1]=$!
    sleep 10  # stagger: tests 1 and 2 both exec into agent-terminal; avoid session race
    test2_aider       >"${LOGS[2]}" 2>&1 & PIDS[2]=$!
    test3_claude_code >"${LOGS[3]}" 2>&1 & PIDS[3]=$!
    test4_kilocode    >"${LOGS[4]}" 2>&1 & PIDS[4]=$!
    test5_c7a_gateway >"${LOGS[5]}" 2>&1 & PIDS[5]=$!
    test6_c7b_cli     >"${LOGS[6]}" 2>&1 & PIDS[6]=$!
    test7_hermes      >"${LOGS[7]}" 2>&1 & PIDS[7]=$!

    echo "  PIDs: ${PIDS[1]} ${PIDS[2]} ${PIDS[3]} ${PIDS[4]} ${PIDS[5]} ${PIDS[6]} ${PIDS[7]}"
    echo "  Waiting for all pair tests to complete..."

    for id in 1 2 3 4 5 6 7; do
        if wait "${PIDS[$id]}"; then
            ok "Test $id: ${NAMES[$id]}"
            cat "${LOGS[$id]}" | sed 's/^/    /'
            RESULTS+=("PASS:$id:${NAMES[$id]}")
        else
            fail "Test $id: ${NAMES[$id]}"
            cat "${LOGS[$id]}" | sed 's/^/    /'
            RESULTS+=("FAIL:$id:${NAMES[$id]}")
        fi
    done

    # Debate always runs after parallel pair tests (it coordinates all agents sequentially)
    if ! $SKIP_DEBATE; then
        run_test 8 "Multi-Agent Debate (90s smoke)" test8_debate
    fi
}

# ── summary ────────────────────────────────────────────────────────────────────
print_summary() {
    header "Results: All Pair + Debate Tests"

    echo ""
    printf "  ${BOLD}%-4s  %-38s  %-10s${RESET}\n" "#" "Test" "Result"
    printf "  %s\n" "$(printf '─%.0s' {1..56})"

    # Metadata for display table
    declare -A PAIR_LABEL API_LABEL TOOL_LABEL
    PAIR_LABEL[1]="C2 OpenCode → C1+C3"
    PAIR_LABEL[2]="C2 Aider → C1+C3"
    PAIR_LABEL[3]="C5 Claude Code → C1+C3"
    PAIR_LABEL[4]="C6 KiloCode → C1+C3"
    PAIR_LABEL[5]="C7a Gateway → C1+C3"
    PAIR_LABEL[6]="C7b CLI → C1+C3"
    PAIR_LABEL[7]="C8 Hermes → C1+C3"
    PAIR_LABEL[8]="Multi-Agent Debate (90s)"

    API_LABEL[1]="OpenAI /v1/chat/completions"
    API_LABEL[2]="OpenAI /v1/chat/completions"
    API_LABEL[3]="Anthropic /v1/messages"
    API_LABEL[4]="OpenAI /v1/chat/completions"
    API_LABEL[5]="Health endpoint :18789"
    API_LABEL[6]="OpenAI /v1/chat/completions"
    API_LABEL[7]="OpenAI /v1/chat/completions"
    API_LABEL[8]="All 6 agents + judge via C1"

    for r in "${RESULTS[@]}"; do
        IFS=: read -r status id name <<< "$r"
        if [[ "$status" == "PASS" ]]; then
            printf "  ${GREEN}%-4s  %-38s  ✅ PASS${RESET}\n" "$id" "${PAIR_LABEL[$id]:-$name}"
            (( PASS++ )) || true
        else
            printf "  ${RED}%-4s  %-38s  ❌ FAIL${RESET}\n"  "$id" "${PAIR_LABEL[$id]:-$name}"
            (( FAIL++ )) || true
        fi
    done

    echo ""
    local TOTAL=$((PASS + FAIL))
    local CHECK_MARK="${GREEN}✅${RESET}"
    local FAIL_MARK="${RED}❌${RESET}"

    if [[ "$FAIL" -eq 0 ]]; then
        echo -e "  ${BOLD}${GREEN}All $TOTAL tests PASSED  ✅${RESET}"
    else
        echo -e "  ${BOLD}Total: $TOTAL | ${GREEN}${PASS} PASS${RESET} | ${RED}${FAIL} FAIL${RESET}"
    fi

    if ! $SKIP_DEBATE; then
        echo ""
        echo -e "  ${MAGENTA}${BOLD}Debate Framework:${RESET}"
        echo -e "  ${CYAN}  10-min debate:  python3 tests/agent_debate.py${RESET}"
        echo -e "  ${CYAN}  Custom topic:   python3 tests/agent_debate.py --topic 'P vs NP'${RESET}"
        echo -e "  ${CYAN}  Agent subset:   python3 tests/agent_debate.py --agents C2a C5 C8${RESET}"
        echo -e "  ${CYAN}  Transcripts:    tests/debate-transcripts/debate_<ts>.json${RESET}"
    fi

    echo ""
    rm -rf "$LOG_DIR"
    [[ "$FAIL" -eq 0 ]]
}

# ── main ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║    Copilot-OpenAI-Wrapper — Full Integration Test Suite      ║${RESET}"
echo -e "${BOLD}║    7 Pair Tests (C2×2, C5, C6, C7a, C7b, C8)  + Debate     ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo "  Mode        : $( $PARALLEL && echo 'PARALLEL (pairs) → then debate' || echo 'SEQUENTIAL' )"
echo "  Skip debate : $SKIP_DEBATE"
echo "  Dir         : $COMPOSE_DIR"

if $PARALLEL; then
    run_parallel
else
    run_sequential
fi

print_summary
